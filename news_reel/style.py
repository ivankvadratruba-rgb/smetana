"""Стиль выпуска и самообучение: история публикаций хранится в R2, стиль на каждый день выбирает модель
по статистике прошлых роликов и (если есть) по заметкам о трендах.

Жёсткие правила безопасности (без лиц, насилия, текста на картинках, мата) модель менять не может.
"""
import json
import os

import lyric_video
import paraphrase
import rap
import r2
import stats
import suno

HISTORY = "state/history.json"
MODEL = os.environ.get("STYLE_MODEL", "gpt-4.1")
IMG_SAFETY = ("vertical composition, no text, no letters, no numbers, no logos, no faces, no real people, "
              "no violence shown, no weapons, not photorealistic. Scene: ")
CTA = "Подпишись на Новости в треке, чтобы не пропустить завтрашний выпуск."
DEFAULT = {
    "name": "базовый нуар",
    "image_style": "Dark stylized editorial illustration, graphic novel noir, limited palette of deep blue "
                   "and black with one red accent",
    "suno_style": suno.STYLE,
    "lyrics_extra": "",
    "scene_mood": "ночь, дождь, городские улицы",
    "cta": CTA,
    "hashtags": "",
}
KEYS = ["name", "image_style", "suno_style", "lyrics_extra", "scene_mood", "cta", "hashtags"]


EXPERIMENT_START = "2026-11-05"
EXPERIMENT_END = "2026-12-05"     # месяц экспериментов с форматом, потом режим анализа и закрепления лучшего
EXTRA = ["variable", "hypothesis"]


def phase(day, h=None):
    """experiment (идут эксперименты) или analysis (месяц прошёл: анализ и закрепление лучшего)."""
    exp = (h or {}).get("experiment") or {}
    return "experiment" if day.isoformat() < exp.get("end", EXPERIMENT_END) else "analysis"


def load_history():
    raw = r2.get(HISTORY)
    if raw:
        return json.loads(raw)
    # первый запуск: уже опубликованные ролики считаем выпущенными в базовом стиле
    posts = [{"date": m["timestamp"][:10], "media_id": m["id"], "style": DEFAULT,
              "published_at": m["timestamp"]} for m in stats.list_media()]
    return {"posts": list(reversed(posts))}


def save_history(h):
    r2.put(HISTORY, json.dumps(h, ensure_ascii=False, indent=2).encode("utf-8"))


def refresh_stats(h, last=14):
    for p in h["posts"][-last:]:
        p["metrics"] = stats.media_metrics(p["media_id"])
    h["followers"] = stats.followers()


PROMPT = """Ты редактор и аналитик Instagram-аккаунта новостных рилсов «Новости в треке»: каждый день новая песня (рэп по новостям дня), иллюстрации; в подписи стоят дата, текст песни и призыв к подписке.
Твоя задача: выбрать стиль завтрашнего выпуска так, чтобы расти по просмотрам, досмотрам, репостам, сохранениям и подпискам.

ФАЗА: {phase_text}

Правила анализа:
- Данных пока мало: аккаунт молодой, числа маленькие и шумные. Не делай жёстких выводов по одному ролику. Меняй за раз 1-2 элемента (так видно, что сработало) и каждый день пробуй новый стиль, не повторяя последние.
- Смотри на досмотр (ig_reels_avg_watch_time, reels_skip_rate), репосты и сохранения важнее лайков. Если метрик ещё нет, опирайся на заметки о трендах и разнообразие.
- Если какой-то стиль заметно лучше, развивай его, а не бросай.

Можно менять только эти поля (всё остальное зафиксировано):
news_count: целое число от 4 до 8, сколько новостей взять в выпуск (длина ролика: 4 новости примерно 35 секунд, 8 примерно 60-75);
variable: что именно ты меняешь сегодня относительно прошлого выпуска (одно-два слова, например: жанр, визуал, длина, настрой сцен, подача текста, призыв);
hypothesis: одна фраза, что ты ожидаешь от этого изменения и по какой метрике это проверим;
name: короткое название стиля по-русски;
image_style: описание визуального стиля иллюстраций, английский, 1-2 предложения (палитра, техника, настроение); без людей, лиц, текста;
suno_style: музыкальный стиль песни для Suno, английский (жанр, темп BPM, настроение, вокал, инструменты); текст песни по-русски;
lyrics_extra: до 2 предложений по подаче куплетов (настроение, лексика, ритм), по-русски; факты менять нельзя, без мата;
scene_mood: общий настрой сцен на день по-русски: время суток, места, ракурсы (например: яркий день, интерьеры и транспорт, крупные планы предметов); не повторяй настрой прошлых дней, ночные улицы и фонари не выбирай;
cta: призыв к подписке одной фразой по-русски, можно новую формулировку;
hashtags: 3-5 хэштегов через пробел, по теме, или пустая строка.

Заметки о трендах:
{trends}

История выпусков (последние, со статистикой, от старых к новым):
{history}

Подписчиков сейчас: {followers}.

Верни только JSON с полями name, image_style, suno_style, lyrics_extra, scene_mood, cta, hashtags и ещё news_count, variable, hypothesis и rationale (2-3 предложения по-русски: что показала статистика и почему выбран этот стиль)."""


MOODS = ["яркий день, интерьеры, транспорт, крупные планы предметов",
         "утро, оживлённые улицы, детали городской среды",
         "закат, природа, вода, небо",
         "вечер, уютные интерьеры, тёплый свет",
         "пасмурный день, архитектура, необычные ракурсы сверху"]


def next_style(h, trends="", day=None):
    from datetime import date
    day = day or date.today()
    exp_end = (h.get("experiment") or {}).get("end", EXPERIMENT_END)
    phase_text = ("ЭКСПЕРИМЕНТЫ (первый месяц, до {end}). Цель: найти формат, который работает. Меняй за раз одну главную переменную (variable), чтобы потом можно было сравнить; не повторяй уже проверенные комбинации; пробуй разные жанры, визуал, длину, подачу, призыв. Записывай гипотезу.".format(end=exp_end) if phase(day, h) == "experiment" else "АНАЛИЗ (эксперименты закончены). Цель: закрепить лучшее. Сравни стили по досмотру, репостам, сохранениям и подпискам, выбери лучшие, лишь слегка варьируй их (по одной мелочи за раз), не возвращайся к слабым. В rationale напиши вывод: что сработало, что нет.")
    posts = h["posts"]
    last = posts[-1]["style"] if posts else DEFAULT
    rotate = MOODS[len(posts) % len(MOODS)]       # запасной настрой сцен, если модель его не вернула
    brief = [{"date": p.get("date"), "style": {k: p["style"].get(k) for k in KEYS},
              "metrics": p.get("metrics", {})} for p in posts[-10:]]
    q = PROMPT.format(phase_text=phase_text, trends=trends.strip() or "нет заметок", history=json.dumps(brief, ensure_ascii=False),
                      followers=h.get("followers", "?"))
    for _ in range(3):
        try:
            txt = paraphrase.call_openai(q, MODEL)
            d = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        except Exception as e:
            print("выбор стиля не удался, повтор:", e)
            continue
        ok = lambda k: isinstance(d.get(k), str) and d[k].strip()
        if not (ok("image_style") and ok("suno_style")):
            print("в ответе нет image_style или suno_style, повтор")
            continue
        cfg = {k: (d[k].strip() if ok(k) else (rotate if k == "scene_mood" else last.get(k, DEFAULT[k])))
               for k in KEYS if k != "hashtags"}
        cfg["hashtags"] = d["hashtags"].strip() if isinstance(d.get("hashtags"), str) else ""
        cfg["rationale"] = str(d.get("rationale", "")).strip()
        cfg["variable"] = str(d.get("variable", "")).strip()
        cfg["hypothesis"] = str(d.get("hypothesis", "")).strip()
        try:
            cfg["news_count"] = max(4, min(8, int(d.get("news_count", 8))))
        except (TypeError, ValueError):
            cfg["news_count"] = 8
        return cfg
    return dict(last, scene_mood=rotate, news_count=last.get("news_count", 8), variable="настрой сцен", hypothesis="", rationale="модель не ответила, стиль прошлого дня с новым настроем сцен")


def apply(cfg):
    lyric_video.STYLE = cfg["image_style"].rstrip(". ") + ". " + IMG_SAFETY
    suno.STYLE = cfg["suno_style"]
    lyric_video.SCENE_MOOD = cfg.get("scene_mood", "")
    base = rap.RULES.split("\nДополнительно по подаче:")[0]
    rap.RULES = base + (f"\nДополнительно по подаче: {cfg['lyrics_extra']}" if cfg["lyrics_extra"] else "")
