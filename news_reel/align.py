"""Сопоставляет текст песни с метками времени сервиса: когда звучит каждая группа строк (вступление, новости, концовка).

Сопоставление по символам без пробелов, поэтому кавычки и тире не сбивают счёт.
"""
import json
import re


def build_song_text(lyr):
    it = lyr["items"]
    blk = lambda rows: "\n".join(l for r in rows for l in r)
    groups = [lyr["intro"]] + ["\n".join(r) for r in it] + [lyr["outro"]]
    text = ("[Intro]\n" + lyr["intro"] + "\n\n[Verse 1]\n" + blk(it[:4]) +
            "\n\n[Verse 2]\n" + blk(it[4:]) + "\n\n[Outro]\n" + lyr["outro"])
    return text, groups


def _nows(s):
    return re.sub(r"\s+", "", s)


def group_times(lyr, aligned_words):
    """Возвращает [(начало, конец)] для вступления, каждой новости и концовки."""
    text, groups = build_song_text(lyr)
    sent = _nows(text)
    got = _nows("".join(w["word"] for w in aligned_words))
    if sent != got:
        raise ValueError("текст в разметке не совпадает с отправленным")
    # конец каждого токена в позициях без пробелов
    ends, pos = [], 0
    for w in aligned_words:
        pos += len(_nows(w["word"]))
        ends.append(pos)
    starts = [e - len(_nows(w["word"])) for e, w in zip(ends, aligned_words)]

    def tok_at(p):  # токен, содержащий символ с индексом p
        for i, (s, e) in enumerate(zip(starts, ends)):
            if s <= p < e:
                return i
        return len(ends) - 1

    result, cursor = [], 0
    for g in groups:
        g_nows = _nows(g)
        i = sent.index(g_nows, cursor)
        first, last = tok_at(i), tok_at(i + len(g_nows) - 1)
        cursor = i + len(g_nows)
        result.append((aligned_words[first]["startS"], aligned_words[last]["endS"]))
    return result


def line_times(lyr, aligned_words):
    """[(текст строки, начало, конец)] для всех строк по порядку: вступление, куплеты новостей, концовка."""
    text, _ = build_song_text(lyr)
    sent = _nows(text)
    got = _nows("".join(w["word"] for w in aligned_words))
    if sent != got:
        raise ValueError("текст в разметке не совпадает с отправленным")
    ends, pos = [], 0
    for w in aligned_words:
        pos += len(_nows(w["word"]))
        ends.append(pos)
    starts = [e - len(_nows(w["word"])) for e, w in zip(ends, aligned_words)]
    tok = lambda p: next((i for i, (a, b) in enumerate(zip(starts, ends)) if a <= p < b), len(ends) - 1)
    lines = [lyr["intro"]] + [l for r in lyr["items"] for l in r] + [lyr["outro"]]
    out, cursor = [], 0
    for ln in lines:
        g = _nows(ln)
        i = sent.index(g, cursor)
        cursor = i + len(g)
        out.append((ln, aligned_words[tok(i)]["startS"], aligned_words[tok(i + len(g) - 1)]["endS"]))
    return out


if __name__ == "__main__":
    import sys
    lyr = json.load(open(sys.argv[1], encoding="utf-8"))
    ts = json.load(open(sys.argv[2], encoding="utf-8"))["data"]["alignedWords"]
    for name, (a, b) in zip(["intro"] + [f"новость {i + 1}" for i in range(len(lyr["items"]))] + ["outro"],
                            group_times(lyr, ts)):
        print("%-10s %6.1f - %6.1f  (%.1f с)" % (name, a, b, b - a))
