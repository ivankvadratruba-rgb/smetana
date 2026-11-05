"""Чтение и запись небольших файлов в R2 (подпись S3 через curl). Ключи берутся из окружения."""
import os
import subprocess
import tempfile

BUCKET = os.environ.get("R2_BUCKET", "vtreke-reels")


def _curl(args):
    user = f"{os.environ['R2_ACCESS_KEY_ID']}:{os.environ['R2_SECRET_ACCESS_KEY']}"
    return subprocess.run(["curl", "-sS", "--aws-sigv4", "aws:amz:auto:s3", "--user", user, *args],
                          capture_output=True, text=True)


def _url(name):
    return f"{os.environ['R2_ENDPOINT'].rstrip('/')}/{BUCKET}/{name}"


def get(name):
    """Содержимое файла или None, если файла нет."""
    with tempfile.NamedTemporaryFile() as f:
        r = _curl(["-o", f.name, "-w", "%{http_code}", _url(name)])
        if r.stdout.strip() == "404":
            return None
        if r.stdout.strip() != "200":
            raise RuntimeError(f"чтение {name} из R2 не удалось: {r.stdout} {r.stderr[:200]}")
        return open(f.name, "rb").read()


def put(name, data, content_type="application/json"):
    with tempfile.NamedTemporaryFile() as f:
        f.write(data)
        f.flush()
        r = _curl(["-o", "/dev/null", "-w", "%{http_code}", "-X", "PUT", "-H", f"Content-Type: {content_type}",
                   "-T", f.name, _url(name)])
    if r.stdout.strip() != "200":
        raise RuntimeError(f"запись {name} в R2 не удалась: {r.stdout} {r.stderr[:200]}")
