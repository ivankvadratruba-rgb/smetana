"""Генерирует песню по тексту в Suno (api.sunoapi.org) и получает метки времени слов.

Ключ подставляет прокси сессии или берётся из SUNO_API_KEY; в репозиторий ключ не кладётся.

Запуск: python3 suno.py lyrics.json out_dir
Результат: out_dir/suno_1.mp3 и out_dir/suno_1_ts.json
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import align

API = "https://api.sunoapi.org/api/v1"
MODEL = os.environ.get("SUNO_MODEL", "V4_5")
STYLE = os.environ.get("SUNO_STYLE", "dark russian boom bap rap, 96 BPM, melancholic, male vocal, "
                                      "minor key, sparse piano and vinyl crackle, calm delivery")
TITLE = "Новости дня"


def call(path, data=None):
    headers = {"Content-Type": "application/json"}
    if os.environ.get("SUNO_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["SUNO_API_KEY"]
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(f"{API}/{path}", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Suno вернул {e.code}: {e.read().decode()[:300]}")
    if d.get("code") != 200:
        raise RuntimeError(f"Suno: {d}")
    return d["data"]


def generate(lyr, out_dir, timeout=900):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    text, _ = align.build_song_text(lyr)
    task = call("generate", {"customMode": True, "instrumental": False, "model": MODEL, "prompt": text,
                             "style": STYLE, "title": TITLE,
                             "callBackUrl": "https://example.com/suno-callback"})["taskId"]
    t = time.time()
    track = None
    while time.time() - t < timeout:
        time.sleep(15)
        info = call(f"generate/record-info?taskId={task}") or {}
        status = info.get("status")
        print("Suno:", status, flush=True)
        if status in ("CREATE_TASK_FAILED", "GENERATE_AUDIO_FAILED", "CALLBACK_EXCEPTION", "SENSITIVE_WORD_ERROR"):
            raise RuntimeError(f"Suno не сгенерировал песню: {status}")
        tracks = (info.get("response") or {}).get("sunoData") or []
        if status == "SUCCESS" and tracks and tracks[0].get("audioUrl"):
            track = tracks[0]
            break
    if not track:
        raise RuntimeError("таймаут генерации песни")
    mp3 = out / "suno_1.mp3"
    with urllib.request.urlopen(track["audioUrl"], timeout=120) as r:
        mp3.write_bytes(r.read())
    ts = call("generate/get-timestamped-lyrics", {"taskId": task, "audioId": track["id"]})
    (out / "suno_1_ts.json").write_text(json.dumps({"code": 200, "msg": "success", "data": ts},
                                                   ensure_ascii=False), encoding="utf-8")
    return mp3, out / "suno_1_ts.json"


if __name__ == "__main__":
    lyr = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(*generate(lyr, sys.argv[2]))
