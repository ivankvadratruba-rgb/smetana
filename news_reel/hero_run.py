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
REFS = ["character/front_waist.png", "character/portrait_front_b.png", "character/portrait_three_quarter.png"]   # опоры образа: по пояс, лицо анфас и в три четверти (кадр в полный рост уводит композицию от плотного плана)
SUNO_DETAILS = "90 BPM, dusty dark sample, hard dry drums, confident controlled rhythmic flow, calm menace not shouting, clear articulate mid-range male voice, natural and not too deep, no heavy rasp"
W, H, FPS = 1080, 1920, 30
LABEL_Y = 1060    # у героя лицо наверху кадра: дату ставим над строкой текста, ниже груди (сверху её закрыл бы интерфейс Instagram)
LINE_Y = H - 800   # строки текста выше подписи рилса и панели ответа сторис

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
CONCEPT = os.environ.get("HERO_CONCEPT", "studio_roles")   # studio_roles: эфирная студия + роли по новостям; walk: старый вариант

KEYFRAME_ROLES = (
    "Photorealistic cinematic vertical 9:16 frame, shot on a full-frame camera, fine film grain, moody hard lighting. "
    "The exact same man as in the reference images (identical face, shaved head, handlebar mustache, silver hoop "
    "earring, white shirt, thin black tie, always wearing his oversized dark navy fur coat) {scene} "
    "He looks straight into the camera with a serious, composed expression and quiet menace. Tight chest-up framing: the image is cropped "
    "at his chest, his legs and lower body are NOT visible, his face is large and sharp in the upper half of the frame, the surroundings "
    "visible behind and beside him. No other people, no text, no logos, no flags, no weapons, "
    "no blood, no ropes or loops."
)
ROLE_CLIP_PROMPT = (
    "The man raps confidently and calmly straight into the camera, clear precise articulation of every word, mouth and lips "
    "follow the vocal naturally, composed serious expression with quiet menace, never shouting, mouth never stretched wide, "
    "small rhythmic head nods and measured hand gestures that fit his role, his fur coat shifts slightly. The surroundings move naturally (light, dust, steam, screens). "
    "The camera slowly pushes in, he stays chest-up in frame."
)
STUDIO_INTRO = ("is standing in a dark TV news studio at a black desk with a studio microphone, behind him a wall of "
                "monitors glowing with abstract colorful imagery without any text, a red ON AIR lamp lit above, "
                "cables and spotlights around.")
STUDIO_OUTRO = ("is in the same dark TV news studio, the red ON AIR lamp now switched off, monitors behind him dimmed to "
                "a dark blue glow, he is about to sign off with a short nod.")
ROLE_RULES = (
    "Для каждой новости придумай роль и место для героя клипа: рэпер-ведущий в тёмной шубе «оказался на месте» новости. "
    "Опиши по-английски одним-двумя предложениями начиная со слов 'is ...': где он (место) и что у него в руках или рядом "
    "(реквизит под тему: фонарик при блэкауте, горы писем, наушники, ручка и бумага). Шуба на нём всегда. "
    "Правила: никаких других людей и лиц; без текста и логотипов; без оружия, крови, насилия, тел и жертв; для новостей про "
    "гибель, насилие, задержания, приговоры, политиков и конфликты показывай только нейтральное символическое место "
    "(пустой коридор, закрытая дверь, ночная улица, зал ожидания) и сдержанный реквизит, без самого события; "
    "без верёвок и петель. Все места должны сильно отличаться друг от друга. "
    'Верни только JSON-массив строк той же длины и порядка: ["is ...", ...]'
)


ANGLES = ["eye-level front view",
          "low-angle shot from slightly below, looking up at him",
          "three-quarter view from his left side, his head turned toward the camera",
          "high-angle shot from slightly above",
          "three-quarter view from his right side, his head turned toward the camera",
          "slightly tilted dutch-angle framing"]
CAMERA_MOVES = ["the camera slowly orbits around him from left to right while he keeps looking at the lens",
                "the camera dollies in fast toward his face and settles",
                "the camera cranes up from a low angle to eye level",
                "the camera arcs around him from right to left",
                "a handheld camera with slight shake pushing in",
                "the camera pulls back and sweeps to the right"]
HOOK_RULES = (
    "Из новостей выбери одну самую цепляющую для крючка в начале ролика и придумай экранный текст крючка: до 7 слов, "
    "обычным предложением с заглавной буквы в начале (НЕ капслоком), по-русски, только факты из заголовка и без мата. Для новостей про гибель, насилие, политиков и "
    'конфликты тон сдержанный. Верни только JSON: {"index": номер новости начиная с 1, "text": "ТЕКСТ"}'
)


SHOT_ZOOMS = [1.0, 1.08, 1.04, 1.1, 1.06, 1.1]    # цифровой зум не больше 1,1: больше размывает кадр       # масштабы коротких планов: общий, крупный, средний, очень крупный...
SHOT_SHIFTS = [0.0, -0.05, 0.04, -0.03, 0.05, 0.0]       # лёгкий сдвиг кадра по горизонтали
MACROS = ["macro_front_b", "macro_profile_right", "macro_front_c", "macro_three_quarter_a", "macro_eyes",
          "macro_profile_left", "macro_mouth", "macro_three_quarter_b", "macro_front_a", "macro_eye_ear"]


def macro_paths():
    out = []
    for n in MACROS:
        for ext in ("jpg", "png"):
            q = Path("character/macro") / f"{n}.{ext}" if ext == "jpg" else Path("character") / f"{n}.{ext}"
            if q.exists():
                out.append(q)
                break
    return out


def clip_prompt(i, n):
    base = ROLE_CLIP_PROMPT.replace("The camera slowly pushes in, he stays chest-up in frame.", "He stays chest-up in frame.")
    if i == 0:
        move = "the camera dollies in toward his face from a wider studio shot"
    elif i == n - 1:
        move = "the camera slowly pulls back from his face to a wider studio shot"
    else:
        move = CAMERA_MOVES[(i - 1) % len(CAMERA_MOVES)]
    return base + " Camera movement: " + move + "."


def stage_hook(day, work):
    f = work / "hook.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    news = json.loads((work / "news_final.json").read_text(encoding="utf-8"))
    src = [{"n": i + 1, "title": it["title"], "summary": it.get("summary", "")} for i, it in enumerate(news["items"])]
    for _ in range(3):
        txt = paraphrase.call_openai(HOOK_RULES + "\n\nНовости:\n" + json.dumps(src, ensure_ascii=False), "gpt-4.1")
        try:
            d = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
            k = int(d["index"]) - 1
            t = str(d["text"]).strip()
            if 0 <= k < len(src) and 3 <= len(t) <= 70:
                f.write_text(json.dumps({"index": k, "text": t}, ensure_ascii=False), encoding="utf-8")
                return {"index": k, "text": t}
        except (ValueError, KeyError, json.JSONDecodeError):
            pass
    d = {"index": 0, "text": "Главное в новостях сегодня"}
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def hook_png(text, path):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(lyric_video.FONT_BOLD, 92)
    d0 = ImageDraw.Draw(Image.new("RGBA", (W, 10)))
    rows = lyric_video.wrap(d0, text, font, W - 200)
    step = 112
    h = 60 + step * len(rows)
    img = Image.new("RGBA", (W, h + 20), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.rounded_rectangle((50, 10, W - 50, 10 + h), radius=28, fill=(205, 25, 35, 235))
    y = 10 + 30
    for r in rows:
        w = d.textlength(r, font=font)
        d.text(((W - w) / 2, y), r, font=font, fill=(255, 255, 255, 255))
        y += step
    img.save(path)
    return img.height


def role_scenes(items):
    src = [{"title": it["title"], "summary": it.get("summary", ""), "setting": it.get("_setting", "")} for it in items]
    for _ in range(3):
        txt = paraphrase.call_openai(ROLE_RULES + "\n\nНовости:\n" + json.dumps(src, ensure_ascii=False), "gpt-4.1")
        try:
            arr = json.loads(txt[txt.find("["):txt.rfind("]") + 1])
            if len(arr) == len(items) and all(isinstance(x, str) and x.strip() for x in arr):
                arr = ["Setting: " + x.strip() if not x.strip().lower().startswith("setting") else x for x in arr]
                return [x.rstrip() + " Camera angle: " + ANGLES[(i + 1) % len(ANGLES)] + "." for i, x in enumerate(arr)]
        except json.JSONDecodeError:
            pass
    raise RuntimeError("не удалось придумать роли")


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


_FAL_UP = {}


ANGLE_REFS = ["character/portrait_3q_left_a.png", "character/portrait_three_quarter.png",
              "character/portrait_3q_stern.png", "character/portrait_3q_left_b.png"]   # третья опора кадра, по кругу


def _refs_for(i):
    """Базовые опоры плюс одна под ракурс кадра: так образ держится при смене углов."""
    extra = ANGLE_REFS[(i or 0) % len(ANGLE_REFS)]
    base = [r for r in REFS if r != extra]
    return base + [extra]


def _kf(scene):
    return (KEYFRAME_ROLES if CONCEPT == "studio_roles" else KEYFRAME).format(scene=scene)


def gen_keyframe_fal(scene, dst, i=None):
    """Запасной путь: кадр героя через fal (nano-banana/edit, 0,04 доллара за кадр), если OpenAI недоступен."""
    import fal_client
    fal_video.load_key()
    refs = _refs_for(i)
    for p_ in refs:
        if p_ not in _FAL_UP:
            _FAL_UP[p_] = fal_client.upload_file(p_)
    res = fal_client.subscribe("fal-ai/nano-banana/edit", arguments={
        "prompt": "Use the person from the reference images as the exact same man (identical face, shaved head, "
                  "mustache, earring, navy fur coat, white shirt, black tie). " + _kf(scene),
        "image_urls": [_FAL_UP[p_] for p_ in refs], "aspect_ratio": "2:3", "output_format": "png", "num_images": 1})
    try:
        import ledger
        ledger.fal("nano-banana", 1, 0.0398)
    except Exception:
        pass
    url = res["images"][0]["url"]
    data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120).read()
    Path(dst).write_bytes(data)


def gen_keyframe(scene, dst, i=None):
    if os.environ.get("HERO_IMAGE_PROVIDER") == "fal":
        return gen_keyframe_fal(scene, dst, i)
    files = [("image[]", (Path(p).name, Path(p).read_bytes(), "image/png")) for p in _refs_for(i)]
    r = httpx.post("https://api.openai.com/v1/images/edits", headers={"User-Agent": UA}, timeout=290, files=files,
                   data={"model": "gpt-image-2", "prompt": _kf(scene), "size": "1024x1536",
                         "quality": "medium", "n": "1"})
    if r.status_code == 429 and "credit_balance_exhausted" in r.text:   # на OpenAI нет кредитов: рисуем через fal
        print("OpenAI без кредитов, кадр через fal")
        return gen_keyframe_fal(scene, dst, i)
    r.raise_for_status()
    try:
        import ledger
        ledger.openai("gpt-image-2", r.json().get("usage"))
    except Exception:
        pass
    Path(dst).write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))


LEAD_IN = 0.4


def segments(work, lyr, ts):
    """Границы сегментов: вступление, каждая новость, концовка. Возвращает (t0, t_end, [(начало, конец)])."""
    g = align.group_times(lyr, ts)
    t0 = max(0.0, g[0][0] - LEAD_IN)      # ролик открывается почти сразу на слове, без тихих секунд
    t_end = g[-1][1] + 0.7     # сразу после последнего слова: дальше модель шевелит ртом в тишине
    cuts = [t0] + [(g[i - 1][1] + g[i][0]) / 2 for i in range(1, len(g))] + [t_end]
    return t0, t_end, [(cuts[i], cuts[i + 1]) for i in range(len(g))]


def cut_audio(src, a, b, dst):
    d = max(b - a, 4.0)       # модель принимает аудио не короче 4 секунд
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{d:.3f}",
                    "-i", str(src), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(dst)], check=True)   # wav: fal иногда не принимает mp3


def stage_prep(day, work, song=True):
    news_f, lyr_f = work / "news_final.json", work / "lyrics.json"
    if not news_f.exists():
        from daily_run import dedupe
        items = dedupe(telegram_source.collect(CHANNEL, day, NEWS_COUNT + 4), NEWS_COUNT)
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
    if not song:
        print("prep: песня не нужна (день без рилса)")
        return
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
        if CONCEPT == "studio_roles":
            for it in news["items"]:
                it["_setting"] = scene_brief(it)          # обстановка по фото новости, дальше к ней добавляется роль
            scenes = ["Setting: he " + STUDIO_INTRO] + role_scenes(news["items"]) + ["Setting: he " + STUDIO_OUTRO]
        else:
            scenes = [INTRO_SCENE] + [scene_brief(it) for it in news["items"]] + [OUTRO_SCENE]
        briefs_f.write_text(json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")
    paths = [work / f"frame_{i:02d}.png" for i in range(len(scenes))]

    def one(i):
        if not paths[i].exists():
            for attempt in range(3):
                try:
                    gen_keyframe(scenes[i], paths[i], i)
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
    lead = work / "lead.json"
    if not lead.exists():
        lead.write_text(json.dumps({"t0": segs[0][0]}), encoding="utf-8")
    fal_video.MAX_SECONDS = 15
    fal_video.load_key()

    def one(i):
        out = work / f"clip_{i:02d}.mp4"
        if out.exists():
            return
        a_f = work / f"seg_{i:02d}.wav"
        cut_audio(audio_src, segs[i][0], segs[i][1], a_f)
        try:    # один платный вызов на сегмент, без слепых повторов
            fal_video.avatar_clip(work / f"frame_{i:02d}.png", a_f, out, prompt=(clip_prompt(i, len(segs)) if CONCEPT == "studio_roles" else CLIP_PROMPT), go=True)
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
    main_total = t_end - t0
    lines = align.line_times(lyr, ts)
    # видео-крючок отключён по решению владельца (HOOK_VIDEO=1 включает); текст крючка остаётся для карусели и подписи
    hook = stage_hook(day, work) if (CONCEPT == "studio_roles" and os.environ.get("HOOK_VIDEO", "0") == "1") else None
    HD = 0.0
    if hook:      # крючок на целой фразе (не на полуслове): от начала строки до её конца
        _, la, lb = lines[1 + 2 * hook["index"]]
        HD = min(max(lb - la + 0.15, 2.6), 4.2)
    total = main_total + HD
    inputs, filt = [], []
    n = len(segs)

    def zoom(i, d, strong=False):
        k = 0.18 if strong else 0.12
        z = f"(1+{k}*t/{d:.3f})" if i % 2 == 0 else f"(1+{k}-{k}*t/{d:.3f})"     # наезд на чётных, отъезд на нечётных
        return f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame,crop={W}:{H},setsar=1"

    for i, (a, b) in enumerate(segs):
        d = b - a
        clip = work / f"clip_{i:02d}.mp4"
        off0 = 0.0
        if i == 0 and (work / "lead.json").exists():     # первый клип оживлён по аудио со старого t0: срезаем лишнее начало
            off0 = max(0.0, a - json.loads((work / "lead.json").read_text(encoding="utf-8"))["t0"])
        if clip.exists():
            inputs += ["-i", str(clip)]
            fl = ",fade=t=in:st=0:d=0.10:color=white" if (hook or i > 0) else ""     # вспышка на склейке
            kshots = max(1, round(d / 1.9))                                           # динамичный монтаж: планы по ~2 секунды
            if kshots == 1:
                filt.append(f"[{i}:v]trim=start={off0:.3f}:duration={d:.3f},setpts=PTS-STARTPTS,scale=-2:{H},crop={W}:{H},fps={FPS},setsar=1,{zoom(i, d)}{fl}[v{i}]")
            else:
                filt.append(f"[{i}:v]trim=start={off0:.3f}:duration={d:.3f},setpts=PTS-STARTPTS,scale=-2:{H},crop={W}:{H},fps={FPS},setsar=1,split={kshots}" + "".join(f"[r{i}_{j}]" for j in range(kshots)))
                for j in range(kshots):
                    a0, dj = d * j / kshots, d / kshots
                    zz = SHOT_ZOOMS[(i * 3 + j) % len(SHOT_ZOOMS)]
                    sh = SHOT_SHIFTS[(i + j) % len(SHOT_SHIFTS)]
                    filt.append(f"[r{i}_{j}]trim=start={a0:.3f}:duration={dj:.3f},setpts=PTS-STARTPTS,"
                                f"scale=w='trunc({W}*{zz}*(1+0.03*t/{dj:.3f})/2)*2':h='trunc({H}*{zz}*(1+0.03*t/{dj:.3f})/2)*2':eval=frame,"
                                f"crop={W}:{H}:'(iw-{W})/2+({sh})*{W}':'(ih-{H})*0.12',setsar=1[q{i}_{j}]")
                filt.append("".join(f"[q{i}_{j}]" for j in range(kshots)) + f"concat=n={kshots}:v=1:a=0{fl}[v{i}]")
        else:   # запасной вариант: кадр без анимации с плавным приближением
            inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{d:.3f}", "-i", str(work / f"frame_{i:02d}.png")]
            filt.append(f"[{i}:v]scale=-2:{H},crop={W}:{H},zoompan=z='min(1+0.0004*on,1.08)':d=1:s={W}x{H}:fps={FPS},setsar=1[v{i}]")
    idx = n
    parts = []
    if hook:   # крючок: начало самой цепляющей новости, увеличенный наезд, крупный текст
        k = hook["index"] + 1
        a_line = lines[1 + 2 * hook["index"]][1]
        off = max(0.0, a_line - segs[k][0])
        hc = work / f"clip_{k:02d}.mp4"
        if hc.exists():
            inputs += ["-i", str(hc)]
            filt.append(f"[{idx}:v]trim=start={off:.3f}:duration={HD:.3f},setpts=PTS-STARTPTS,scale=-2:{H},crop={W}:{H},fps={FPS},setsar=1,{zoom(0, HD, True)}[hk]")
        else:
            inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{HD:.3f}", "-i", str(work / f"frame_{k:02d}.png")]
            filt.append(f"[{idx}:v]scale=-2:{H},crop={W}:{H},fps={FPS},setsar=1[hk]")
        idx += 1
        parts.append("[hk]")
    parts += [f"[v{i}]" for i in range(n)]
    filt.append("".join(parts) + f"concat=n={len(parts)}:v=1:a=0[base0]")
    base = "base0"
    macros = macro_paths() if (CONCEPT == "studio_roles" and os.environ.get("MACROS", "0") == "1") else []   # вставки отключены, пока не привязаны к биту
    for k in range(len(news["items"]) if macros else 0):      # короткий крупный план лица на первую строку каждой новости
        la = lines[1 + 2 * k][1]
        T = HD + (la - t0) - 0.02
        md = 0.34
        inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{md + 0.1:.2f}", "-i", str(macros[k % len(macros)])]
        filt.append(f"[{idx}:v]scale=-2:{H},crop={W}:{H},scale=w='trunc({W}*(1+0.14*t/{md})/2)*2':h='trunc({H}*(1+0.14*t/{md})/2)*2':eval=frame,"
                    f"crop={W}:{H},setsar=1,setpts=PTS+{T:.3f}/TB[mx{k}]")
        filt.append(f"[{base}][mx{k}]overlay=0:0:enable='between(t,{T:.3f},{T + md:.3f})':eof_action=pass[mb{k}]")
        base = f"mb{k}"
        idx += 1
    filt.append(f"[{base}]null[base]")

    label = work / "label.png"
    if CONCEPT == "studio_roles":
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGBA", (W, 130), (0, 0, 0, 0)); dr = ImageDraw.Draw(img)
        dr.ellipse((50, 38, 74, 62), fill=(230, 30, 40, 255))
        y_, m_, d_ = news["date"][:4], news["date"][5:7], news["date"][8:10]
        dr.text((88, 28), f"Эфир · Новости в треке · {d_}.{m_}.{y_}", font=ImageFont.truetype(lyric_video.FONT_BOLD, 38), fill=(255, 255, 255, 235))
        img.save(label)
    else:
        lyric_video.label_overlay(news["date"], label)
    inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{total:.3f}", "-i", str(label)]
    filt.append(f"[base][{idx}:v]overlay=0:{LABEL_Y}:enable='gte(t,{HD:.2f})'[o0]")
    idx += 1
    cur = "o0"
    if hook:   # крупный текст крючка на первых секундах
        hp = work / "hook.png"
        hook_png(hook["text"], hp)
        inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{total:.3f}", "-i", str(hp)]
        filt.append(f"[{cur}][{idx}:v]overlay=0:{H - 1080}:enable='lt(t,{HD:.2f})'[oh]")
        cur = "oh"; idx += 1
    for j, (txt, a, b) in enumerate(lines):
        png = work / f"line_{j:02d}.png"
        lyric_video.line_overlay(txt, png)
        inputs += ["-framerate", str(FPS), "-loop", "1", "-t", f"{total:.3f}", "-i", str(png)]
        start = max(0.0, a - t0 - 0.05) + HD
        end = ((lines[j + 1][1] - t0 - 0.05) if j + 1 < len(lines) else (b - t0 + 1.0)) + HD
        filt.append(f"[{cur}][{idx}:v]overlay=0:{LINE_Y}:enable='between(t,{start:.2f},{end:.2f})'[p{j}]")
        cur = f"p{j}"; idx += 1
    filt.append(f"[{cur}]fade=t=out:st={total - 0.8:.2f}:d=0.8[vout]")     # без появления из чёрного: первый кадр сразу крючок

    audio_inputs = []
    if hook:   # затухание в конце крючка и нарастание в начале основной части: без щелчка и скачка на стыке
        inputs += ["-ss", f"{a_line:.3f}", "-t", f"{HD:.3f}", "-i", str(work / "suno_1.mp3")]
        filt.append(f"[{idx}:a]afade=t=out:st={HD - 0.18:.3f}:d=0.18[ha]")
        audio_inputs.append("[ha]")
        idx += 1
    inputs += ["-ss", f"{t0:.3f}", "-t", f"{main_total:.3f}", "-i", str(work / "suno_1.mp3")]
    filt.append(f"[{idx}:a]afade=t=in:st=0:d=0.15[ma]" if hook else f"[{idx}:a]anull[ma]")
    audio_inputs.append("[ma]")
    tail = f"afade=t=out:st={total - 1.2:.2f}:d=1.2,loudnorm=I=-14:TP=-1.5,aresample=48000[aout]"
    if len(audio_inputs) > 1:
        filt.append("".join(audio_inputs) + f"concat=n={len(audio_inputs)}:v=0:a=1," + tail)
    else:   # один вход: без concat (concat с одним входом портит метки времени звука и даёт треск)
        filt.append(f"{audio_inputs[0]}" + tail)
    out = work / "reel_hero.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt),
                    "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-r", str(FPS), "-fps_mode", "cfr", "-avoid_negative_ts", "make_zero",
                    "-movflags", "+faststart", str(out)], check=True)   # формат под Instagram: 48 кГц, чистый старт потоков
    print(f"готово: {out}, {total:.1f} с" + (f", крючок: {hook['text']}" if hook else ""))


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
    import ledger
    ledger.flush()
