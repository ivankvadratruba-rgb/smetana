"""Перефразирует новости своими словами, чтобы не копировать формулировки автора.

Использует бесплатный тариф Gemini API. Ключ берётся из переменной окружения GEMINI_API_KEY
(в GitHub Actions это секрет). Если ключа нет в окружении, запрос уходит без него:
так работает прокси облачной сессии, который подставляет ключ сам.

Запуск: python3 paraphrase.py in.json out.json
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
    result = []
    for old, n in zip(items, new):
        merged = dict(old)
        merged.update(title=n["title"], summary=n["summary"])
        result.append(merged)
    return result


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "out/news.json")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "out/news_rewritten.json")
    data = json.loads(src.read_text(encoding="utf-8"))
    try:
        data["items"] = paraphrase(data["items"])
    except urllib.error.HTTPError as e:
        sys.exit(f"Gemini вернул {e.code}: проверьте ключ и название модели ({MODEL})")
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in data["items"]:
        print("-", it["title"])
