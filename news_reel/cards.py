"""Рисует вертикальные карточки 1080x1920 из заголовков новостей.

Только свои карточки: текст на градиентном фоне. Чужие фото не используются.
"""
import json
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Пары цветов фона (сверху вниз), по кругу
PALETTES = [
    ((20, 24, 60), (120, 40, 140)),
    ((10, 50, 70), (20, 140, 120)),
    ((60, 20, 30), (200, 80, 60)),
    ((25, 25, 25), (90, 90, 110)),
]


def gradient(top, bottom):
    img = Image.new("RGB", (W, H), top)
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        row = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(W):
            px[x, y] = row
    return img


def draw_card(index, total, title, summary, date_text):
    top, bottom = PALETTES[index % len(PALETTES)]
    img = gradient(top, bottom)
    d = ImageDraw.Draw(img)
    f_small = ImageFont.truetype(FONT_REG, 44)
    f_title = ImageFont.truetype(FONT_BOLD, 84)
    f_sum = ImageFont.truetype(FONT_REG, 52)

    d.text((70, 90), f"НОВОСТИ ДНЯ  {date_text}", font=f_small, fill=(255, 255, 255))
    d.text((70, H - 130), f"{index + 1} из {total}", font=f_small, fill=(255, 255, 255))

    y = 420
    for line in textwrap.wrap(title, width=18):
        d.text((70, y), line, font=f_title, fill=(255, 255, 255))
        y += 105
    y += 50
    for line in textwrap.wrap(summary, width=30):
        d.text((70, y), line, font=f_sum, fill=(230, 230, 235))
        y += 68
    return img


def make_cards(news, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = news["items"]
    paths = []
    for i, it in enumerate(items):
        img = draw_card(i, len(items), it["title"], it.get("summary", ""), news["date"])
        p = out_dir / f"card_{i + 1:02d}.png"
        img.save(p)
        paths.append(p)
    return paths


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "sample_news.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "out/cards"
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    for p in make_cards(data, out):
        print(p)
