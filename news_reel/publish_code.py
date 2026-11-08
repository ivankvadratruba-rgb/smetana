"""Выкладывает код завода в R2 (code/news_reel.tar.gz), чтобы автозапуск не зависел от доступа к GitHub.
Запускать после каждого изменения кода: python3 publish_code.py"""
import io
import os
import tarfile

import r2

KEEP_DIRS = {"character"}
KEEP_EXT = (".py", ".md", ".json")
SKIP = {"__pycache__", "out", "samples"}


def build():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))     # всегда из папки news_reel, откуда бы ни запустили
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in sorted(os.listdir(".")):
            if name in SKIP or name.startswith("."):
                continue
            if os.path.isfile(name) and name.endswith(KEEP_EXT):
                tar.add(name, arcname=f"news_reel/{name}")
        for f in ("character/front_closeup.png", "character/front_waist.png", "character/full_body.png",
                  "character/portrait_front_b.png", "character/portrait_three_quarter.png", "character/portrait_3q_left_a.png",
                  "character/portrait_3q_left_b.png", "character/portrait_3q_stern.png"):
            if os.path.exists(f):
                tar.add(f, arcname=f"news_reel/{f}")
    return buf.getvalue()


if __name__ == "__main__":
    data = build()
    if len(data) < 100_000:
        raise SystemExit(f"архив подозрительно мал ({len(data)} байт), в R2 не выкладываю")
    r2.put("code/news_reel.tar.gz", data, "application/gzip")
    print(f"код выложен в R2: {len(data) // 1024} КБ")
