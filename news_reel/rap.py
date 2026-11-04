"""Пишет текст рэпа по новостям дня (OpenAI API). Две строки с рифмой на каждую новость.

Каждая новость идёт отдельным коротким запросом (длинные запросы обрывает прокси).
Вступление и концовка собираются из шаблона, без модели.

Запуск: python3 rap.py out/news_rewritten.json out/lyrics.json
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from paraphrase import call_openai

RAP_MODEL = os.environ.get("RAP_MODEL", "gpt-4.1")

RULES = (
    "Ты автор коротких рэп-куплетов для новостного клипа. По-русски. "
    "Напиши ровно две строки с рифмой по этой новости, речитативом под бит 92 BPM, до 10 слов в строке. "
    "ГЛАВНОЕ ПРАВИЛО: каждая строка точна по фактам. Используй только то, что прямо сказано в новости. "
    "Не добавляй обстоятельств, эмоций и оценок, которых там нет (никаких 'в шоке', 'на веселе', 'к сожалению'). "
    "Не меняй числа, глаголы и смысл (гулял не значит ехал; задержали не значит арестовали). "
    "Если рифма плохо ложится на факт, возьми более простую рифму, но не искажай факт. "
    "Рифма настоящая, по ударным слогам; текст грамотный, без выдуманных слов и бессмыслицы. "
    "Сохраняй оговорки ('утверждает', 'по словам очевидцев'). Тон спокойный. "
    "Про гибель людей и атаки пиши сдержанно, без шуток и пафоса. Без мата и призывов к насилию. "
    "Не называй по имени тех, у кого в тексте стоит звёздочка *; говори по должности или безлично. "
    'Верни только JSON: {"lines": ["строка 1", "строка 2"]}'
)

DAYS = ["", "Первое", "Второе", "Третье", "Четвёртое", "Пятое", "Шестое", "Седьмое", "Восьмое",
        "Девятое", "Десятое", "Одиннадцатое", "Двенадцатое", "Тринадцатое", "Четырнадцатое",
        "Пятнадцатое", "Шестнадцатое", "Семнадцатое", "Восемнадцатое", "Девятнадцатое", "Двадцатое",
        "Двадцать первое", "Двадцать второе", "Двадцать третье", "Двадцать четвёртое", "Двадцать пятое",
        "Двадцать шестое", "Двадцать седьмое", "Двадцать восьмое", "Двадцать девятое", "Тридцатое",
        "Тридцать первое"]
MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def intro_outro(date_text):
    d = datetime.strptime(date_text, "%Y-%m-%d")
    return (f"Привет! {DAYS[d.day]} {MONTHS[d.month]}, вот новости дня.",
            "Вот и всё на сегодня. До завтра!")


def build_prompt(item):
    return RULES + "\n\nНовость:\n" + json.dumps(
        {"title": item["title"], "summary": item.get("summary", "")}, ensure_ascii=False)


def parse(text):
    a, b = text.find("{"), text.rfind("}")
    lines = json.loads(text[a:b + 1])["lines"]
    if len(lines) != 2 or not all(str(l).strip() for l in lines):
        raise ValueError("нужно две непустые строки")
    return [str(l).strip() for l in lines]


CHECK = (
    "Ты строгий редактор-фактчекер. Сверь две строки куплета с новостью. Ошибка, если: "
    "в строках есть факт, должность, число или источник, которых нет в новости (например 'по сообщениям СМИ', "
    "'по словам главы ...', если этого нет в тексте); изменён смысл; есть бессмыслица, "
    "несуществующие слова или грамматические ошибки; строки не рифмуются вообще. "
    'Верни только JSON: {"ok": true или false, "problem": "что не так, коротко"}'
)


def check(item, lines):
    q = (CHECK + "\n\nНовость:\n" + json.dumps({"title": item["title"], "summary": item.get("summary", "")},
         ensure_ascii=False) + "\n\nКуплет:\n" + "\n".join(lines))
    txt = call_openai(q, RAP_MODEL)
    d = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    return bool(d.get("ok")), str(d.get("problem", ""))


def one_couplet(item, tries=4):
    """Пишет куплет, проверяет, при ошибке переписывает с замечанием. Возвращает (строки, замечание или '')."""
    note, lines, problem = "", None, ""
    for _ in range(tries):
        q = build_prompt(item) + (f"\n\nПрошлая попытка была отклонена: {note}. Исправь." if note else "")
        try:
            lines = parse(call_openai(q, RAP_MODEL))
            ok, problem = check(item, lines)
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        if ok:
            return lines, ""
        note = problem
    if lines is None:
        raise RuntimeError(f"не вышло написать куплет для «{item['title']}»")
    return lines, problem or "не прошёл проверку"


def make_lyrics(items, date_text, tries=3):
    out, flags = [], {}
    for i, it in enumerate(items):
        lines, problem = one_couplet(it)
        out.append(lines)
        if problem:
            flags[i + 1] = problem
    intro, outro = intro_outro(date_text)
    return {"intro": intro, "items": out, "outro": outro, "flags": flags}


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "out/news_rewritten.json")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "out/lyrics.json")
    news = json.loads(src.read_text(encoding="utf-8"))
    lyr = make_lyrics(news["items"], news["date"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(lyr, ensure_ascii=False, indent=2), encoding="utf-8")
    print(lyr["intro"])
    for it, lines in zip(news["items"], lyr["items"]):
        print("\n[", it["title"], "]"); print(" ", lines[0]); print(" ", lines[1])
    print("\n" + lyr["outro"])
    for k, v in lyr["flags"].items():
        print(f"ВНИМАНИЕ, куплет №{k} не прошёл проверку: {v}")
