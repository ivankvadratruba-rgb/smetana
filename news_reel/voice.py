"""Озвучка строк. По умолчанию голос onyx от OpenAI (gpt-4o-mini-tts), запасной вариант edge-tts.

Манера: быстрый жёсткий речитатив с чёткой дикцией. Темп дополнительно поднимается без смены тона.
Ключ берётся из OPENAI_API_KEY или подставляется прокси сессии.
"""
import asyncio
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

PROVIDER = os.environ.get("TTS_PROVIDER", "openai")        # openai или edge
OPENAI_VOICE = os.environ.get("OPENAI_VOICE", "onyx")
TEMPO = float(os.environ.get("TTS_TEMPO", "1.35"))          # ускорение после озвучки
EDGE_VOICE = os.environ.get("TTS_VOICE", "ru-RU-DmitryNeural")
EDGE_RATE = os.environ.get("TTS_RATE", "+20%")
INSTRUCTIONS = (
    "Говори по-русски чисто, без акцента, с правильными ударениями в каждом слове. "
    "Читай как быстрый агрессивный рэпер: плотный чёткий ритм, нажим на конец каждой строки, "
    "напористо и уверенно, без пафоса и без певучести. Между строками только короткая пауза."
)


def speakable(text):
    """Что произносим: без звёздочек и лишних символов."""
    text = text.replace("*", "")
    text = re.sub(r"[«»\"“”]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _openai_tts(text, path):
    body = json.dumps({"model": "gpt-4o-mini-tts", "voice": OPENAI_VOICE, "input": text,
                       "instructions": INSTRUCTIONS, "response_format": "mp3"}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("OPENAI_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["OPENAI_API_KEY"]
    req = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as r:
        Path(path).write_bytes(r.read())


async def _edge(text, path):
    import edge_tts
    kw = {}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        kw["proxy"] = proxy
    await edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE, **kw).save(str(path))


def _speed_up(src, dst, tempo):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                    "-af", f"atempo={tempo}", "-c:a", "libmp3lame", "-q:a", "3", str(dst)], check=True)


def synth(text, path, tries=3):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = speakable(text)
    raw = path.with_suffix(".raw.mp3")
    for i in range(tries):
        try:
            if PROVIDER == "openai":
                _openai_tts(text, raw)
                _speed_up(raw, path, TEMPO)
            else:
                asyncio.run(_edge(text, path))
            if path.exists() and path.stat().st_size > 1000:
                return duration(path)
        except Exception:
            if i == tries - 1:
                raise
    raise RuntimeError(f"озвучка не получилась: {text[:40]}")
