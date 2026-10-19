import os, json, time, asyncio, subprocess, datetime, urllib.parse
import requests, edge_tts

GEMINI_KEY = os.environ["GEMINI_KEY"]
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT = os.environ["TG_CHAT_ID"]
VOICE = "hi-IN-MadhurNeural"
STYLE = "flat 2D illustration, warm earthy colors, simple shapes, no text"

def pick_topic():
    lines = [l.strip() for l in open("topics.txt", encoding="utf-8") if l.strip()]
    return lines[datetime.date.today().toordinal() % len(lines)]

def get_script(topic):
    prompt = f"""Topic: {topic}
Make a 3-4 minute educational explainer video script.
Narration: Hindi in Devanagari script, simple and conversational (Hinglish feel);
write English terms in Devanagari too. Use only well-established facts; avoid
uncertain numbers and dates. Exactly 12 scenes, each narration 2-3 sentences.
image_prompt: English, describes the visual for that scene.
Return ONLY JSON:
{{"title":"...","description":"...","scenes":[{{"narration":"...","image_prompt":"..."}}]}}"""
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash:generateContent?key={GEMINI_KEY}")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    r = requests.post(url, json=body, timeout=180)
    r.raise_for_status()
    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])

def make_image(prompt, path, seed):
    q = urllib.parse.quote(f"{prompt}, {STYLE}")
    url = f"https://image.pollinations.ai/prompt/{q}?width=1280&height=720&nologo=true&seed={seed}"
    for _ in range(4):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and len(r.content) > 5000:
                open(path, "wb").write(r.content)
                return
        except Exception:
            pass
        time.sleep(8)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x3b2f2f:s=1280x720",
                    "-frames:v", "1", path], check=True, capture_output=True)

def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(out.stdout.strip())

def build_scene(i, scene):
    img, aud, vid = f"s{i}.jpg", f"s{i}.mp3", f"s{i}.mp4"
    asyncio.run(edge_tts.Communicate(scene["narration"], VOICE).save(aud))
    make_image(scene["image_prompt"], img, seed=i + 1)
    dur = duration(aud) + 0.4
    frames = int(dur * 25) + 10
    vf = (f"scale=1920:1080,zoompan=z='min(zoom+0.0007,1.2)':d={frames}"
          f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps=25,format=yuv420p")
    subprocess.run(["ffmpeg", "-y", "-i", img, "-i", aud, "-vf", vf, "-t", str(dur),
                    "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                    "-ar", "44100", "-ac", "2", vid], check=True, capture_output=True)
    return vid

def send_telegram(video, text):
    base = f"https://api.telegram.org/bot{TG_TOKEN}"
    requests.post(f"{base}/sendMessage",
                  data={"chat_id": TG_CHAT, "text": text[:4000]}, timeout=60)
    with open(video, "rb") as f:
        r = requests.post(f"{base}/sendVideo", data={"chat_id": TG_CHAT},
                          files={"video": f}, timeout=600)
    print(r.status_code, r.text[:200])

def main():
    topic = pick_topic()
    print("Topic:", topic)
    data = get_script(topic)
    clips = [build_scene(i, s) for i, s in enumerate(data["scenes"])]
    with open("list.txt", "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
                    "-c", "copy", "final.mp4"], check=True, capture_output=True)
    text = f"Title: {data['title']}\n\nDescription:\n{data['description']}"
    send_telegram("final.mp4", text)

main()
