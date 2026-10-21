import os, re, sys, json, time, wave, shutil, base64, subprocess, datetime
import requests

def clean(v):
    v = v.strip()
    return v[3:] if v.lower().startswith("bot") else v

GEMINI_KEY = os.environ["GEMINI_KEY"].strip()
TG_TOKEN = clean(os.environ["TG_TOKEN"])
TG_CHAT = os.environ["TG_CHAT_ID"].strip()
CF_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
API = "https://generativelanguage.googleapis.com/v1beta"
COMMONS = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "YTDocuBot/1.0 (personal educational project)"}

TTS_VOICE = "Charon"       # also try: Orus, Kore, Fenrir
SUBTITLES = True
MUSIC = True               # halka ambient background sound
STYLE = ("photorealistic documentary photograph, natural light, cinematic, detailed, "
         "no text, no letters, no watermark")

W, H, FPS = 1280, 720, 24
BIG_W, BIG_H = 1920, 1080
CF_OK = bool(CF_ID and CF_TOKEN)
WORKING_MODEL = "gemini-flash-latest"
TTS_MODELS = None
TTS_OK = True
TTS_FAILS = 0
TTS_LAST = 0.0
USED = set()
LAST_PATHS = []
VIGNETTE = None
BAD_WORDS = ("logo", "icon", "flag of", "coat of arms", "stamp", "signature", "diagram",
             "screenshot", "banner", "poster", "wikimedia", "commons-", "template")


def ensure_ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    subprocess.run("sudo apt-get update -y && sudo apt-get install -y ffmpeg",
                   shell=True, check=True)


def ensure_fonts():
    try:
        out = subprocess.run(["fc-list"], capture_output=True, text=True).stdout
    except Exception:
        out = ""
    if "Devanagari" not in out:
        subprocess.run("sudo apt-get update -y && sudo apt-get install -y fonts-noto-core "
                       "&& sudo fc-cache -f", shell=True)
    try:
        out = subprocess.run(["fc-list"], capture_output=True, text=True).stdout
        print("Devanagari font available:", "Devanagari" in out)
    except Exception:
        pass


def ensure_pillow():
    try:
        import PIL  # noqa
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "pillow"], check=True)


def ensure_gtts():
    try:
        import gtts  # noqa
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "gTTS"], check=True)


ensure_pillow()
from PIL import Image, ImageEnhance  # noqa: E402

RES = getattr(Image, "Resampling", Image)
LANCZOS, BILINEAR = RES.LANCZOS, RES.BILINEAR


def pick_topic():
    lines = [l.strip() for l in open("topics.txt", encoding="utf-8") if l.strip()]
    return lines[datetime.date.today().toordinal() % len(lines)]


def candidate_models():
    names = ["gemini-flash-latest"]
    try:
        r = requests.get(f"{API}/models",
                         params={"key": GEMINI_KEY, "pageSize": 100}, timeout=60)
        if r.status_code == 200:
            found = []
            for m in r.json().get("models", []):
                n = m.get("name", "").replace("models/", "")
                ok = "generateContent" in m.get("supportedGenerationMethods", [])
                bad = any(x in n for x in ("image", "tts", "live", "audio", "lite",
                                           "exp", "8b", "thinking", "omni", "preview"))
                if "flash" in n and ok and not bad and n not in names:
                    found.append(n)
            names += sorted(set(found), reverse=True)
    except Exception as e:
        print("List models error:", str(e)[:100])
    names = names[:8]
    print("Models to try:", names)
    return names


def get_script(topic):
    global WORKING_MODEL
    prompt = f"""You are the narrator and writer of a gripping history/geography/science
documentary for YouTube, in the style of serious documentary channels.
Topic: {topic}
Write a 3-4 minute documentary script with exactly 14 scenes:
1. Scene 1 is a hook: a shocking fact or a mystery question that makes people keep watching.
2. Middle scenes tell it as a story with a clear sequence (cause and effect), concrete
   details, one surprising twist, and a curiosity line before every few scenes.
3. The last scene has a thought-provoking closing line and a short call to action:
   ask viewers to comment their answer and subscribe.
Narration: Hindi in Devanagari, calm authoritative documentary narrator tone, short
sentences, simple everyday words (avoid heavy Sanskrit words), write years and numbers
in words, write English terms in Devanagari, no emojis or symbols. Use only
well-established facts; avoid uncertain numbers and dates; never invent quotes.
Each scene narration is 2-3 sentences (about 30-40 words).
For each scene also give:
- search_queries: a list of exactly 3 English search queries (2-5 words each) to find real
  photos, paintings or maps on Wikimedia Commons. Name concrete real things (a specific
  place, monument, artefact, painting, river, person). First query most specific, third
  more general. No abstract words.
- image_prompt: English, a detailed description of a photorealistic documentary photograph
  for that scene (used only if no real photo is found). No text in the image.
Also give:
- title: catchy YouTube title in Hinglish, max 70 characters.
- screen_title: a short title in Devanagari, max 5 words, for the opening title card.
- description: 2-3 lines plus 5 hashtags.
Return ONLY JSON:
{{"title":"...","screen_title":"...","description":"...","scenes":[{{"narration":"...","search_queries":["...","...","..."],"image_prompt":"..."}}]}}"""
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    for model in candidate_models():
        for attempt in range(2):
            try:
                r = requests.post(f"{API}/models/{model}:generateContent",
                                  params={"key": GEMINI_KEY}, json=body, timeout=120)
                if r.status_code == 200:
                    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    text = re.sub(r"^```(?:json)?|```$", "", text.strip(),
                                  flags=re.M).strip()
                    data = json.loads(text)
                    if data.get("scenes"):
                        print("Model used:", model)
                        WORKING_MODEL = model
                        return data
                    break
                print(model, r.status_code, r.text[:120])
                if r.status_code == 503 and attempt == 0:
                    time.sleep(5)
                    continue
                break
            except Exception as e:
                print(model, "error:", str(e)[:120])
                break
    raise RuntimeError("Script not generated: all Gemini models failed")


# ---------------- voice ----------------

def tts_models():
    global TTS_MODELS
    if TTS_MODELS is not None:
        return TTS_MODELS
    names = []
    try:
        r = requests.get(f"{API}/models",
                         params={"key": GEMINI_KEY, "pageSize": 200}, timeout=60)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                n = m.get("name", "").replace("models/", "")
                if "tts" in n and "pro" not in n and \
                        "generateContent" in m.get("supportedGenerationMethods", []):
                    names.append(n)
    except Exception as e:
        print("TTS list error:", str(e)[:100])

    def rank(n):
        return (0 if "3.1" in n else 1 if "2.5" in n else 2, n)

    names = sorted(set(names), key=rank)
    for d in ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"):
        if d not in names:
            names.append(d)
    TTS_MODELS = names[:4]
    print("TTS models:", TTS_MODELS)
    return TTS_MODELS


def gemini_tts(text, path):
    global TTS_LAST
    for model in tts_models():
        prefix = "" if "3.8" in model else (
            "Read this in Hindi like a calm, authoritative documentary narrator, warm and "
            "expressive, with natural pauses: ")
        body = {"contents": [{"parts": [{"text": prefix + text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": TTS_VOICE}}}}}
        for attempt in range(3):
            wait = 32 - (time.time() - TTS_LAST)
            if wait > 0:
                time.sleep(wait)
            TTS_LAST = time.time()
            try:
                r = requests.post(f"{API}/models/{model}:generateContent",
                                  params={"key": GEMINI_KEY}, json=body, timeout=180)
            except Exception as e:
                print("TTS request error:", str(e)[:100])
                continue
            if r.status_code == 200:
                try:
                    part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                    pcm = base64.b64decode(part["data"])
                    m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
                    rate = int(m.group(1)) if m else 24000
                    with wave.open(path, "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(rate)
                        w.writeframes(pcm)
                    if os.path.getsize(path) > 5000:
                        return True
                except Exception as e:
                    print("TTS parse error:", str(e)[:100])
                continue
            print("TTS", model, r.status_code, r.text[:150])
            if r.status_code in (400, 404):
                break
    return False


def make_voice(text, i):
    global TTS_OK, TTS_FAILS
    if TTS_OK:
        path = f"s{i}.wav"
        if gemini_tts(text, path):
            TTS_FAILS = 0
            print("Voice: Gemini TTS")
            return path
        TTS_FAILS += 1
        if TTS_FAILS >= 2:
            TTS_OK = False
            print("Gemini TTS disabled, using gTTS for the rest")
    ensure_gtts()
    from gtts import gTTS
    path = f"s{i}.mp3"
    for _ in range(4):
        try:
            gTTS(text=text, lang="hi").save(path)
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                print("Voice: gTTS")
                return path
        except Exception as e:
            print("gTTS error:", str(e)[:100])
        time.sleep(5)
    raise RuntimeError("Voice generation failed")


# ---------------- images (Wikimedia Commons) ----------------

def commons_candidates(query):
    params = {"action": "query", "format": "json", "generator": "search",
              "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 15,
              "prop": "imageinfo", "iiprop": "url|mime|size|extmetadata",
              "iiurlwidth": 1920}
    r = requests.get(COMMONS, params=params, headers=UA, timeout=40)
    pages = r.json().get("query", {}).get("pages", {})
    out = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        title = p.get("title", "")
        url = info.get("thumburl") or info.get("url")
        w, h = info.get("width", 0), info.get("height", 0)
        if not url or url in USED:
            continue
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if w < 1200 or h < 700 or w < h * 1.1:
            continue
        if any(b in title.lower() for b in BAD_WORDS):
            continue
        meta = info.get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        credit = f"{title.replace('File:', '')[:70]} | {artist[:50]} | {lic}"
        out.append((url, "png" if info.get("mime") == "image/png" else "jpg", credit))
    return out


def cf_image(prompt, path):
    global CF_OK
    url = (f"https://api.cloudflare.com/client/v4/accounts/{CF_ID}"
           "/ai/run/@cf/black-forest-labs/flux-1-schnell")
    for _ in range(3):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {CF_TOKEN}"},
                              json={"prompt": prompt[:1500], "steps": 6}, timeout=120)
            if r.status_code == 200:
                b64 = (r.json().get("result") or {}).get("image")
                if b64:
                    open(path, "wb").write(base64.b64decode(b64))
                    return True
            print("Image status:", r.status_code, r.text[:200])
            if r.status_code in (401, 403):
                CF_OK = False
                return False
        except Exception as e:
            print("Image error:", str(e)[:100])
        time.sleep(6)
    return False


def get_images(scene, i, need):
    found, credits = [], []
    queries = scene.get("search_queries") or []
    if isinstance(queries, str):
        queries = [queries]
    for q in queries[:3]:
        if len(found) >= need:
            break
        try:
            cands = commons_candidates(q)
        except Exception as e:
            print("Commons error:", str(e)[:100])
            continue
        for url, ext, credit in cands[:5]:
            try:
                r = requests.get(url, headers=UA, timeout=60)
            except Exception as e:
                print("Download error:", str(e)[:80])
                continue
            if r.status_code == 200 and len(r.content) > 20000:
                path = f"s{i}_{len(found)}.{ext}"
                open(path, "wb").write(r.content)
                USED.add(url)
                found.append(path)
                credits.append(credit)
                print("Commons image ok:", q)
                break
        time.sleep(0.7)
    while len(found) < need and CF_OK:
        path = f"s{i}_{len(found)}.jpg"
        if cf_image(f"{scene.get('image_prompt', '')}. {STYLE}", path):
            found.append(path)
            credits.append("AI generated (Cloudflare FLUX)")
        else:
            break
    if not found and LAST_PATHS:
        print("No new image, reusing previous one for scene", i + 1)
        found = [LAST_PATHS[-1]]
        credits = [""]
    if not found:
        print("Using plain background for scene", i + 1)
        path = f"s{i}_0.png"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"color=c=0x1a1a1a:s={BIG_W}x{BIG_H}", "-frames:v", "1", path],
                       check=True, capture_output=True)
        found, credits = [path], [""]
    LAST_PATHS.extend(found)
    return found, credits


# ---------------- video ----------------

def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(out.stdout.strip())


def run_ffmpeg(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise RuntimeError("ffmpeg failed")


def get_vignette():
    global VIGNETTE
    if VIGNETTE is None:
        g = Image.radial_gradient("L").resize((W, H), BILINEAR)
        VIGNETTE = g.point(lambda v: int(max(0, v - 100) * 0.8))
    return VIGNETTE


def prep_image(path):
    img = Image.open(path).convert("RGB")
    w, h = img.size
    s = max(BIG_W / w, BIG_H / h)
    img = img.resize((int(w * s) + 1, int(h * s) + 1), LANCZOS)
    x, y = (img.width - BIG_W) // 2, (img.height - BIG_H) // 2
    img = img.crop((x, y, x + BIG_W, y + BIG_H))
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(0.93)
    return img


def shot_frame(big, kind, p):
    if kind == 0:
        z, shift = 1.0 + 0.2 * p, 0.0
    elif kind == 1:
        z, shift = 1.2 - 0.2 * p, 0.0
    elif kind == 2:
        z, shift = 1.18, -0.9 + 1.8 * p
    else:
        z, shift = 1.18, 0.9 - 1.8 * p
    w, h = int(BIG_W / z), int(BIG_H / z)
    x0 = int((BIG_W - w) / 2 + shift * (BIG_W - w) / 2)
    y0 = (BIG_H - h) // 2
    return big.crop((x0, y0, x0 + w, y0 + h)).resize((W, H), BILINEAR)


def render_scene(i, paths, aud, raw, dur):
    bigs = [prep_image(p) for p in paths]
    m = len(bigs)
    kinds = [(i * 2 + s) % 4 for s in range(m)]
    n = max(2, int(dur * FPS))
    seg = n / m
    dis = int(0.6 * FPS)
    vig = get_vignette()
    fade = f"fade=t=in:st=0:d=0.2,fade=t=out:st={max(0.0, dur - 0.3):.2f}:d=0.3"
    log = open("ffmpeg.log", "ab")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", aud,
           "-vf", fade, "-af", "apad", "-t", f"{dur:.2f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "44100", "-ac", "2", raw]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=log)
    for k in range(n):
        s = min(int(k / seg), m - 1)
        p = min(1.0, (k - s * seg) / seg)
        frame = shot_frame(bigs[s], kinds[s], p)
        if s < m - 1 and k >= (s + 1) * seg - dis:
            a = (k - ((s + 1) * seg - dis)) / dis
            nxt = shot_frame(bigs[s + 1], kinds[s + 1], 0.0)
            frame = Image.blend(frame, nxt, min(1.0, max(0.0, a)))
        frame.paste((0, 0, 0), (0, 0, W, H), vig)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()
    log.close()
    if proc.returncode != 0:
        print(open("ffmpeg.log", "rb").read()[-1500:])
        raise RuntimeError("scene render failed")


def ass_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_ass(text, dur, path, title=None):
    words = re.sub(r"[{}\\]", "", text).split()
    chunks, i = [], 0
    while i < len(words):
        left = len(words) - i
        n = 6
        if left <= 8:
            n = max(1, (left + 1) // 2) if left > 6 else left
        chunks.append(" ".join(words[i:i + n]))
        i += n
    total = sum(len(c) for c in chunks) or 1
    start, avail = 0.15, max(0.5, dur - 0.5)
    lines = []
    t = start
    for c in chunks:
        d = avail * len(c) / total
        lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Cap,,0,0,0,,{c}")
        t += d
    if title:
        tt = re.sub(r"[{}\\]", "", title)
        lines.append("Dialogue: 1,0:00:00.40,0:00:04.60,Title,,0,0,0,,"
                     "{\\fad(600,700)}" + tt)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Noto Sans Devanagari,44,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,3,1,2,60,60,38,1
Style: Title,Noto Sans Devanagari,78,&H0037AFD4,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,2,5,60,60,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")


def burn_subs(raw, ass, out):
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
                        "-vf", f"subtitles={ass}", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "copy", out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("Subtitles failed, using video without subtitles:", r.stderr[-300:])
        shutil.copy(raw, out)


def build_scene(i, scene, title, all_credits):
    raw, ass, vid = f"r{i}.mp4", f"s{i}.ass", f"s{i}.mp4"
    aud = make_voice(scene["narration"], i)
    dur = duration(aud) + 0.4
    need = 3 if dur > 13 else 2 if dur > 7 else 1
    paths, credits = get_images(scene, i, need)
    all_credits.extend([c for c in credits if c])
    render_scene(i, paths, aud, raw, dur)
    if SUBTITLES:
        make_ass(scene["narration"], dur, ass, title if i == 0 else None)
        burn_subs(raw, ass, vid)
    else:
        shutil.copy(raw, vid)
    print("Scene done:", i + 1, "| shots:", len(paths))
    return vid


def make_music(total, path):
    T = f"{total:.1f}"
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"sine=f=110:d={T}",
           "-f", "lavfi", "-i", f"sine=f=164.81:d={T}",
           "-f", "lavfi", "-i", f"sine=f=220:d={T}",
           "-f", "lavfi", "-i", f"sine=f=329.63:d={T}",
           "-filter_complex",
           "[0][1][2][3]amix=inputs=4:normalize=0,volume=0.35,tremolo=f=0.2:d=0.6,"
           "aecho=0.8:0.9:700:0.3,lowpass=f=900,afade=t=in:d=3,"
           f"afade=t=out:st={max(0.0, total - 4):.1f}:d=4[m]",
           "-map", "[m]", "-c:a", "aac", path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0


def mix_music(video, music, out):
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", music,
                        "-filter_complex",
                        "[1:a]volume=0.5[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]",
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
                        "-ar", "44100", "-ac", "2", out], capture_output=True, text=True)
    if r.returncode != 0:
        print("Music mix failed:", r.stderr[-300:])
        shutil.copy(video, out)


def shrink_if_needed(path):
    if os.path.getsize(path) <= 48 * 1024 * 1024:
        return path
    print("Video too big, shrinking...")
    run_ffmpeg(["ffmpeg", "-y", "-i", path, "-vf", "scale=854:480",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                "-c:a", "aac", "small.mp4"])
    return "small.mp4"


def send_telegram(video, text, credits_text):
    base = f"https://api.telegram.org/bot{TG_TOKEN}"
    r1 = requests.post(f"{base}/sendMessage",
                       data={"chat_id": TG_CHAT, "text": text[:4000]}, timeout=60)
    print("Message status:", r1.status_code, r1.text[:150])
    with open(video, "rb") as f:
        r2 = requests.post(f"{base}/sendVideo",
                           data={"chat_id": TG_CHAT, "supports_streaming": "true"},
                           files={"video": f}, timeout=900)
    print("Video status:", r2.status_code, r2.text[:150])
    if r2.status_code != 200:
        raise RuntimeError("Telegram video send failed")
    if credits_text:
        requests.post(f"{base}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": credits_text[:4000]}, timeout=60)


def main():
    ensure_ffmpeg()
    ensure_fonts()
    print("Cloudflare FLUX fallback enabled:", CF_OK)
    topic = pick_topic()
    print("Topic:", topic)
    data = get_script(topic)
    title = data.get("screen_title") or data.get("title", topic)
    credits = []
    clips = [build_scene(i, s, title, credits) for i, s in enumerate(data["scenes"])]
    with open("list.txt", "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
                "-c", "copy", "base.mp4"])
    final = "base.mp4"
    if MUSIC:
        total = duration("base.mp4")
        if make_music(total, "music.m4a"):
            mix_music("base.mp4", "music.m4a", "final.mp4")
            final = "final.mp4"
    shutil.copy(final, "final_out.mp4")
    final = shrink_if_needed("final_out.mp4")
    text = f"Title: {data.get('title', topic)}\n\nDescription:\n{data.get('description', '')}"
    credits_text = ""
    if credits:
        credits_text = ("Image credits (paste in YouTube description):\n"
                        + "\n".join(f"- {c}" for c in credits))
    send_telegram(final, text, credits_text)


main()
