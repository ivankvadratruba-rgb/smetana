"""Собирает ролик целиком: карточки, голос и бит (по тактам) из новостей и текста рэпа.

Запуск: python3 make_reel.py out/news.json out/lyrics.json out/reel_full.mp4
"""
import json
import math
import subprocess
import sys
from pathlib import Path

import beat
import build_video
import cards
import voice

BPM = 92
BAR = 4 * 60 / BPM          # длина такта, секунд
LEAD, GAP, TAIL = 0.3, 0.25, 0.5


def plan_segments(news, lyr, work):
    """Список сегментов: карточка, строки, озвучка, смещения и длительность (кратна такту)."""
    items = news["items"]
    total = len(items)
    date_text = news["date"]
    segs = [{"card": cards.draw_card(0, 0, "Новости дня", date_text, date_text, counter=False),
             "lines": [lyr["intro"]]}]
    for i, (it, lines) in enumerate(zip(items, lyr["items"])):
        segs.append({"card": cards.draw_card(i, total, it["title"], it.get("summary", ""), date_text),
                     "lines": lines})
    segs.append({"card": cards.draw_card(0, 0, "До завтра", "Новости дня", date_text, counter=False),
                 "lines": [lyr["outro"]]})

    t = 0.0
    for k, s in enumerate(segs):
        pos, s["clips"] = LEAD, []
        for j, line in enumerate(s["lines"]):
            path = work / f"voice_{k:02d}_{j}.mp3"
            d = voice.synth(line, path)
            s["clips"].append((path, t + pos))
            pos += d + GAP
        s["dur"] = math.ceil((pos - GAP + TAIL) / BAR) * BAR
        t += s["dur"]
    return segs, t


def mix_audio(segs, total, work, beat_vol=0.35):
    beat_path = beat.render(total + 0.5, bpm=BPM, path=str(work / "beat.wav"))
    clips = [c for s in segs for c in s["clips"]]
    inputs = ["-i", beat_path]
    for p, _ in clips:
        inputs += ["-i", str(p)]
    f = [f"[0:a]volume={beat_vol}[b]"]
    for k, (_, at) in enumerate(clips, start=1):
        ms = int(at * 1000)
        f.append(f"[{k}:a]adelay={ms}|{ms},volume=1.6[v{k}]")
    labels = "[b]" + "".join(f"[v{k}]" for k in range(1, len(clips) + 1))
    f.append(f"{labels}amix=inputs={len(clips) + 1}:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100,pan=stereo|c0=c0|c1=c0[a]")
    out = work / "mix.m4a"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(f), "-map", "[a]", "-t", f"{total:.3f}",
                    "-c:a", "aac", "-b:a", "192k", str(out)], check=True)
    return out


def make(news_path, lyrics_path, out_path):
    news = json.loads(Path(news_path).read_text(encoding="utf-8"))
    lyr = json.loads(Path(lyrics_path).read_text(encoding="utf-8"))
    work = Path(out_path).parent
    work.mkdir(parents=True, exist_ok=True)
    segs, total = plan_segments(news, lyr, work)
    pngs = []
    for k, s in enumerate(segs):
        p = work / "cards" / f"seg_{k:02d}.png"
        p.parent.mkdir(exist_ok=True)
        s["card"].save(p)
        pngs.append(p)
    audio = mix_audio(segs, total, work)
    build_video.assemble(pngs, [s["dur"] for s in segs], audio, out_path)
    return out_path, total


if __name__ == "__main__":
    a = sys.argv[1:]
    out, total = make(a[0] if a else "out/news.json", a[1] if len(a) > 1 else "out/lyrics.json",
                      a[2] if len(a) > 2 else "out/reel_full.mp4")
    print(f"{out}: {total:.1f} с")
