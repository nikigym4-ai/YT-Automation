import os, re, sys, json, time, shutil, asyncio, subprocess, datetime
import requests, edge_tts

def clean(v):
    v = v.strip()
    return v[3:] if v.lower().startswith("bot") else v

GEMINI_KEY = os.environ["GEMINI_KEY"].strip()
TG_TOKEN = clean(os.environ["TG_TOKEN"])
TG_CHAT = os.environ["TG_CHAT_ID"].strip()
VOICE = "hi-IN-MadhurNeural"
API = "https://generativelanguage.googleapis.com/v1beta"
USE_EDGE = True
LAST_IMAGE = None
WORKING_MODEL = "gemini-flash-latest"


def ensure_ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    subprocess.run("sudo apt-get update -y && sudo apt-get install -y ffmpeg",
                   shell=True, check=True)


def ensure_gtts():
    try:
        import gtts  # noqa
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "gTTS"], check=True)


def ensure_cairosvg():
    try:
        import cairosvg  # noqa
    except Exception:
        subprocess.run("sudo apt-get install -y libcairo2", shell=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "cairosvg"], check=True)


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
    prompt = f"""Topic: {topic}
Make a 3-4 minute educational explainer video script.
Narration: Hindi in Devanagari script, simple and conversational (Hinglish feel);
write English terms in Devanagari too. Use only well-established facts; avoid
uncertain numbers and dates. Exactly 12 scenes, each narration 2-3 sentences.
character: one short English description of a simple cartoon character who appears
in many scenes (example: "a boy with black hair, red shirt, round head").
palette: 4 colors for the whole video in plain words (example: "warm sand, teal, orange, dark brown").
image_prompt: English, one clear simple cartoon scene for that narration: setting,
what the character does, 2-3 key objects. No text in the image.
Return ONLY JSON:
{{"title":"...","description":"...","character":"...","palette":"...","scenes":[{{"narration":"...","image_prompt":"..."}}]}}"""
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


def make_voice(text, path):
    global USE_EDGE
    if USE_EDGE:
        try:
            asyncio.run(edge_tts.Communicate(text, VOICE).save(path))
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                return
        except Exception as e:
            print("Edge voice failed, switching to gTTS:", str(e)[:100])
        USE_EDGE = False
    ensure_gtts()
    from gtts import gTTS
    for _ in range(4):
        try:
            gTTS(text=text, lang="hi").save(path)
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                return
        except Exception as e:
            print("gTTS error:", str(e)[:100])
        time.sleep(5)
    raise RuntimeError("Voice generation failed")


def get_svg(scene, character, palette):
    prompt = f"""Draw a 2D flat vector cartoon illustration as SVG for this scene:
{scene['image_prompt']}
Main character: {character}. Color palette: {palette}.
Rules:
- Start with <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"> and end with </svg>.
- Fill the whole canvas with a background: sky or wall color, plus ground or floor.
- Use only rect, circle, ellipse, polygon, polyline, line, path, g, linearGradient.
- Build depth in layers: background, middle objects, foreground character.
- Characters are simple: round head, dot eyes, rectangle or rounded body, limbs as thick lines.
- Make the main subject large and in the center. Bold flat colors.
- NO text, NO letters, NO <image>, NO external links, NO filters, NO animation tags.
Return ONLY the SVG code, nothing else."""
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    for model in [WORKING_MODEL, "gemini-flash-latest"]:
        try:
            r = requests.post(f"{API}/models/{model}:generateContent",
                              params={"key": GEMINI_KEY}, json=body, timeout=150)
            if r.status_code != 200:
                print("SVG status:", model, r.status_code)
                time.sleep(5)
                continue
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            m = re.search(r"<svg.*</svg>", text, flags=re.S)
            if not m:
                continue
            svg = m.group(0)
            if re.search(r"<script|<image|<foreignObject|href=\"http|<text", svg):
                print("SVG rejected (unsafe or text)")
                continue
            head = svg[:300]
            if "xmlns=" not in head:
                svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
            if "viewBox" not in head:
                svg = svg.replace("<svg", '<svg viewBox="0 0 1280 720"', 1)
            return svg
        except Exception as e:
            print("SVG error:", str(e)[:100])
    return None


def render_svg(svg, path):
    try:
        import cairosvg
        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=path,
                         output_width=1920, output_height=1080)
        return os.path.exists(path) and os.path.getsize(path) > 3000
    except Exception as e:
        print("Render error:", str(e)[:150])
        return False


def make_image(scene, character, palette, i):
    global LAST_IMAGE
    path = f"s{i}.png"
    for attempt in range(3):
        svg = get_svg(scene, character, palette)
        if svg and render_svg(svg, path):
            LAST_IMAGE = path
            print("Drawing ok for scene", i + 1)
            return path
        time.sleep(4)
    if LAST_IMAGE:
        print("Reusing previous drawing for scene", i + 1)
        shutil.copy(LAST_IMAGE, path)
        return path
    print("Using plain background for scene", i + 1)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x2b3a67:s=1920x1080",
                    "-frames:v", "1", path], check=True, capture_output=True)
    return path


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(out.stdout.strip())


def run_ffmpeg(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise RuntimeError("ffmpeg failed")


def motion(i, frames):
    cy = "y='ih/2-(ih/zoom/2)'"
    kind = i % 4
    if kind == 0:
        return f"zoompan=z='min(zoom+0.0008,1.25)':d={frames}:x='iw/2-(iw/zoom/2)':{cy}"
    if kind == 1:
        return (f"zoompan=z='if(eq(on,0),1.25,max(zoom-0.0008,1.0))':d={frames}"
                f":x='iw/2-(iw/zoom/2)':{cy}")
    if kind == 2:
        return f"zoompan=z=1.2:d={frames}:x='(iw-iw/zoom)*on/{frames}':{cy}"
    return f"zoompan=z=1.2:d={frames}:x='(iw-iw/zoom)*(1-on/{frames})':{cy}"


def build_scene(i, scene, character, palette):
    aud, vid = f"s{i}.mp3", f"s{i}.mp4"
    make_voice(scene["narration"], aud)
    img = make_image(scene, character, palette, i)
    dur = duration(aud) + 0.4
    frames = int(dur * 25) + 10
    vf = f"scale=1920:1080,{motion(i, frames)}:s=1280x720:fps=25,format=yuv420p"
    run_ffmpeg(["ffmpeg", "-y", "-i", img, "-i", aud, "-vf", vf, "-t", f"{dur:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", vid])
    print("Scene done:", i + 1)
    time.sleep(3)
    return vid


def shrink_if_needed(path):
    if os.path.getsize(path) <= 48 * 1024 * 1024:
        return path
    print("Video too big, shrinking...")
    run_ffmpeg(["ffmpeg", "-y", "-i", path, "-vf", "scale=854:480",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                "-c:a", "aac", "small.mp4"])
    return "small.mp4"


def send_telegram(video, text):
    base = f"https://api.telegram.org/bot{TG_TOKEN}"
    r1 = requests.post(f"{base}/sendMessage",
                       data={"chat_id": TG_CHAT, "text": text[:4000]}, timeout=60)
    print("Message status:", r1.status_code, r1.text[:150])
    with open(video, "rb") as f:
        r2 = requests.post(f"{base}/sendVideo", data={"chat_id": TG_CHAT},
                           files={"video": f}, timeout=900)
    print("Video status:", r2.status_code, r2.text[:150])
    if r2.status_code != 200:
        raise RuntimeError("Telegram video send failed")


def main():
    ensure_ffmpeg()
    ensure_cairosvg()
    topic = pick_topic()
    print("Topic:", topic)
    data = get_script(topic)
    character = data.get("character", "a friendly cartoon guide with a round head")
    palette = data.get("palette", "warm sand, teal, orange, dark brown")
    clips = [build_scene(i, s, character, palette)
             for i, s in enumerate(data["scenes"])]
    with open("list.txt", "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
                "-c", "copy", "final.mp4"])
    final = shrink_if_needed("final.mp4")
    text = f"Title: {data.get('title', topic)}\n\nDescription:\n{data.get('description', '')}"
    send_telegram(final, text)


main()
