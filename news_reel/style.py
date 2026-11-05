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
    "cta": CTA,
    "hashtags": "",
}
KEYS = ["name", "image_style", "suno_style", "lyrics_extra", "cta", "hashtags"]


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

Правила анализа:
- Данных пока мало: аккаунт молодой, числа маленькие и шумные. Не делай жёстких выводов по одному ролику. Меняй за раз 1-2 элемента (так видно, что сработало) и каждый день пробуй новый стиль, не повторяя последние.
- Смотри на досмотр (ig_reels_avg_watch_time, reels_skip_rate), репосты и сохранения важнее лайков. Если метрик ещё нет, опирайся на заметки о трендах и разнообразие.
- Если какой-то стиль заметно лучше, развивай его, а не бросай.

Можно менять только эти поля (всё остальное зафиксировано):
name: короткое название стиля по-русски;
image_style: описание визуального стиля иллюстраций, английский, 1-2 предложения (палитра, техника, настроение); без людей, лиц, текста;
suno_style: музыкальный стиль песни для Suno, английский (жанр, темп BPM, настроение, вокал, инструменты); текст песни по-русски;
lyrics_extra: до 2 предложений по подаче куплетов (настроение, лексика, ритм), по-русски; факты менять нельзя, без мата;
cta: призыв к подписке одной фразой по-русски, можно новую формулировку;
hashtags: 3-5 хэштегов через пробел, по теме, или пустая строка.

Заметки о трендах:
{trends}

История выпусков (последние, со статистикой, от старых к новым):
{history}

Подписчиков сейчас: {followers}.

Верни только JSON с полями name, image_style, suno_style, lyrics_extra, cta, hashtags и ещё rationale (2-3 предложения по-русски: что показала статистика и почему выбран этот стиль)."""


def next_style(h, trends=""):
    brief = [{"date": p.get("date"), "style": {k: p["style"].get(k) for k in KEYS},
              "metrics": p.get("metrics", {})} for p in h["posts"][-10:]]
    q = PROMPT.format(trends=trends.strip() or "нет заметок", history=json.dumps(brief, ensure_ascii=False),
                      followers=h.get("followers", "?"))
    for _ in range(3):
        try:
            txt = paraphrase.call_openai(q, MODEL)
            d = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
            if all(isinstance(d.get(k), str) for k in KEYS) and d["image_style"].strip() and d["suno_style"].strip():
                return {k: d[k].strip() for k in KEYS} | {"rationale": str(d.get("rationale", "")).strip()}
        except Exception as e:
            print("выбор стиля не удался, повтор:", e)
    last = h["posts"][-1]["style"] if h["posts"] else DEFAULT
    return dict(last, rationale="модель не ответила, взят прошлый стиль")


def apply(cfg):
    lyric_video.STYLE = cfg["image_style"].rstrip(". ") + ". " + IMG_SAFETY
    suno.STYLE = cfg["suno_style"]
    base = rap.RULES.split("\nДополнительно по подаче:")[0]
    rap.RULES = base + (f"\nДополнительно по подаче: {cfg['lyrics_extra']}" if cfg["lyrics_extra"] else "")
