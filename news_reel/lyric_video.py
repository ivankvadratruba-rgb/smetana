"""Лирик-видео: готовая песня + иллюстрации под каждую новость + слова на экране по меткам времени.

Запуск:
  python3 lyric_video.py news.json lyrics.json song.mp3 song_timestamps.json out/reel_lyric.mp4

Иллюстрации: тёмная стилизованная графика (gpt-image-2), без лиц, людей, надписей и сцен насилия.
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
LABEL_Y = 260     # дата ниже верхней панели Instagram (имя аккаунта и время)
LINE_Y = H - 800   # строки текста выше подписи рилса и панели ответа сторис
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "gpt-image-2")
SCENE_MODEL = os.environ.get("SCENE_MODEL", "gpt-4.1")
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
STYLE = ("Dark stylized editorial illustration, graphic novel noir, limited palette of deep blue and black "
         "with one red accent, vertical composition, no text, no letters, no numbers, no logos, no faces, "
         "no real people, no violence shown, no weapons, not photorealistic. Scene: ")

SCENE_MOOD = ""     # время суток, место и ракурс сцен на сегодня; задаёт стиль дня (style.py)

SCENE_RULES = (
    "Для вступления, каждой новости и концовки клипа придумай по ОДНОЙ символической сцене для иллюстрации, "
    "на английском, 1-2 предложения. Нужно ровно {n} сцен: первая для вступления (общий образ дня, "
    "приветствие), затем по одной на каждую новость по порядку, последняя для концовки (прощание, завтра). "
    "Правила: без лиц и без конкретных людей (силуэты вдали допустимы); без текста, надписей и логотипов; "
    "не показывай насилие, кровь, оружие, тела и жертв: для трагедий, атак и смертей используй сдержанную "
    "метафору (свеча, пустая дорога, опустевший предмет); не изображай реальных политиков и их символику; "
    "сцена должна ассоциироваться с темой новости, но не выглядеть как реальный кадр события. "
    "РАЗНООБРАЗИЕ: все сцены должны сильно отличаться друг от друга местом, предметами, ракурсом и временем суток. "
    "Не повторяй одни и те же образы (особенно уличные фонари, ночные улицы, дождь, свечи), каждый образ "
    "используй не больше одного раза за клип. Для обычных (не трагических) новостей бери разные дневные и "
    "вечерние места, интерьеры, транспорт, природу, предметы и крупные планы. "
    "Начинай вступление не с фонаря и не с ночной улицы. "
    "{mood}"
    'Верни только JSON-массив из {n} строк в том же порядке: ["scene 1", ...]'
)


def make_scenes(items):
    """Сцены для вступления, всех новостей и концовки: len(items) + 2 штук."""
    src = [{"title": it["title"], "summary": it.get("summary", "")} for it in items]
    n = len(items) + 2
    mood = f"Общий настрой сцен на сегодня: {SCENE_MOOD}. " if SCENE_MOOD else ""
    rules = SCENE_RULES.replace("{n}", str(n)).replace("{mood}", mood)
    for _ in range(3):
        txt = call_openai(rules + "\n\nНовости:\n" + json.dumps(src, ensure_ascii=False), SCENE_MODEL)
        try:
            arr = json.loads(txt[txt.find("["):txt.rfind("]") + 1])
            if len(arr) == n and all(isinstance(x, str) and x.strip() for x in arr):
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
    try:
        import ledger
        ledger.openai(IMAGE_MODEL, d.get("usage"))
    except Exception:
        pass
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
    """Прозрачная плашка со строкой песни внизу кадра; шрифт подбирается, чтобы строка влезла целиком."""
    img = Image.new("RGBA", (W, 460), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for size in (64, 56, 50, 44):
        font = ImageFont.truetype(FONT_BOLD, size)
        rows = wrap(d, text, font, W - 160)
        if len(rows) <= 3:
            break
    step = int(size * 1.32)
    h = 56 + step * len(rows)
    top = (460 - h) // 2
    d.rounded_rectangle((40, top, W - 40, top + h), radius=36, fill=(0, 0, 0, 150))
    y = top + 28
    for r in rows:
        w = d.textlength(r, font=font)
        d.text(((W - w) / 2, y), r, font=font, fill=(255, 255, 255, 255))
        y += step
    img.save(path)


def label_overlay(date_text, path):
    img = Image.new("RGBA", (W, 130), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y, m = date_text[:4], date_text[5:7]
    day = date_text[8:10]
    d.text((50, 30), f"Новости в треке  {day}.{m}.{y}", font=ImageFont.truetype(FONT_BOLD, 38), fill=(255, 255, 255, 235))
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
        scenes = make_scenes(news["items"])
        scenes_file.write_text(json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")
    imgs = make_images(scenes, work)

    # границы картинок: каждая длится до начала следующей группы
    bounds = [0.0] + [g[0] - t0 for g in groups[1:]] + [total]
    durs = [bounds[i + 1] - bounds[i] for i in range(len(groups))]

    inputs, filt = [], []
    for i, (p, d) in enumerate(zip(imgs, durs)):
        inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{d:.3f}", "-i", str(p)]
        # d=1: один выходной кадр на каждый входной; плавное приближение по номеру кадра on
        filt.append(f"[{i}:v]zoompan=z='min(1+0.0004*on,1.10)':d=1:x='iw/2-(iw/zoom/2)'"
                    f":y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},setsar=1[v{i}]")
    n = len(imgs)
    filt.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[base]")

    label = work / "label.png"
    label_overlay(news["date"], label)
    inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{total:.3f}", "-i", str(label)]
    filt.append(f"[base][{n}:v]overlay=0:{LABEL_Y}[o0]")
    cur = "o0"
    for j, (txt, a, b) in enumerate(lines):
        png = work / f"line_{j:02d}.png"
        line_overlay(txt, png)
        k = n + 1 + j
        inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{total:.3f}", "-i", str(png)]
        start = max(0.0, a - t0 - 0.05)
        end = (lines[j + 1][1] - t0 - 0.05) if j + 1 < len(lines) else (b - t0 + 1.0)
        filt.append(f"[{cur}][{k}:v]overlay=0:{LINE_Y}:enable='between(t,{start:.2f},{end:.2f})'[o{j + 1}]")
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
