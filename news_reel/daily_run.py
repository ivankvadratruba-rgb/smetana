"""Ежедневный выпуск целиком: новости -> текст -> песня Suno -> видео -> R2 -> Instagram.

Запуск: python3 daily_run.py [ГГГГ-ММ-ДД] [--no-publish]
Дата по умолчанию: сегодня по Москве. Если ролик за эту дату уже лежит в R2, повторно не публикуется.
Ключи: R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY из окружения; ключи OpenAI, Suno и токен
Instagram подставляет прокси сессии (или переменные OPENAI_API_KEY, SUNO_API_KEY, INSTAGRAM_ACCESS_TOKEN).
Проверку фактов не блокируем: это поэзия, пометки модели только выводятся в журнал.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import instagram_publish
import lyric_video
import paraphrase
import rap
import suno
import telegram_source

BUCKET = os.environ.get("R2_BUCKET", "vtreke-reels")
PUBLIC = os.environ.get("R2_PUBLIC_URL", "https://pub-ede07af251c94a0e8b12d5e08e0f9e44.r2.dev")
CHANNEL = os.environ.get("NEWS_CHANNEL", "ostorozhno_novosti")
MIN_ITEMS = 4
MONTHS = rap.MONTHS


def head_status(url):
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except Exception:
        return 0


def upload_r2(path, name):
    url = f"{os.environ['R2_ENDPOINT'].rstrip('/')}/{BUCKET}/{name}"
    r = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-X", "PUT",
                        "-H", "Content-Type: video/mp4", "--aws-sigv4", "aws:amz:auto:s3",
                        "--user", f"{os.environ['R2_ACCESS_KEY_ID']}:{os.environ['R2_SECRET_ACCESS_KEY']}",
                        "-T", str(path), url], capture_output=True, text=True)
    if r.stdout.strip() != "200":
        raise RuntimeError(f"загрузка в R2 не удалась: {r.stdout} {r.stderr[:200]}")
    public = f"{PUBLIC}/{name}"
    if head_status(public) != 200:
        raise RuntimeError("файл в R2 не открывается по публичной ссылке")
    return public


CTA = "Подпишись на Новости в треке, чтобы не пропустить завтрашний выпуск."


def make_hook(items, lyr):
    """Короткий крючок для начала подписи: самое цепляющее из выпуска, только по фактам новостей."""
    titles = "\n".join(f"- {it['title']}" for it in items)
    q = ("Выбери из новостей дня самую цепляющую, но не про гибель людей и не про атаки, если есть другой вариант, и напиши для подписи к ролику один короткий крючок "
         "по-русски, до 90 символов, без кавычек и эмодзи, без вопроса про подписку. "
         "Используй только факты из заголовка, ничего не добавляй. Верни только текст крючка.\n\n" + titles)
    try:
        hook = paraphrase.call_openai(q, rap.RAP_MODEL).strip().strip('"«»').splitlines()[0].strip()
        if 10 <= len(hook) <= 120:
            return hook
    except Exception as e:
        print("крючок не получился, беру строку из песни:", e)
    return lyr["items"][0][0].rstrip(",.;:")


def make_caption(day, items, lyr):
    return f"{make_hook(items, lyr)}\n\nНовости дня, {day.day} {MONTHS[day.month]}\n{CTA}"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    msk = timezone(timedelta(hours=3))
    day = (datetime.strptime(args[0], "%Y-%m-%d").date() if args else datetime.now(msk).date())
    name = f"reel_{day.isoformat()}.mp4"
    if head_status(f"{PUBLIC}/{name}") == 200 and "--force" not in sys.argv:
        sys.exit(f"ролик за {day} уже есть в R2, повторно не делаю")

    out = Path("out") / day.isoformat()
    out.mkdir(parents=True, exist_ok=True)

    items = telegram_source.collect(CHANNEL, day, 8)
    if len(items) < MIN_ITEMS:
        sys.exit(f"за {day} найдено {len(items)} новостей, нужно минимум {MIN_ITEMS}: выпуск пропущен")
    news = {"date": day.isoformat(), "items": paraphrase.paraphrase(items)}
    (out / "news_final.json").write_text(json.dumps(news, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"новостей: {len(news['items'])}", flush=True)

    lyr = rap.make_lyrics(news["items"], news["date"])
    (out / "lyrics.json").write_text(json.dumps(lyr, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in lyr.get("flags", {}).items():
        print(f"пометка к куплету {k} (не блокирует): {v}")

    mp3, ts = suno.generate(lyr, out)
    video, total = lyric_video.build(out / "news_final.json", out / "lyrics.json", mp3, ts, out / "reel.mp4")
    print(f"видео готово: {total:.1f} с", flush=True)

    if "--no-publish" in sys.argv:
        print("публикация отключена флагом --no-publish:", video)
        print("подпись была бы:\n" + make_caption(day, news["items"], lyr))
        return
    public = upload_r2(video, name)
    caption = make_caption(day, news["items"], lyr)
    print("подпись:\n" + caption)
    cid = instagram_publish.create_container(public, caption)
    instagram_publish.wait_ready(cid)
    print("опубликовано, id:", instagram_publish.publish(cid))


if __name__ == "__main__":
    main()
