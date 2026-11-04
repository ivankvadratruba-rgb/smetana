"""Собирает вертикальный ролик 1080x1920 из карточек и звука.

Запуск: python3 build_video.py [news.json] [out/reel.mp4] [audio.wav]
Если звук не указан, делается тестовый бит из beat.py.
"""
import json
import subprocess
import sys
from pathlib import Path

import beat
import cards

SEC_PER_CARD = 3.5
FPS = 30


def build(news_path, out_path, audio_path=None):
    news = json.loads(Path(news_path).read_text(encoding="utf-8"))
    work = Path(out_path).parent
    work.mkdir(parents=True, exist_ok=True)
    pngs = cards.make_cards(news, work / "cards")
    total = SEC_PER_CARD * len(pngs)

    if audio_path is None:
        audio_path = beat.render(total + 0.5, path=str(work / "beat.wav"))

    inputs, filters = [], []
    for i, p in enumerate(pngs):
        inputs += ["-loop", "1", "-t", str(SEC_PER_CARD), "-i", str(p)]
        frames = int(SEC_PER_CARD * FPS)
        # Лёгкое приближение, чтобы кадр жил
        filters.append(
            f"[{i}:v]scale=1080:1920,zoompan=z='min(zoom+0.0008,1.12)':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={FPS},setsar=1[v{i}]"
        )
    chain = "".join(f"[v{i}]" for i in range(len(pngs)))
    filters.append(f"{chain}concat=n={len(pngs)}:v=1:a=0[v]")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-i", str(audio_path),
           "-filter_complex", ";".join(filters),
           "-map", "[v]", "-map", f"{len(pngs)}:a",
           "-t", str(total), "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)]
    subprocess.run(cmd, check=True)
    return out_path


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "sample_news.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "out/reel.mp4"
    aud = sys.argv[3] if len(sys.argv) > 3 else None
    print(build(src, out, aud))
