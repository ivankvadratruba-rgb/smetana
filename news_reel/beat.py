"""Простой бит без авторских прав: синтез из кода (бочка и хэт). Только стандартная библиотека."""
import math
import random
import struct
import sys
import wave

RATE = 44100


def render(seconds, bpm=92, path="out/beat.wav"):
    n = int(seconds * RATE)
    buf = [0.0] * n
    beat = 60.0 / bpm
    rnd = random.Random(7)

    def add_kick(t0):
        s = int(t0 * RATE)
        for i in range(int(0.25 * RATE)):
            if s + i >= n:
                break
            t = i / RATE
            freq = 120 * math.exp(-t * 18) + 45
            buf[s + i] += 0.9 * math.sin(2 * math.pi * freq * t) * math.exp(-t * 9)

    def add_hat(t0, vol):
        s = int(t0 * RATE)
        for i in range(int(0.05 * RATE)):
            if s + i >= n:
                break
            t = i / RATE
            buf[s + i] += vol * (rnd.random() * 2 - 1) * math.exp(-t * 90)

    def add_snare(t0):
        s = int(t0 * RATE)
        for i in range(int(0.18 * RATE)):
            if s + i >= n:
                break
            t = i / RATE
            buf[s + i] += 0.5 * (rnd.random() * 2 - 1) * math.exp(-t * 22)

    t = 0.0
    step = 0
    while t < seconds:
        pos = step % 8
        if pos in (0, 3, 4):
            add_kick(t)
        if pos in (2, 6):
            add_snare(t)
        add_hat(t, 0.18 if pos % 2 else 0.1)
        t += beat / 2
        step += 1

    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    peak = max(1e-9, max(abs(x) for x in buf))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(x / peak * 0.8 * 32767)) for x in buf))
    return path


if __name__ == "__main__":
    print(render(float(sys.argv[1]) if len(sys.argv) > 1 else 20))
