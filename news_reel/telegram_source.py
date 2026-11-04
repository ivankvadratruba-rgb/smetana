"""Читает публичный Telegram-канал через веб-версию t.me/s/<канал> и собирает новости за день.

Запуск: python3 telegram_source.py [канал] [выходной.json] [максимум_новостей] [дата ГГГГ-ММ-ДД]
Только чтение открытой страницы. Тексты принадлежат авторам канала: использование на свой риск.
"""
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
UA = "Mozilla/5.0 (compatible; news-reel-prototype)"
AD_MARKERS = ("#реклама", "реклама.", "erid", "на правах рекламы")


def fetch(channel, before=None):
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def clean(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def parse_page(page):
    posts = []
    chunks = re.split(r'(?=<div class="tgme_widget_message_wrap)', page)
    for ch in chunks:
        post_id = re.search(r'data-post="[^/"]+/(\d+)"', ch)
        when = re.search(r'<time[^>]*datetime="([^"]+)"', ch)
        body = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', ch, re.S)
        if not (post_id and when and body):
            continue
        posts.append({
            "id": int(post_id.group(1)),
            "time": datetime.fromisoformat(when.group(1)).astimezone(MSK),
            "text": clean(body.group(1)),
        })
    return posts


def split_title(text, limit=110):
    first = re.split(r"(?<=[.!?])\s|\n", text, maxsplit=1)
    title = first[0].strip()
    rest = first[1].strip() if len(first) > 1 else ""
    if len(title) > limit:
        cut = title[:limit].rsplit(" ", 1)[0]
        title, rest = cut.rstrip(" ,;:—-") + "…", ""
    return title, rest


def collect(channel, day, max_items=8, max_pages=6):
    """Новости за календарный день day (по Москве), от новых к старым, потом разворачиваем."""
    found, before = [], None
    for _ in range(max_pages):
        posts = parse_page(fetch(channel, before))
        if not posts:
            break
        for p in posts:
            if p["time"].date() == day:
                found.append(p)
        oldest = min(posts, key=lambda p: p["id"])
        if oldest["time"].date() < day:
            break
        before = oldest["id"]
    seen, items = set(), []
    for p in sorted(found, key=lambda p: p["id"]):
        low = p["text"].lower()
        if len(p["text"]) < 40 or any(m in low for m in AD_MARKERS) or p["id"] in seen:
            continue
        seen.add(p["id"])
        title, summary = split_title(p["text"])
        items.append({"title": title, "summary": summary[:220].rstrip(),
                      "time": p["time"].strftime("%H:%M"), "post": p["id"]})
    return items[-max_items:]


if __name__ == "__main__":
    channel = sys.argv[1] if len(sys.argv) > 1 else "ostorozhno_novosti"
    out = sys.argv[2] if len(sys.argv) > 2 else "out/news.json"
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    day = (datetime.strptime(sys.argv[4], "%Y-%m-%d").date() if len(sys.argv) > 4
           else datetime.now(MSK).date())
    items = collect(channel, day, limit)
    from pathlib import Path
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"date": day.isoformat(), "items": items},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{day}: {len(items)} новостей -> {out}")
    for it in items:
        print(f"  {it['time']}  {it['title']}")
