"""Учёт расходов: токены OpenAI по моделям и расход fal. Хранится в R2 (state/usage.json) по месяцам.
Нужен дашборду, потому что баланс и расход через API без административных ключей не читаются."""
import json
import threading
from datetime import datetime, timezone

import r2

_lock = threading.Lock()
_pending = {"openai": {}, "fal": {}}
KEY = "state/usage.json"


def openai(model, usage):
    """usage из ответа OpenAI: prompt_tokens/completion_tokens (чат) или input_tokens/output_tokens (картинки)."""
    if not usage:
        return
    with _lock:
        m = _pending["openai"].setdefault(model, {"calls": 0, "in": 0, "out": 0})
        m["calls"] += 1
        m["in"] += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        m["out"] += int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)


def fal(item, units, usd):
    with _lock:
        m = _pending["fal"].setdefault(item, {"units": 0.0, "usd": 0.0})
        m["units"] += float(units or 0)
        m["usd"] += float(usd or 0)


def flush():
    """Складывает накопленное в R2. Ошибка учёта не должна ломать выпуск."""
    try:
        with _lock:
            if not any(_pending.values()) or not (_pending["openai"] or _pending["fal"]):
                return
            snap = json.loads(json.dumps(_pending))
            _pending["openai"].clear(); _pending["fal"].clear()
        raw = r2.get(KEY)
        data = json.loads(raw) if raw else {"months": {}}
        mon = data["months"].setdefault(datetime.now(timezone.utc).strftime("%Y-%m"), {"openai": {}, "fal": {}})
        for model, v in snap["openai"].items():
            t = mon["openai"].setdefault(model, {"calls": 0, "in": 0, "out": 0})
            for k in t: t[k] += v[k]
        for item, v in snap["fal"].items():
            t = mon["fal"].setdefault(item, {"units": 0.0, "usd": 0.0})
            t["units"] = round(t["units"] + v["units"], 3); t["usd"] = round(t["usd"] + v["usd"], 4)
        r2.put(KEY, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
    except Exception as e:
        print("учёт расходов не записан:", e)
