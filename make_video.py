import os, re, sys, json, time, shutil, asyncio, subprocess, datetime, urllib.parse
import requests, edge_tts

GEMINI_KEY = os.environ["GEMINI_KEY"]
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT = os.environ["TG_CHAT_ID"]
VOICE = "hi-IN-MadhurNeural"
STYLE = "flat 2D illustration, warm earthy colors, simple shapes, no text"
API = "https://generativelanguage.googleapis.com/v1beta"
USE_EDGE = True


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
        else:
            print("List models status:", r.status_code)
    except Exception as e:
        print("List models error:", e)
    names = names[:8]
    print("Models to try:", names)
    return names


def get_script(topic):
    prompt = f"""Topic: {topic}
Make a 3-4 minute educational explainer video script.
Narration: Hindi in Devanagari script, simple and conversational (Hinglish feel);
write English terms in Devanagari too. Use only well-established facts; avoid
uncertain numbers and dates. Exactly 12 scenes, each narration 2-3 sentences.
image_prompt: English, describes the visual for that scene.
Return ONLY JSON:
{{"title":"...","description":"...","scenes":[{{"narration":"...","image_prompt":"..."}}]}}"""
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


def make_image(prompt, path, seed):
    q = urllib.parse.quote(f"{prompt}, {STYLE}")
    url = (f"https://image.pollinations.ai/prompt/{q}"
           f"?width=1280&height=720&nologo=true&seed={seed}")
    for _ in range(4):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and len(r.content) > 5000:
                open(path, "wb").write(r.content)
                return
            print("Image status:", r.status_code)
        except Exception as e:
            print("Image error:", str(e)[:100])
        time.sleep(8)
    print("Using plain background for", path)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x3b2f2f:s=1280x720",
                    "-frames:v", "1", path], check=True, capture_output=True)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(out.stdout.strip())


def run_ffmpeg(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise RuntimeError("ffmpeg failed")


def build_scene(i, scene):
    img, aud, vid = f"s{i}.jpg", f"s{i}.mp3", f"s{i}.mp4"
    make_voice(scene["narration"], aud)
    make_image(scene["image_prompt"], img, seed=i + 1)
    dur = duration(aud) + 0.4
    frames = int(dur * 25) + 10
    vf = (f"scale=1920:1080,zoompan=z='min(zoom+0.0007,1.2)':d={frames}"
          f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps=25,format=yuv420p")
    run_ffmpeg(["ffmpeg", "-y", "-i", img, "-i", aud, "-vf", vf, "-t", f"{dur:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", vid])
    print("Scene done:", i + 1)
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
    topic = pick_topic()
    print("Topic:", topic)
    data = get_script(topic)
    clips = [build_scene(i, s) for i, s in enumerate(data["scenes"])]
    with open("list.txt", "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
                "-c", "copy", "final.mp4"])
    final = shrink_if_needed("final.mp4")
    text = f"Title: {data.get('title', topic)}\n\nDescription:\n{data.get('description', '')}"
    send_telegram(final, text)


main()
