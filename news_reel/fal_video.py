"""Видео «персонаж читает трек» через fal.ai (по умолчанию bytedance/omnihuman/v2).

Вход: картинка персонажа и аудио (wav или mp3, 4-300 секунд, до 15 МБ). Выход: mp4.
Цена модели: 0,105 доллара за секунду; каждый вызов платный, поэтому есть лимит секунд на вызов.

Ключ FAL_KEY берётся из окружения или из файла /home/user/.fal.env (вне репозитория, в коммит не попадает).
Запуск: python3 fal_video.py avatar.png vocal.mp3 out.mp4 [--go]
Без --go только считает ориентировочную стоимость и ничего не отправляет.
"""
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

MODEL = os.environ.get("FAL_AVATAR_MODEL", "bytedance/omnihuman/v2")
PRICE_PER_SEC = float(os.environ.get("FAL_PRICE_PER_SEC", "0.105"))
MAX_SECONDS = float(os.environ.get("FAL_MAX_SECONDS", "40"))      # потолок длины одного вызова
ENV_FILE = os.environ.get("FAL_ENV_FILE", "/home/user/.fal.env")


def load_key():
    if os.environ.get("FAL_KEY"):
        return
    p = Path(ENV_FILE)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("FAL_KEY="):
                os.environ["FAL_KEY"] = line.split("=", 1)[1].strip().strip('"')
                return
    raise RuntimeError("нет FAL_KEY: нужен ключ в окружении или в " + ENV_FILE)


def audio_seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout.strip()
    return float(out)


def estimate(audio):
    sec = audio_seconds(audio)
    return sec, sec * PRICE_PER_SEC


def avatar_clip(image, audio, out, prompt=None, go=False):
    """Оживляет картинку под аудио. Платный вызов, без go=True ничего не отправляет."""
    sec, cost = estimate(audio)
    if sec > MAX_SECONDS:
        raise ValueError(f"аудио {sec:.0f} с длиннее лимита {MAX_SECONDS:.0f} с (FAL_MAX_SECONDS); режьте на части")
    print(f"длина {sec:.1f} с, ориентировочно {cost:.2f} доллара")
    if not go:
        print("пробный режим: ничего не отправлено (нужен --go)")
        return None
    import fal_client
    load_key()
    args = {"image_url": fal_client.upload_file(str(image)), "audio_url": fal_client.upload_file(str(audio))}
    if prompt:
        args["prompt"] = prompt
    result = fal_client.subscribe(MODEL, arguments=args)    # один вызов = одно платное задание, не повторять вслепую
    url = result["video"]["url"]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "smetana-news-reel/1.0"}),
                                timeout=180) as r:
        Path(out).write_bytes(r.read())
    print(f"готово: {out}, оплачено секунд: {result.get('duration')}")
    return out


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(a) < 3:
        sys.exit(__doc__)
    avatar_clip(a[0], a[1], a[2], go="--go" in sys.argv)
