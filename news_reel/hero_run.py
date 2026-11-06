"""Выпуск, где персонаж сам читает трек: новости -> текст -> песня и чистый вокал -> кадры героя в обстановке
новостей -> оживление (fal.ai, OmniHuman) -> монтаж со строками.

Запуск по стадиям (каждая использует кэш в out/hero_ДАТА, можно запускать повторно):
  python3 hero_run.py ДАТА prep       новости, текст, песня Suno, вокал
  python3 hero_run.py ДАТА frames     кадры героя (дёшево, можно проверить глазами)
  python3 hero_run.py ДАТА animate    оживление кадров (платно: 0,105 доллара за секунду)
  python3 hero_run.py ДАТА assemble   монтаж итогового mp4
  python3 hero_run.py ДАТА all

Правила: фото новостей используются только как источник описания обстановки (без лиц и людей), сами пиксели чужих
фото в кадры не попадают. Тяжёлые новости, политики и насилие показываются нейтральным символическим местом.
Лимит расходов на fal: переменная FAL_BUDGET (по умолчанию 8 долларов на выпуск).
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

import align
import fal_video
import lyric_video
import paraphrase
import rap
import style
import suno
import telegram_source

UA = suno.UA
CHANNEL = os.environ.get("NEWS_CHANNEL", "ostorozhno_novosti")
NEWS_COUNT = int(os.environ.get("HERO_NEWS", "6"))
FAL_BUDGET = float(os.environ.get("FAL_BUDGET", "8"))
REFS = ["character/front_waist.png", "character/full_body.png"]
SUNO_DETAILS = "90 BPM, dusty dark sample, hard dry drums, fast aggressive punchy flow, clear articulate mid-range male voice, natural and not too deep, no heavy rasp"
W, H, FPS = 1080, 1920, 30

BRIEF_RULES = (
    "Опиши ОБСТАНОВКУ для художника: место, время суток, погода, ключевые предметы, цвета, атмосфера. "
    "ВАЖНО: не описывай лица и внешность людей, не называй людей, организации и символику; людей упоминай только "
    "как далёкие силуэты. Если новость про насилие, смерть, преступление, атаку, политиков или конфликт, опиши "
    "только нейтральное символическое место (фасад здания, пустой коридор, улица, дверь), а не само событие. "
    "Никаких верёвок, петель и тросов. 2-3 предложения по-английски, только описание обстановки."
)
KEYFRAME = (
    "Photorealistic cinematic vertical 9:16 frame, shot on a full-frame camera, natural light, fine film grain. "
    "The exact same man as in the reference images (identical face, shaved head, handlebar mustache, silver hoop "
    "earring, white shirt, thin black tie, oversized dark navy fur coat) walking slowly toward the camera, "
    "TIGHT chest-up medium close-up cropped at his belt, head and shoulders fill most of the frame, his face is large "
    "(at least a quarter of the frame height), sharp and well lit, looking straight into the camera with an intense stern expression, mouth slightly open mid-rap. {scene} "
    "No other people in focus, no text, no logos, no flags with symbols, no ropes or loops."
)
CLIP_PROMPT = (
    "The man walks slowly toward the camera while rapping aggressively, clear precise articulation of every word, "
    "mouth and lips move constantly with the vocal, stern intense expression, small rhythmic head nods, natural "
    "arm swing, fur coat sways. Background elements move naturally (leaves, branches, light). The camera "
    "backs up smoothly so he stays waist-up in frame."
)
INTRO_SCENE = "Setting: an empty city street at early evening, warm streetlights just coming on, wet asphalt, calm."
OUTRO_SCENE = "Setting: the same city street at night, distant warm window lights, he is stopping and about to nod."


def jget(url, data=None, timeout=120):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def news_photo(post):
    try:
        req = urllib.request.Request(f"https://t.me/{CHANNEL}/{post}?embed=1&mode=tme", headers={"User-Agent": UA})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        urls = re.findall(r"background-image:url\('([^']+)'\)", html)
        if urls:
            return urllib.request.urlopen(urllib.request.Request(urls[0], headers={"User-Agent": UA}),
                                          timeout=30).read()
    except Exception as e:
        print("фото не получено:", e)
    return None


def scene_brief(item):
    """Описание обстановки по фото новости (или по тексту, если фото нет)."""
    photo = news_photo(item.get("post"))
    text = f"Новость: {item['title']}. {item.get('summary', '')}\n" + BRIEF_RULES
    content = [{"type": "text", "text": text}]
    if photo:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(photo).decode()}})
    r = jget("https://api.openai.com/v1/chat/completions",
             {"model": "gpt-4.1", "messages": [{"role": "user", "content": content}]})
    return "Setting: " + r["choices"][0]["message"]["content"].strip()


_FAL_REFS = []


def gen_keyframe_fal(scene, dst):
    """Запасной путь: кадр героя через fal (nano-banana/edit, 0,04 доллара за кадр), если OpenAI недоступен."""
    import fal_client
    fal_video.load_key()
    if not _FAL_REFS:
        _FAL_REFS.extend(fal_client.upload_file(p) for p in REFS)
    res = fal_client.subscribe("fal-ai/nano-banana/edit", arguments={
        "prompt": "Use the person from the reference images as the exact same man (identical face, shaved head, "
                  "mustache, earring, navy fur coat, white shirt, black tie). " + KEYFRAME.format(scene=scene),
        "image_urls": _FAL_REFS, "aspect_ratio": "2:3", "output_format": "png", "num_images": 1})
    url = res["images"][0]["url"]
    data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120).read()
    Path(dst).write_bytes(data)


def gen_keyframe(scene, dst):
    if os.environ.get("HERO_IMAGE_PROVIDER") == "fal":
        return gen_keyframe_fal(scene, dst)
    files = [("image[]", (Path(p).name, Path(p).read_bytes(), "image/png")) for p in REFS]
    r = httpx.post("https://api.openai.com/v1/images/edits", headers={"User-Agent": UA}, timeout=290, files=files,
                   data={"model": "gpt-image-2", "prompt": KEYFRAME.format(scene=scene), "size": "1024x1536",
                         "quality": "medium", "n": "1"})
    if r.status_code == 429 and "credit_balance_exhausted" in r.text:   # на OpenAI нет кредитов: рисуем через fal
        print("OpenAI без кредитов, кадр через fal")
        return gen_keyframe_fal(scene, dst)
    r.raise_for_status()
    Path(dst).write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))


def segments(work, lyr, ts):
    """Границы сегментов: вступление, каждая новость, концовка. Возвращает (t0, t_end, [(начало, конец)])."""
    g = align.group_times(lyr, ts)
    t0 = max(0.0, g[0][0] - 1.5)
    t_end = g[-1][1] + 0.7     # сразу после последнего слова: дальше модель шевелит ртом в тишине
    cuts = [t0] + [(g[i - 1][1] + g[i][0]) / 2 for i in range(1, len(g))] + [t_end]
    return t0, t_end, [(cuts[i], cuts[i + 1]) for i in range(len(g))]


def cut_audio(src, a, b, dst):
    d = max(b - a, 4.0)       # модель принимает аудио не короче 4 секунд
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{d:.3f}",
                    "-i", str(src), "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(dst)], check=True)


def stage_prep(day, work):
    news_f, lyr_f = work / "news_final.json", work / "lyrics.json"
    if not news_f.exists():
        items = telegram_source.collect(CHANNEL, day, NEWS_COUNT)
        if len(items) < 4:
            sys.exit(f"за {day} найдено {len(items)} новостей, нужно минимум 4")
        news_f.write_text(json.dumps({"date": day.isoformat(), "items": paraphrase.paraphrase(items)},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    news = json.loads(news_f.read_text(encoding="utf-8"))
    cfg = dict(style.DEFAULT, suno_style=SUNO_DETAILS, lyrics_extra="")
    style.apply(cfg)
    if not lyr_f.exists():
        lyr = rap.make_lyrics(news["items"], news["date"])
        lyr_f.write_text(json.dumps(lyr, ensure_ascii=False, indent=2), encoding="utf-8")
    lyr = json.loads(lyr_f.read_text(encoding="utf-8"))
    if not (work / "suno_1.mp3").exists():
        suno.generate(lyr, work)
    if not (work / "vocals.mp3").exists():
        try:
            suno.separate_vocals(work)
        except Exception as e:
            print("ВНИМАНИЕ: вокал не отделён, будет полный микс (губы хуже):", e)
    print("prep готово:", [p.name for p in sorted(work.glob("*")) if p.is_file()])


def stage_frames(day, work):
    news = json.loads((work / "news_final.json").read_text(encoding="utf-8"))
    briefs_f = work / "scenes.json"
    scenes = json.loads(briefs_f.read_text(encoding="utf-8")) if briefs_f.exists() else None
    if not scenes:
        scenes = [INTRO_SCENE] + [scene_brief(it) for it in news["items"]] + [OUTRO_SCENE]
        briefs_f.write_text(json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")
    paths = [work / f"frame_{i:02d}.png" for i in range(len(scenes))]

    def one(i):
        if not paths[i].exists():
            for attempt in range(3):
                try:
                    gen_keyframe(scenes[i], paths[i])
                    return
                except Exception as e:
                    print(f"кадр {i}: попытка {attempt + 1}: {e}")
                    time.sleep(65)
            raise RuntimeError(f"кадр {i} не создан")
    with ThreadPoolExecutor(1) as ex:
        list(ex.map(one, range(len(scenes))))
    print("кадры готовы:", len(paths))


def stage_animate(day, work):
    lyr = json.loads((work / "lyrics.json").read_text(encoding="utf-8"))
    ts = json.loads((work / "suno_1_ts.json").read_text(encoding="utf-8"))["data"]["alignedWords"]
    _, _, segs = segments(work, lyr, ts)
    audio_src = work / ("vocals.mp3" if (work / "vocals.mp3").exists() else "suno_1.mp3")
    secs = [max(b - a, 4.0) for a, b in segs]
    cost = sum(secs) * fal_video.PRICE_PER_SEC
    print(f"сегментов {len(segs)}, {sum(secs):.0f} с, ориентировочно {cost:.2f} доллара (лимит {FAL_BUDGET})")
    if cost > FAL_BUDGET:
        sys.exit("выше лимита FAL_BUDGET, остановлено")
    fal_video.MAX_SECONDS = 15
    fal_video.load_key()

    def one(i):
        out = work / f"clip_{i:02d}.mp4"
        if out.exists():
            return
        a_f = work / f"seg_{i:02d}.mp3"
        cut_audio(audio_src, segs[i][0], segs[i][1], a_f)
        try:    # один платный вызов на сегмент, без слепых повторов
            fal_video.avatar_clip(work / f"frame_{i:02d}.png", a_f, out, prompt=CLIP_PROMPT, go=True)
        except Exception as e:
            print(f"сегмент {i} не оживлён: {e}")
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, range(len(segs))))
    print("клипы:", sorted(p.name for p in work.glob("clip_*.mp4")))


def stage_assemble(day, work):
    news = json.loads((work / "news_final.json").read_text(encoding="utf-8"))
    lyr = json.loads((work / "lyrics.json").read_text(encoding="utf-8"))
    ts = json.loads((work / "suno_1_ts.json").read_text(encoding="utf-8"))["data"]["alignedWords"]
    t0, t_end, segs = segments(work, lyr, ts)
    total = t_end - t0
    lines = align.line_times(lyr, ts)
    inputs, filt = [], []
    for i, (a, b) in enumerate(segs):
        d = b - a
        clip = work / f"clip_{i:02d}.mp4"
        if clip.exists():
            inputs += ["-i", str(clip)]
            filt.append(f"[{i}:v]trim=duration={d:.3f},setpts=PTS-STARTPTS,scale=-2:{H},crop={W}:{H},fps={FPS},setsar=1[v{i}]")
        else:   # запасной вариант: кадр без анимации с плавным приближением
            inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{d:.3f}", "-i", str(work / f"frame_{i:02d}.png")]
            filt.append(f"[{i}:v]scale=-2:{H},crop={W}:{H},zoompan=z='min(1+0.0004*on,1.08)':d=1:s={W}x{H}:fps={FPS},setsar=1[v{i}]")
    n = len(segs)
    filt.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[base]")
    label = work / "label.png"
    lyric_video.label_overlay(news["date"], label)
    inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{total:.3f}", "-i", str(label)]
    filt.append(f"[base][{n}:v]overlay=0:40[o0]")
    cur = "o0"
    for j, (txt, a, b) in enumerate(lines):
        png = work / f"line_{j:02d}.png"
        lyric_video.line_overlay(txt, png)
        k = n + 1 + j
        inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{total:.3f}", "-i", str(png)]
        start = max(0.0, a - t0 - 0.05)
        end = (lines[j + 1][1] - t0 - 0.05) if j + 1 < len(lines) else (b - t0 + 1.0)
        filt.append(f"[{cur}][{k}:v]overlay=0:{H - 540}:enable='between(t,{start:.2f},{end:.2f})'[o{j + 1}]")
        cur = f"o{j + 1}"
    filt.append(f"[{cur}]fade=t=in:st=0:d=0.5,fade=t=out:st={total - 0.8:.2f}:d=0.8[vout]")
    a_idx = n + 1 + len(lines)
    inputs += ["-ss", f"{t0:.3f}", "-t", f"{total:.3f}", "-i", str(work / "suno_1.mp3")]
    filt.append(f"[{a_idx}:a]afade=t=out:st={total - 1.2:.2f}:d=1.2,loudnorm=I=-14:TP=-1.5[aout]")
    out = work / "reel_hero.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt),
                    "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                    "-movflags", "+faststart", str(out)], check=True)
    print(f"готово: {out}, {total:.1f} с")


STAGES = {"prep": stage_prep, "frames": stage_frames, "animate": stage_animate, "assemble": stage_assemble}

if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2 or a[1] not in list(STAGES) + ["all"]:
        sys.exit(__doc__)
    day = datetime.strptime(a[0], "%Y-%m-%d").date()
    work = Path("out") / f"hero_{day.isoformat()}"
    work.mkdir(parents=True, exist_ok=True)
    for name in (STAGES if a[1] == "all" else [a[1]]):
        print(f"== {name} ==", flush=True)
        STAGES[name](day, work)
