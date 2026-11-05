"""Лирик-видео: готовая песня + иллюстрации под каждую новость + слова на экране по меткам времени.

Запуск:
  python3 lyric_video.py news.json lyrics.json song.mp3 song_timestamps.json out/reel_lyric.mp4

Иллюстрации: тёмная стилизованная графика (gpt-image-2), без лиц, людей, надписей и сцен насилия.
На видео стоит пометка, что иллюстрации созданы ИИ.
"""
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import align
from paraphrase import call_openai

W, H, FPS = 1080, 1920, 30
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "gpt-image-2")
SCENE_MODEL = os.environ.get("SCENE_MODEL", "gpt-4.1")
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
STYLE = ("Dark stylized editorial illustration, graphic novel noir, limited palette of deep blue and black "
         "with one red accent, vertical composition, no text, no letters, no numbers, no logos, no faces, "
         "no real people, no violence shown, no weapons, not photorealistic. Scene: ")

SCENE_RULES = (
    "Для каждой новости придумай ОДНУ символическую сцену для тёмной иллюстрации, на английском, 1-2 предложения. "
    "Правила: без лиц и без конкретных людей (силуэты вдали допустимы); без текста, надписей и логотипов; "
    "не показывай насилие, кровь, оружие, тела и жертв: для трагедий, атак и смертей используй сдержанную метафору "
    "(пустая ночная дорога, свеча, тёмное небо, дождь за окном); не изображай реальных политиков и их символику; "
    "сцена должна ассоциироваться с темой новости, но не выглядеть как реальный кадр события. "
    'Верни только JSON-массив строк той же длины и в том же порядке: ["scene 1", ...]'
)


def make_scenes(items):
    src = [{"title": it["title"], "summary": it.get("summary", "")} for it in items]
    for _ in range(3):
        txt = call_openai(SCENE_RULES + "\n\nНовости:\n" + json.dumps(src, ensure_ascii=False), SCENE_MODEL)
        try:
            arr = json.loads(txt[txt.find("["):txt.rfind("]") + 1])
            if len(arr) == len(items) and all(isinstance(x, str) and x.strip() for x in arr):
                return arr
        except json.JSONDecodeError:
            pass
    raise RuntimeError("не удалось получить описания сцен")


def gen_image(scene, path):
    import base64
    body = json.dumps({"model": IMAGE_MODEL, "prompt": STYLE + scene, "size": "1024x1536",
                       "quality": "low", "n": 1}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("OPENAI_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["OPENAI_API_KEY"]
    req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=150) as r:
        d = json.loads(r.read())
    raw = Path(path).with_suffix(".raw.png")
    raw.write_bytes(base64.b64decode(d["data"][0]["b64_json"]))
    im = Image.open(raw).convert("RGB")
    im = im.resize((int(im.width * H / im.height), H), Image.LANCZOS)
    left = (im.width - W) // 2
    im.crop((left, 0, left + W, H)).save(path)


def make_images(scenes, work):
    paths = [work / f"scene_{i:02d}.png" for i in range(len(scenes))]

    def one(i):
        for attempt in range(3):
            try:
                if not paths[i].exists():
                    gen_image(scenes[i], paths[i])
                return
            except Exception as e:
                if attempt == 2:
                    raise RuntimeError(f"картинка {i}: {e}")

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one, range(len(scenes))))
    return paths


def wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def line_overlay(text, path):
    """Прозрачная плашка со строкой песни внизу кадра."""
    img = Image.new("RGBA", (W, 420), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_BOLD, 64)
    rows = wrap(d, text, font, W - 160)[:3]
    h = 60 + 84 * len(rows)
    top = (420 - h) // 2
    d.rounded_rectangle((40, top, W - 40, top + h), radius=36, fill=(0, 0, 0, 150))
    y = top + 30
    for r in rows:
        w = d.textlength(r, font=font)
        d.text(((W - w) / 2, y), r, font=font, fill=(255, 255, 255, 255))
        y += 84
    img.save(path)


def label_overlay(date_text, path):
    img = Image.new("RGBA", (W, 130), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT_REG, 36)
    d.text((50, 50), f"НОВОСТИ ДНЯ  {date_text}   ·   иллюстрации созданы ИИ", font=f, fill=(255, 255, 255, 220))
    img.save(path)


def build(news_path, lyrics_path, song_path, ts_path, out_path):
    news = json.loads(Path(news_path).read_text(encoding="utf-8"))
    lyr = json.loads(Path(lyrics_path).read_text(encoding="utf-8"))
    aligned = json.loads(Path(ts_path).read_text(encoding="utf-8"))["data"]["alignedWords"]
    work = Path(out_path).parent / "lyric_work"
    work.mkdir(parents=True, exist_ok=True)

    groups = align.group_times(lyr, aligned)          # вступление, новости..., концовка
    lines = align.line_times(lyr, aligned)
    t0 = max(0.0, groups[0][0] - 1.5)                 # песня начинается чуть раньше первых слов
    t_end = groups[-1][1] + 3.0
    total = t_end - t0

    scenes_file = work / "scenes.json"
    if scenes_file.exists():
        scenes = json.loads(scenes_file.read_text(encoding="utf-8"))
    else:
        scenes = ["an empty city street at night in the rain, lone streetlight, deep blue shadows"] + \
                 make_scenes(news["items"]) + \
                 ["a quiet city skyline at dawn after rain, a single red light on the horizon"]
        scenes_file.write_text(json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")
    imgs = make_images(scenes, work)

    # границы картинок: каждая длится до начала следующей группы
    bounds = [0.0] + [g[0] - t0 for g in groups[1:]] + [total]
    durs = [bounds[i + 1] - bounds[i] for i in range(len(groups))]

    inputs, filt = [], []
    for i, (p, d) in enumerate(zip(imgs, durs)):
        inputs += ["-loop", "1", "-t", f"{d:.3f}", "-i", str(p)]
        frames = max(1, int(d * FPS))
        filt.append(f"[{i}:v]zoompan=z='min(zoom+0.0005,1.10)':d={frames}:x='iw/2-(iw/zoom/2)'"
                    f":y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},setsar=1[v{i}]")
    n = len(imgs)
    filt.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[base]")

    label = work / "label.png"
    label_overlay(news["date"], label)
    inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(label)]
    filt.append(f"[base][{n}:v]overlay=0:40[o0]")
    cur = "o0"
    for j, (txt, a, b) in enumerate(lines):
        png = work / f"line_{j:02d}.png"
        line_overlay(txt, png)
        k = n + 1 + j
        inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(png)]
        start = max(0.0, a - t0 - 0.05)
        end = (lines[j + 1][1] - t0 - 0.05) if j + 1 < len(lines) else (b - t0 + 1.0)
        filt.append(f"[{cur}][{k}:v]overlay=0:{H - 520}:enable='between(t,{start:.2f},{end:.2f})'[o{j + 1}]")
        cur = f"o{j + 1}"
    filt.append(f"[{cur}]fade=t=in:st=0:d=0.6,fade=t=out:st={total - 2:.2f}:d=2[vout]")

    a_idx = n + 1 + len(lines)
    inputs += ["-ss", f"{t0:.3f}", "-t", f"{total:.3f}", "-i", str(song_path)]
    filt.append(f"[{a_idx}:a]afade=t=out:st={total - 2.5:.2f}:d=2.5,loudnorm=I=-14:TP=-1.5[aout]")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(filt), "-map", "[vout]", "-map", "[aout]",
           "-t", f"{total:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
           "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(out_path)]
    subprocess.run(cmd, check=True)
    return out_path, total


if __name__ == "__main__":
    a = sys.argv[1:]
    out, total = build(a[0], a[1], a[2], a[3], a[4])
    print(f"{out}: {total:.1f} с")
