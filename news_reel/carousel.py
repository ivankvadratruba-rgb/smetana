"""Карусель Instagram из кадров героя (сценарий «эфир плюс роли по новостям»).

Слайды 1080x1350 (4:5): обложка с крючком и по слайду на каждую новость с куплетом. Призыв к подписке только в подписи.
Запуск: python3 carousel.py ДАТА            собрать слайды и выложить в R2, ничего не публиковать
        python3 carousel.py ДАТА --publish  опубликовать карусель (только по команде владельца)
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import daily_run
import hero_run
import instagram_publish as ig
import lyric_video
import r2

W, H = 1080, 1350
BOLD = lyric_video.FONT_BOLD


def cover(img):
    """Кадр героя под размер 4:5: по ширине, обрезка снизу (лицо у героя вверху кадра)."""
    img = img.convert("RGB")
    k = W / img.width
    img = img.resize((W, int(img.height * k)), Image.LANCZOS)
    top = int((img.height - H) * 0.12) if img.height > H else 0
    return img.crop((0, top, W, top + H))


def gradient(img, share=0.5, strength=205):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    top = int(H * (1 - share))
    for y in range(top, H):
        a = int(strength * ((y - top) / (H - top)) ** 1.4)
        d.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    return Image.alpha_composite(img.convert("RGBA"), ov)


def text_block(img, lines, bottom=70, sizes=(56, 50, 44), color=(255, 255, 255, 255)):
    d = ImageDraw.Draw(img)
    for size in sizes:
        font = ImageFont.truetype(BOLD, size)
        rows = [r for ln in lines for r in lyric_video.wrap(d, ln, font, W - 140)]
        if len(rows) <= 5:
            break
    step = int(size * 1.3)
    y = H - bottom - step * len(rows)
    for r in rows:
        w = d.textlength(r, font=font)
        d.text(((W - w) / 2, y), r, font=font, fill=color, stroke_width=2, stroke_fill=(0, 0, 0, 200))
        y += step
    return img


def label(img, date_text):
    d = ImageDraw.Draw(img)
    d.ellipse((46, 46, 66, 66), fill=(230, 30, 40, 255))
    y_, m_, d_ = date_text[:4], date_text[5:7], date_text[8:10]
    d.text((80, 38), f"Эфир · Новости в треке · {d_}.{m_}.{y_}", font=ImageFont.truetype(BOLD, 32), fill=(255, 255, 255, 235), stroke_width=2, stroke_fill=(0, 0, 0, 160))
    return img


def red_box(img, text, y_center, size=84):
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(BOLD, size)
    rows = lyric_video.wrap(d, text, font, W - 200)
    step = int(size * 1.2)
    h = 52 + step * len(rows)
    y0 = int(y_center - h / 2)
    d.rounded_rectangle((50, y0, W - 50, y0 + h), radius=26, fill=(205, 25, 35, 240))
    y = y0 + 28
    for r in rows:
        w = d.textlength(r, font=font)
        d.text(((W - w) / 2, y), r, font=font, fill=(255, 255, 255, 255))
        y += step
    return img


def build_slides(work, day):
    news = json.loads((work / "news_final.json").read_text(encoding="utf-8"))
    lyr = json.loads((work / "lyrics.json").read_text(encoding="utf-8"))
    hook = hero_run.stage_hook(day, work)
    n = len(news["items"])
    slides = []
    # 1. обложка: студия и крючок
    im = gradient(cover(Image.open(work / "frame_00.png")), 0.45, 170)
    label(im, news["date"]); red_box(im, hook["text"], H - 430)
    d = ImageDraw.Draw(im); f = ImageFont.truetype(BOLD, 34)
    d.text((W - 230, H - 80), "Листай  ›", font=f, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 200))
    slides.append(im)
    # 2..n+1. новости
    for i in range(n):
        im = gradient(cover(Image.open(work / f"frame_{i + 1:02d}.png")))
        label(im, news["date"]); text_block(im, lyr["items"][i])
        slides.append(im)
    out = []
    for i, s in enumerate(slides, 1):
        p = work / f"slide_{i:02d}.jpg"
        s.convert("RGB").save(p, quality=92)
        out.append(p)
    return out, hook, news


def make_caption(day, news, hook):
    mon = daily_run.MONTHS[day.month]
    bullets = "\n".join("• " + it["title"].rstrip(".") for it in news["items"])
    first = news["items"][hook["index"]]["title"].rstrip(".")      # заголовок самой цепляющей новости, с правильными заглавными
    return (f"{first}\n\nНовости дня, {day.day} {mon}\n\n{bullets}\n\n"
            "Листай, в каждой новости свой куплет. Подпишись, чтобы не пропустить завтрашний выпуск.\n\n"
            "#новости #рэпновости #андеграунд #город")[:2200]


def run(day, publish=False, cfg=None):
    work = Path("out") / f"hero_{day.isoformat()}"
    paths, hook, news = build_slides(work, day)
    urls = []
    for i, p in enumerate(paths, 1):
        name = f"carousel_{day.isoformat()}_{i}.jpg"
        r2.put(name, p.read_bytes(), "image/jpeg")
        urls.append(f"{daily_run.PUBLIC}/{name}")
    caption = make_caption(day, news, hook)
    print("слайдов:", len(paths))
    print("подпись:\n" + caption)
    if not publish:
        print("публикация не выполнена (нужен флаг --publish)")
        return paths, caption
    items = []
    for u in urls:
        c = ig.create_carousel_item(u)
        ig.wait_ready(c)
        items.append(c)
    cid = ig.create_carousel(items, caption)
    ig.wait_ready(cid)
    mid = ig.publish(cid)
    print("карусель опубликована, id:", mid)
    try:    # запись в историю: формат carousel, чтобы чередование и дубли считались по формату
        import style
        h = style.load_history()
        h["posts"].append({"date": day.isoformat(), "media_id": mid, "format": "carousel",
                           "style": cfg or dict(style.DEFAULT, name="карусель героя", variable="формат: карусель"),
                           "published_at": datetime.now().astimezone().isoformat()})
        style.save_history(h)
    except Exception as e:
        print("история не обновлена:", e)
    return paths, caption, mid


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if not a:
        sys.exit(__doc__)
    run(datetime.strptime(a[0], "%Y-%m-%d").date(), "--publish" in sys.argv)
