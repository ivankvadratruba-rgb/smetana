"""Озвучка строк бесплатным голосом edge-tts (русский мужской голос).

В облачной сессии интернет идёт через прокси, поэтому прокси берётся из HTTPS_PROXY.
"""
import asyncio
import os
import re
import subprocess
from pathlib import Path

import edge_tts

VOICE = os.environ.get("TTS_VOICE", "ru-RU-DmitryNeural")
RATE = os.environ.get("TTS_RATE", "+20%")


def speakable(text):
    """Что произносим: без звёздочек и лишних символов."""
    text = text.replace("*", "")
    text = re.sub(r"[«»\"“”]", "", text)
    return re.sub(r"\s+", " ", text).strip()


async def _save(text, path):
    kw = {}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        kw["proxy"] = proxy
    await edge_tts.Communicate(speakable(text), VOICE, rate=RATE, **kw).save(str(path))


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def synth(text, path, tries=3):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(tries):
        try:
            asyncio.run(_save(text, path))
            if path.exists() and path.stat().st_size > 1000:
                return duration(path)
        except Exception:
            if i == tries - 1:
                raise
    raise RuntimeError(f"озвучка не получилась: {text[:40]}")
