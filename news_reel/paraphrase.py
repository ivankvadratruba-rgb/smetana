"""Перефразирует новости своими словами, чтобы не копировать формулировки автора.

Провайдер выбирается переменной PARAPHRASE_PROVIDER: openai (по умолчанию) или gemini.
Ключи: OPENAI_API_KEY или GEMINI_API_KEY (в GitHub Actions это секреты). Если ключа нет в окружении,
запрос уходит без него: так работает прокси облачной сессии, который подставляет ключ сам.
Модели меняются переменными OPENAI_MODEL и GEMINI_MODEL.

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

PROVIDER = os.environ.get("PARAPHRASE_PROVIDER", "openai")  # openai или gemini
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

RULES = (
    "Ты редактор новостного клипа. Напиши каждую новость ЗАНОВО своими словами по-русски. "
    "Правила: используй только факты из текста, ничего не добавляй и не выдумывай; "
    "заголовок построй иначе, чем в исходнике: другой порядок слов, другие глаголы и существительные, "
    "но тот же смысл; не копируй из исходника ни одного предложения целиком; "
    "заголовок до 80 знаков; анонс это ОДНО короткое предложение до 100 знаков своими словами "
    "(или пустая строка); спокойный нейтральный тон, без оценок и призывов; "
    "оговорки 'утверждает', 'по словам', 'очевидцы', 'предположительно' обязательно сохрани; "
    "ничего не заменяй по смыслу: если в исходнике 'девушка', 'правые', 'неслучайной', не меняй это на другое; "
    "ВАЖНО: звёздочки '*' после имён и названий и пометки вроде 'признан иноагентом' "
    "переноси без изменений, не убирай и не добавляй. "
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


def call_openai(prompt, model=None):
    model = model or OPENAI_MODEL
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if model.startswith("gpt-5"):
        payload["reasoning_effort"] = os.environ.get("OPENAI_REASONING", "low")  # быстрее и без обрывов
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(OPENAI_URL, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


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


import difflib


def sim(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def problems(old, new):
    """Что не так с одной перефразированной новостью (пустой список = всё в порядке)."""
    bad = []
    src = old["title"] + " " + old.get("summary", "")
    if sim(old["title"], new["title"]) > 0.6:
        bad.append("заголовок слишком похож на исходный")
    if new["summary"] and sim(old.get("summary", "") or src, new["summary"]) > 0.5:
        bad.append("анонс слишком похож на исходный")
    if src.count("*") != (new["title"] + new["summary"]).count("*"):
        bad.append("потеряны или добавлены звёздочки *")
    return bad


def paraphrase(items, retries=2):
    call = call_openai if PROVIDER == "openai" else call_gemini
    new = parse_answer(clean_answer(call(build_prompt(items))), len(items))
    for _ in range(retries):
        bad = {i: problems(o, n) for i, (o, n) in enumerate(zip(items, new)) if problems(o, n)}
        if not bad:
            break
        idx = list(bad)
        notes = "; ".join(f"№{k + 1}: {', '.join(v)}" for k, v in bad.items())
        again = parse_answer(clean_answer(call(build_prompt([items[i] for i in idx]) +
                             "\n\nПредыдущая попытка не годится (" + notes + "). Перепиши заново сильнее.")), len(idx))
        for i, n in zip(idx, again):
            new[i] = n
    # то, что всё равно слишком похоже, не оставляем: анонс убираем, а заголовок помечаем
    flagged = []
    for i, (o, n) in enumerate(zip(items, new)):
        b = problems(o, n)
        if "анонс слишком похож на исходный" in b:
            n["summary"] = ""
        if b:
            flagged.append((i + 1, b))
    for k, b in flagged:
        print(f"ВНИМАНИЕ №{k}: {', '.join(b)}", file=sys.stderr)
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
        sys.exit(f"{PROVIDER} вернул {e.code}: проверьте ключ и название модели "
                 f"({OPENAI_MODEL if PROVIDER == 'openai' else MODEL})")
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in data["items"]:
        print("-", it["title"])
