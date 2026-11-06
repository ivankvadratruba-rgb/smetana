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
import stats
import style
import suno
import telegram_source

BUCKET = os.environ.get("R2_BUCKET", "vtreke-reels")
PUBLIC = os.environ.get("R2_PUBLIC_URL", "https://pub-ede07af251c94a0e8b12d5e08e0f9e44.r2.dev")
CHANNEL = os.environ.get("NEWS_CHANNEL", "ostorozhno_novosti")
MIN_ITEMS = 4
MONTHS = rap.MONTHS


def head_status(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": suno.UA})
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


def lyrics_text(lyr):
    """Текст песни для подписи: вступление, куплеты по новостям (с пустой строкой между ними), концовка."""
    blocks = [lyr["intro"]] + ["\n".join(r) for r in lyr["items"]] + [lyr["outro"]]
    return "\n\n".join(blocks)


def make_caption(day, lyr, cfg=None):
    """Дата, текст трека, призыв к подписке, хэштеги. Лимит Instagram на подпись: 2200 символов."""
    cfg = cfg or style.DEFAULT
    tail = f"\n\n{cfg['cta']}" + (f"\n\n{cfg['hashtags']}" if cfg.get("hashtags") else "")
    head = f"Новости дня, {day.day} {MONTHS[day.month]}\n\n"
    room = 2200 - len(head) - len(tail)
    return head + lyrics_text(lyr)[:room].rstrip() + tail


def retry(fn, tries=3, pause=20):
    """Повторяет шаг при сбое сети или сервиса; после последней попытки ошибка пробрасывается."""
    import time
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            print(f"сбой шага {getattr(fn, '__name__', 'шаг')}: {e} (попытка {i + 1} из {tries})", flush=True)
            if i == tries - 1:
                raise
            time.sleep(pause)


STORY_MAX = 59.0     # сторис через API: каждое видео не длиннее 60 секунд


def split_video(video, parts=2):
    """Делит видео на равные части по времени (минимум 2, больше, если часть выйдет длиннее лимита сторис)."""
    video = Path(video)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(video)], capture_output=True, text=True).stdout.strip())
    n = max(parts, int(dur // STORY_MAX) + (1 if dur % STORY_MAX else 0))
    step = dur / n
    out = []
    for i in range(n):
        dst = video.with_name(f"story_{i + 1}.mp4")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{i * step:.3f}",
                        "-i", str(video), "-t", f"{step:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", str(dst)], check=True)
        out.append(dst)
    return out


def post_story(video, name):
    """Публикует ролик в сторис двумя частями подряд. Возвращает список id сторис."""
    ids = []
    for i, part in enumerate(split_video(video), 1):
        url = retry(lambda: upload_r2(part, f"story{i}_" + name))

        def container():
            c = instagram_publish.create_story_container(url)
            instagram_publish.wait_ready(c)
            return c
        ids.append(instagram_publish.publish(retry(container)))
    return ids


def main():
    args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] != "--trends"]
    msk = timezone(timedelta(hours=3))
    day = (datetime.strptime(args[0], "%Y-%m-%d").date() if args else datetime.now(msk).date())
    name = f"reel_{day.isoformat()}.mp4"
    if "--force" in sys.argv:       # перевыпуск за ту же дату: новое имя файла, прежний не затираем
        name = f"reel_{day.isoformat()}_{datetime.now(msk).strftime('%H%M')}.mp4"

    out = Path("out") / day.isoformat()
    out.mkdir(parents=True, exist_ok=True)

    trends = ""
    if "--trends" in sys.argv:
        tf = Path(sys.argv[sys.argv.index("--trends") + 1])
        trends = tf.read_text(encoding="utf-8") if tf.exists() else ""
    hist = retry(style.load_history)
    if any(p["date"] == day.isoformat() for p in hist["posts"]) and "--force" not in sys.argv:
        sys.exit(f"рилс за {day} уже опубликован, повторно не делаю")
    style.refresh_stats(hist)
    cfg = style.next_style(hist, trends, day)
    style.apply(cfg)
    print(f"фаза: {style.phase(day, hist)}\nстиль дня: {cfg['name']}\nпеременная: {cfg.get('variable')}\nгипотеза: {cfg.get('hypothesis')}\nпочему: {cfg['rationale']}", flush=True)

    items = retry(lambda: telegram_source.collect(CHANNEL, day, cfg.get("news_count", 8)))
    if len(items) < MIN_ITEMS:
        sys.exit(f"за {day} найдено {len(items)} новостей, нужно минимум {MIN_ITEMS}: выпуск пропущен")
    news = {"date": day.isoformat(), "items": paraphrase.paraphrase(items)}
    (out / "news_final.json").write_text(json.dumps(news, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"новостей: {len(news['items'])}", flush=True)

    lyr = rap.make_lyrics(news["items"], news["date"])
    (out / "lyrics.json").write_text(json.dumps(lyr, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in lyr.get("flags", {}).items():
        print(f"пометка к куплету {k} (не блокирует): {v}")

    mp3, ts = retry(lambda: suno.generate(lyr, out), tries=2, pause=30)
    video, total = lyric_video.build(out / "news_final.json", out / "lyrics.json", mp3, ts, out / "reel.mp4")
    print(f"видео готово: {total:.1f} с", flush=True)

    if "--no-publish" in sys.argv:
        print("публикация отключена флагом --no-publish:", video)
        print("подпись была бы:\n" + make_caption(day, lyr, cfg))
        return
    public = retry(lambda: upload_r2(video, name))
    caption = make_caption(day, lyr, cfg)
    print("подпись:\n" + caption)
    def container():
        c = instagram_publish.create_container(public, caption)
        instagram_publish.wait_ready(c)
        return c
    cid = retry(container)
    media_id = instagram_publish.publish(cid)
    print("опубликовано, id:", media_id)
    hist["posts"].append({"date": day.isoformat(), "media_id": media_id, "style": cfg,
                          "published_at": datetime.now(timezone.utc).isoformat()})
    style.save_history(hist)
    try:    # сторис не должна ломать выпуск: рилс уже опубликован и записан в историю
        hist["posts"][-1]["story_ids"] = post_story(video, name)
        print("сторис опубликованы, id:", hist["posts"][-1]["story_ids"])
        style.save_history(hist)
    except Exception as e:
        print("сторис не опубликована:", e)


if __name__ == "__main__":
    try:
        main()
    finally:
        import ledger
        ledger.flush()
