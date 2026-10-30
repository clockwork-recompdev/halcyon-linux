"""Interface sounds for the full-screen view: short, soft, glassy tones,
synthesized here (no sound files to ship) and cached as WAVs."""
import math
import os
import random
import struct
import wave
from pathlib import Path

try:
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QSoundEffect
except ImportError:                  # no QtMultimedia: stay silent
    QSoundEffect = None

RATE = 44100
VERSION = 3
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "halcyon-games" / f"sfx-{VERSION}"


def _env(t, attack, decay):
    """Quick attack, exponential tail."""
    a = min(1.0, t / attack) if attack else 1.0
    return a * math.exp(-t / decay)


def _tone(dur, parts, attack=0.003, decay=0.05, glide=None):
    """parts: [(freq, amp)]; glide: (factor) end frequency multiplier."""
    n = int(RATE * dur)
    out = [0.0] * n
    for f, amp in parts:
        ph = 0.0
        for i in range(n):
            t = i / RATE
            k = 1 + (glide - 1) * (t / dur) if glide else 1
            ph += 2 * math.pi * f * k / RATE
            out[i] += amp * math.sin(ph) * _env(t, attack, decay)
    return out


def _seq(*notes):
    """notes: (start seconds, samples) mixed together."""
    n = max(int(s * RATE) + len(x) for s, x in notes)
    out = [0.0] * n
    for s, x in notes:
        o = int(s * RATE)
        for i, v in enumerate(x):
            out[o + i] += v
    return out


def _air(dur, amp, decay, lp=0.15, seed=1):
    """Soft filtered noise, for a breath of air under tones."""
    rnd = random.Random(seed)
    n = int(RATE * dur)
    out, y = [], 0.0
    for i in range(n):
        y += lp * (rnd.uniform(-1, 1) - y)
        out.append(amp * y * _env(i / RATE, 0.01, decay))
    return out


def _chime(f, amp=0.3, decay=0.25):
    # Bell-ish: fundamental plus soft inharmonic partials.
    return _tone(decay * 4, [(f, amp), (f * 2.01, amp * 0.35), (f * 3.98, amp * 0.12)], decay=decay)


def _sounds():
    C5, E5, G5, B5, C6, E6, G6 = 523.25, 659.25, 783.99, 987.77, 1046.5, 1318.5, 1568.0
    return {
        "move": _tone(0.1, [(1250, 0.30), (2500, 0.10), (3760, 0.04)], attack=0.002, decay=0.028),
        "select": _seq((0, _tone(0.12, [(G5, 0.22), (G5 * 2, 0.06)], decay=0.035)),
                       (0.045, _tone(0.2, [(C6, 0.22), (C6 * 2, 0.06)], decay=0.05))),
        "back": _seq((0, _tone(0.12, [(C6, 0.18)], decay=0.035)),
                     (0.045, _tone(0.18, [(G5, 0.18)], decay=0.045))),
        # Soft glass pings a fifth apart: up for the next tab, down for the previous one.
        "tab_right": _seq((0, _chime(E5, 0.2, 0.06)), (0.055, _chime(B5, 0.2, 0.09))),
        "tab_left": _seq((0, _chime(B5, 0.2, 0.06)), (0.055, _chime(E5, 0.2, 0.09))),
        "favorite": _seq((0, _chime(E6, 0.14, 0.08)), (0.05, _chime(G6, 0.14, 0.1)),
                         (0.1, _chime(C6 * 2, 0.12, 0.14))),
        "bump": _tone(0.14, [(220, 0.35), (330, 0.12)], attack=0.002, decay=0.045),
        "launch": _seq((0, _chime(C5, 0.2, 0.3)), (0.07, _chime(E5, 0.18, 0.3)),
                       (0.14, _chime(G5, 0.18, 0.35)), (0.21, _chime(C6, 0.2, 0.5)),
                       (0, _air(0.6, 0.12, 0.2, lp=0.08, seed=3))),
        "intro": _seq((0, _tone(1.8, [(C5 / 2, 0.16), (G5 / 2, 0.1), (E5, 0.07)], attack=0.5, decay=0.9)),
                      (0, _air(1.6, 0.14, 0.6, lp=0.05, seed=4)),
                      (0.35, _chime(G6, 0.06, 0.4)), (0.55, _chime(C6 * 2, 0.05, 0.5)),
                      (0.8, _chime(E6, 0.05, 0.6))),
    }


def _write(path, samples):
    peak = max(1e-9, max(abs(v) for v in samples))
    g = min(1.0, 0.9 / peak)
    fade = int(RATE * 0.01)            # no click at the end
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = bytearray()
        for i, v in enumerate(samples):
            if i > len(samples) - fade:
                v *= (len(samples) - i) / fade
            frames += struct.pack("<h", int(max(-1, min(1, v * g)) * 32767))
        w.writeframes(bytes(frames))


class Sounds:
    def __init__(self, parent=None, volume=0.6):
        self.effects = {}
        self.enabled = QSoundEffect is not None
        if not self.enabled:
            return
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            sounds = None
            for name in ("move", "select", "back", "tab_left", "tab_right", "favorite", "bump", "launch", "intro"):
                path = CACHE / f"{name}.wav"
                if not path.exists():
                    sounds = sounds or _sounds()
                    tmp = path.with_suffix(".tmp")
                    _write(tmp, sounds[name])
                    tmp.replace(path)
                e = QSoundEffect(parent)
                e.setSource(QUrl.fromLocalFile(str(path)))
                e.setVolume(volume)
                self.effects[name] = e
        except OSError:
            self.enabled = False

    def play(self, name):
        e = self.effects.get(name)
        if self.enabled and e:
            e.stop()
            e.play()
