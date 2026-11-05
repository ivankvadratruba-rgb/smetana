"""Публикация Reel в Instagram через Graph API (аккаунт vtreke.news).

По умолчанию ничего не публикует: только создаёт контейнер и проверяет, принял ли Instagram видео.
Публикация только с флагом --publish.

Запуск:
  python3 instagram_publish.py VIDEO_URL "подпись" [--publish]

VIDEO_URL: публичная прямая ссылка на mp4 (Instagram сам забирает файл). Токен подставляет прокси сессии
или берётся из INSTAGRAM_ACCESS_TOKEN; в репозиторий токен не кладётся.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://graph.facebook.com/v21.0"
IG_ID = os.environ.get("INSTAGRAM_ACCOUNT_ID", "17841426148053060")


def call(path, data=None):
    url = f"{API}/{path}"
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if token:
        data = dict(data or {}, access_token=token)
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Instagram вернул {e.code}: {e.read().decode()[:400]}")


def create_container(video_url, caption):
    return call(f"{IG_ID}/media", {"media_type": "REELS", "video_url": video_url,
                                   "caption": caption, "share_to_feed": "true"})["id"]


def create_story_container(video_url):
    """Сторис с видео (до 60 секунд). Подписи у сторис нет."""
    return call(f"{IG_ID}/media", {"media_type": "STORIES", "video_url": video_url})["id"]


def wait_ready(cid, timeout=600):
    t = time.time()
    while time.time() - t < timeout:
        st = call(f"{cid}?fields=status_code,status")
        code = st.get("status_code")
        print("статус:", code, st.get("status", ""), flush=True)
        if code == "FINISHED":
            return True
        if code in ("ERROR", "EXPIRED"):
            raise SystemExit(f"Instagram не принял видео: {st}")
        time.sleep(10)
    raise SystemExit("таймаут ожидания обработки видео")


def publish(cid):
    return call(f"{IG_ID}/media_publish", {"creation_id": cid})["id"]


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        sys.exit(__doc__)
    cid = create_container(args[0], args[1])
    print("контейнер создан:", cid)
    wait_ready(cid)
    if "--publish" in sys.argv:
        print("опубликовано, id:", publish(cid))
    else:
        print("видео принято. Публикация не выполнена (нужен флаг --publish).")
