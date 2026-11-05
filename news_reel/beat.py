"""Свой бит без авторских прав: тёмный бум-бэп (бочка, малый, хэты, бас-808, аккорды).

Синтез кодом, чужих сэмплов нет. Нужен numpy.
Запуск: python3 beat.py [секунд] [bpm]
"""
import math
import os
import sys
import wave

import numpy as np

SR = 44100
# Прогрессия, по такту на аккорд: Am, F, G, E (тёмная минорная)
ROOTS = [55.0, 43.65, 49.0, 41.2]
CHORDS = [
    [220.0, 261.63, 329.63],   # Am
    [174.61, 220.0, 261.63],   # F
    [196.0, 246.94, 293.66],   # G
    [164.81, 207.65, 246.94],  # E
]


def _place(buf, sig, t0):
    s = int(t0 * SR)
    if s >= len(buf):
        return
    e = min(len(buf), s + len(sig))
    buf[s:e] += sig[:e - s]


def kick(vol=1.0):
    t = np.arange(int(0.35 * SR)) / SR
    f = 42 + 110 * np.exp(-t * 28)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return vol * np.sin(ph) * np.exp(-t * 7)


def snare(rng, vol=0.8):
    t = np.arange(int(0.22 * SR)) / SR
    noise = rng.uniform(-1, 1, len(t)) * np.exp(-t * 18)
    tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25)
    return vol * (0.7 * noise + 0.5 * tone)


def hat(rng, vol=0.2, length=0.04, decay=110):
    t = np.arange(int(length * SR)) / SR
    n = rng.uniform(-1, 1, len(t))
    n = np.diff(n, prepend=0)  # грубый высокочастотный фильтр
    return vol * n * np.exp(-t * decay)


def sub(freq, dur, vol=0.9):
    t = np.arange(int(dur * SR)) / SR
    f = freq * (1 + 0.25 * np.exp(-t * 40))
    ph = 2 * np.pi * np.cumsum(f) / SR
    env = np.minimum(1, t * 80) * np.exp(-t * 1.6)
    return vol * np.tanh(1.6 * np.sin(ph)) * env


def pluck(freqs, vol=0.16, dur=0.9):
    t = np.arange(int(dur * SR)) / SR
    out = np.zeros_like(t)
    for f in freqs:
        for k, a in ((1, 1.0), (2, 0.5), (3, 0.25), (4, 0.12)):
            out += a * np.sin(2 * np.pi * f * k * t)
    return vol * out / len(freqs) * np.exp(-t * 4.5) * np.minimum(1, t * 200)


def render(seconds, bpm=96, path="out/beat.wav"):
    rng = np.random.default_rng(7)
    n = int(seconds * SR)
    buf = np.zeros(n + SR)
    beat = 60.0 / bpm
    step = beat / 4          # шестнадцатая
    bar = beat * 4
    swing = 0.12 * step      # лёгкий свинг на чётных шестнадцатых
    bars = int(seconds / bar) + 1
    for b in range(bars):
        t0 = b * bar
        k = b % 4
        for st in range(16):
            t = t0 + st * step + (swing if st % 2 else 0)
            if st in (0, 7, 10):
                _place(buf, kick(1.0 if st == 0 else 0.8), t)
            if st in (4, 12):
                _place(buf, snare(rng), t)
            if st % 2 == 0:
                _place(buf, hat(rng, 0.22 if st % 4 == 0 else 0.14), t)
            elif st in (3, 11, 15):
                _place(buf, hat(rng, 0.09), t)
            if st == 14:
                _place(buf, hat(rng, 0.18, 0.16, 28), t)
        _place(buf, sub(ROOTS[k], beat * 1.8), t0)
        _place(buf, sub(ROOTS[k], beat * 1.5, 0.8), t0 + beat * 2.5)
        _place(buf, pluck(CHORDS[k]), t0)
        _place(buf, pluck(CHORDS[k], 0.12), t0 + 10 * step)
    buf = buf[:n]
    buf = np.tanh(1.3 * buf / max(1e-9, np.abs(buf).max())) * 0.85
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((buf * 32767).astype("<i2").tobytes())
    return path


if __name__ == "__main__":
    print(render(float(sys.argv[1]) if len(sys.argv) > 1 else 20,
                 float(sys.argv[2]) if len(sys.argv) > 2 else 96))
