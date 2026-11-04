"""Перефразирует новости своими словами, чтобы не копировать формулировки автора.

Использует бесплатный тариф Gemini API. Ключ берётся из переменной окружения GEMINI_API_KEY
(в GitHub Actions это секрет). Если ключа нет в окружении, запрос уходит без него:
так работает прокси облачной сессии, который подставляет ключ сам.

Запуск через API:   python3 paraphrase.py in.json out.json
Ручной режим (например, ChatGPT по подписке, без API):
  1) python3 paraphrase.py --prompt in.json out/prompt.txt   -> вставьте prompt.txt в чат
  2) сохраните ответ чата в out/answer.json
  3) python3 paraphrase.py --apply in.json out/answer.json out.json
Модель можно сменить переменной GEMINI_MODEL.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

RULES = (
    "Ты редактор новостного клипа. Перепиши каждую новость СВОИМИ словами по-русски. "
    "Правила: сохраняй только факты из текста, ничего не добавляй и не выдумывай; "
    "не повторяй исходные формулировки дословно (меняй порядок и слова, но не смысл); "
    "заголовок до 80 знаков, спокойный нейтральный тон, без оценок и призывов; "
    "анонс до 140 знаков или пустая строка; "
    "если в тексте сказано 'утверждает', 'по словам', 'очевидцы', сохрани эту оговорку. "
    "Верни только JSON-массив той же длины и в том же порядке: "
    '[{"title": "...", "summary": "..."}, ...]'
)


def build_prompt(items):
    src = [{"title": it["title"], "summary": it.get("summary", "")} for it in items]
    return RULES + "\n\nНовости:\n" + json.dumps(src, ensure_ascii=False)


def call_gemini(prompt):
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        headers["x-goog-api-key"] = key
    req = urllib.request.Request(URL.format(m=MODEL), data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


def parse_answer(text, n):
    arr = json.loads(text)
    if not isinstance(arr, list) or len(arr) != n:
        raise ValueError(f"ожидался список из {n} элементов")
    out = []
    for a in arr:
        title = str(a.get("title", "")).strip()
        if not title:
            raise ValueError("пустой заголовок в ответе")
        out.append({"title": title[:100], "summary": str(a.get("summary", "")).strip()[:160]})
    return out


def paraphrase(items):
    new = parse_answer(call_gemini(build_prompt(items)), len(items))
    return merge(items, new)


def clean_answer(text):
    """Из ответа чата берём только JSON-массив (убираем ```json и пояснения)."""
    text = text.strip()
    a, b = text.find("["), text.rfind("]")
    if a == -1 or b == -1:
        raise ValueError("в ответе нет JSON-массива")
    return text[a:b + 1]


def merge(items, new):
    out = []
    for old, n in zip(items, new):
        merged = dict(old)
        merged.update(title=n["title"], summary=n["summary"])
        out.append(merged)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    mode = args.pop(0) if args and args[0] in ("--prompt", "--apply") else None
    if mode == "--prompt":
        src, dst = Path(args[0]), Path(args[1] if len(args) > 1 else "out/prompt.txt")
        data = json.loads(src.read_text(encoding="utf-8"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(build_prompt(data["items"]), encoding="utf-8")
        print(f"Запрос сохранён: {dst}. Вставьте его в чат и сохраните ответ в файл.")
        sys.exit(0)
    if mode == "--apply":
        src, ans, dst = Path(args[0]), Path(args[1]), Path(args[2] if len(args) > 2 else "out/news_rewritten.json")
        data = json.loads(src.read_text(encoding="utf-8"))
        new = parse_answer(clean_answer(ans.read_text(encoding="utf-8")), len(data["items"]))
        data["items"] = merge(data["items"], new)
        dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Готово:", dst)
        sys.exit(0)
    src = Path(args[0] if args else "out/news.json")
    dst = Path(args[1] if len(args) > 1 else "out/news_rewritten.json")
    data = json.loads(src.read_text(encoding="utf-8"))
    try:
        data["items"] = paraphrase(data["items"])
    except urllib.error.HTTPError as e:
        sys.exit(f"Gemini вернул {e.code}: проверьте ключ и название модели ({MODEL})")
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in data["items"]:
        print("-", it["title"])
