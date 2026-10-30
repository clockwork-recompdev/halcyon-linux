"""Interface sounds, synthesized here (no sound files to ship) and cached as WAVs.

Two palettes, modelled on measurements of the originals:
  games        - the 2012 Steam Big Picture: low, watery bubble "bloops" that swell
                 in rather than click, upward glides and a sub-bass swoosh for pages.
  mediacenter  - Windows Media Center: a soft round B3 "boop" to move, a glassy
                 bell a fourth apart (C6+F6) to select, a slow open-fifth swell on E
                 to start.
Both sit in a small glassy room (a light Schroeder reverb)."""
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
VERSION = 4
NAMES = ("move", "select", "back", "tab_left", "tab_right", "favorite", "bump", "launch", "intro")


def _cache(theme):
    shipped = Path("/usr/share/halcyon-games/sounds") / f"{VERSION}-{theme}"   # rendered at package build
    if all((shipped / f"{n}.wav").exists() for n in NAMES):
        return shipped
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "halcyon-games" / f"sfx-{VERSION}-{theme}"


def _hz(note):
    """'E4' -> 329.6"""
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    return 440 * 2 ** ((names[note[:-1]] + 12 * (int(note[-1]) + 1) - 69) / 12)


def _buf(dur):
    return [0.0] * int(RATE * dur)


def _env(t, attack, decay, hold=0.0):
    """Smooth (raised-cosine) attack, optional hold, exponential tail."""
    if t < attack:
        return 0.5 - 0.5 * math.cos(math.pi * t / attack)
    t -= attack + hold
    return 1.0 if t < 0 else math.exp(-t / decay)


def _sine(out, start, f, amp, attack, decay, f_end=None, glide=0.05, hold=0.0, partials=((1, 1.0),)):
    """Add a (gliding) tone at `start` seconds. Pitch moves exponentially from f to
    f_end over `glide` seconds - upward is how a bubble sounds."""
    o = int(start * RATE)
    n = min(len(out) - o, int(RATE * (attack + hold + decay * 7)))
    ph = 0.0
    for i in range(n):
        t = i / RATE
        fr = f * (f_end / f) ** min(1.0, t / glide) if f_end else f
        ph += 2 * math.pi * fr / RATE
        e = amp * _env(t, attack, decay, hold)
        out[o + i] += e * sum(a * math.sin(k * ph) for k, a in partials)


def _bell(out, start, f, amp, attack=0.004, decay=0.2):
    # Glass: fundamental plus soft, slightly inharmonic partials that die faster.
    _sine(out, start, f, amp, attack, decay)
    _sine(out, start, f * 2.756, amp * 0.18, attack, decay * 0.45)
    _sine(out, start, f * 5.404, amp * 0.06, attack, decay * 0.25)


def _air(out, start, dur, amp, attack, decay, lp0=0.02, lp1=0.2, seed=1):
    """Filtered noise whose brightness sweeps lp0 -> lp1: a swoosh of water or air."""
    rnd = random.Random(seed)
    o = int(start * RATE)
    n = min(len(out) - o, int(RATE * dur))
    y1 = y2 = 0.0
    for i in range(n):
        t = i / RATE
        k = lp0 + (lp1 - lp0) * min(1.0, t / max(1e-3, attack))
        y1 += k * (rnd.uniform(-1, 1) - y1)
        y2 += k * (y1 - y2)                      # two poles: rounder
        out[o + i] += amp * y2 * _env(t, attack, decay) * 3


def _room(x, wet=0.18, size=1.0):
    """Small Schroeder reverb (4 combs + 2 allpasses) so sounds sit in glass, not a void."""
    tail = int(RATE * 0.5 * size)
    x = x + [0.0] * tail
    combs = [int(d * size) for d in (1557, 1617, 1491, 1422)]
    acc = [0.0] * len(x)
    for d in combs:
        buf = [0.0] * d
        damp, fb, j, lp = 0.3, 0.78, 0, 0.0
        for i, v in enumerate(x):
            y = buf[j]
            lp = y * (1 - damp) + lp * damp
            buf[j] = v + lp * fb
            j = (j + 1) % d
            acc[i] += y * 0.25
    for d in (225, 556):
        buf = [0.0] * d
        j = 0
        for i, v in enumerate(acc):
            b = buf[j]
            buf[j] = v + b * 0.5
            acc[i] = b - v
            j = (j + 1) % d
    return [a + wet * b for a, b in zip(x, acc)]


def _games():
    s = {}
    # Move: a small bubble rising E4 -> G4, with a faint glint on top.
    b = _buf(0.3)
    _sine(b, 0, _hz("E4"), 0.5, 0.012, 0.05, f_end=_hz("G4"), glide=0.04, partials=((1, 1), (2, 0.22)))
    _sine(b, 0.006, _hz("E7"), 0.025, 0.003, 0.02)
    s["move"] = _room(b, 0.16)
    # Select: a deeper bubble and a glass fourth (B5 + E6) on top.
    b = _buf(0.6)
    _sine(b, 0, _hz("B3"), 0.42, 0.01, 0.07, f_end=_hz("E4"), glide=0.05, partials=((1, 1), (2, 0.25)))
    _bell(b, 0.02, _hz("B5"), 0.16, 0.012, 0.16)
    _bell(b, 0.05, _hz("E6"), 0.13, 0.012, 0.2)
    s["select"] = _room(b, 0.2)
    # Back: the bubble sinks, and the glass answers a fourth down.
    b = _buf(0.5)
    _sine(b, 0, _hz("G4"), 0.4, 0.01, 0.07, f_end=_hz("B3"), glide=0.08, partials=((1, 1), (2, 0.2)))
    _bell(b, 0.03, _hz("E6"), 0.1, 0.012, 0.12)
    _bell(b, 0.07, _hz("B5"), 0.09, 0.012, 0.15)
    s["back"] = _room(b, 0.18)
    # Pages: water swooshes past with a long bubble glide (higher going right).
    for name, top, seed in (("tab_right", "E4", 5), ("tab_left", "B3", 6)):
        b = _buf(0.7)
        _air(b, 0, 0.6, 0.09, 0.11, 0.12, lp0=0.01, lp1=0.12, seed=seed)
        _sine(b, 0, 44, 0.3, 0.09, 0.1)                                   # sub-bass push
        _sine(b, 0.02, _hz("A2"), 0.35, 0.05, 0.08, f_end=_hz(top), glide=0.13, partials=((1, 1), (2, 0.18)))
        s[name] = _room(b, 0.22)
    # Favorite: a little run of glass up an E major chord.
    b = _buf(0.9)
    for i, n in enumerate(("E6", "G#6", "B6", "E7")):
        _bell(b, i * 0.05, _hz(n), 0.12, 0.006, 0.12 + i * 0.03)
    _sine(b, 0, _hz("B3"), 0.25, 0.01, 0.05, f_end=_hz("E4"), glide=0.04)
    s["favorite"] = _room(b, 0.25)
    # Bump (can't go further): two dull, close low bloops.
    b = _buf(0.35)
    _sine(b, 0, 210, 0.45, 0.006, 0.035, partials=((1, 1), (2, 0.1)))
    _sine(b, 0.07, 176, 0.4, 0.006, 0.045, partials=((1, 1), (2, 0.1)))
    s["bump"] = _room(b, 0.12)
    # Launch: a deep swell rises out of the water into a bright E chord.
    b = _buf(2.0)
    _air(b, 0, 1.2, 0.13, 0.35, 0.3, lp0=0.005, lp1=0.25, seed=7)
    _sine(b, 0, _hz("E1"), 0.35, 0.25, 0.35)
    _sine(b, 0, _hz("A2"), 0.3, 0.05, 0.15, f_end=_hz("E4"), glide=0.3, partials=((1, 1), (2, 0.2)))
    for n, a in (("E3", 0.12), ("B3", 0.1), ("E4", 0.09), ("G#4", 0.06), ("B4", 0.07)):
        _sine(b, 0.22, _hz(n), a, 0.18, 0.5, hold=0.1)
    for i, n in enumerate(("E5", "B5", "E6", "G#6")):
        _bell(b, 0.25 + i * 0.07, _hz(n), 0.1, 0.008, 0.35)
    s["launch"] = _room(b, 0.3, 1.3)
    # Intro: the water parts - a rumble and wash swell into a shimmering open E chord.
    b = _buf(3.6)
    _air(b, 0, 2.6, 0.16, 1.1, 0.5, lp0=0.003, lp1=0.18, seed=8)
    _sine(b, 0, 38, 0.35, 0.6, 0.6, f_end=_hz("E2"), glide=1.2)
    for n, a in (("E2", 0.14), ("B2", 0.1), ("E3", 0.1), ("B3", 0.08), ("E4", 0.07), ("F#4", 0.04), ("B4", 0.05)):
        for det in (0.998, 1.002):                                         # gentle chorus
            _sine(b, 0.3, _hz(n) * det, a * 0.5, 1.0, 0.7, hold=0.3)
    for t, n in ((1.0, "B5"), (1.18, "E6"), (1.36, "G#6"), (1.6, "B6")):
        _bell(b, t, _hz(n), 0.06, 0.01, 0.5)
    s["intro"] = _room(b, 0.3, 1.4)
    return s


def _mediacenter():
    s = {}
    # Move: Media Center's round, pure "boop" (B3), swelled in, gone quickly.
    b = _buf(0.35)
    _sine(b, 0, _hz("B3"), 0.6, 0.045, 0.07, partials=((1, 1), (2, 0.08), (3, 0.02)))
    s["move"] = _room(b, 0.12)
    # Select: two glass notes a fourth apart, C6 and F6.
    b = _buf(0.6)
    _bell(b, 0, _hz("C6"), 0.2, 0.02, 0.2)
    _bell(b, 0.035, _hz("F6"), 0.16, 0.02, 0.2)
    _sine(b, 0, _hz("C5"), 0.06, 0.02, 0.15)
    s["select"] = _room(b, 0.2)
    # Back: the same fourth, falling and softer.
    b = _buf(0.5)
    _bell(b, 0, _hz("F6"), 0.13, 0.02, 0.14)
    _bell(b, 0.04, _hz("C6"), 0.13, 0.02, 0.18)
    s["back"] = _room(b, 0.18)
    for name, notes in (("tab_right", ("B3", "E4")), ("tab_left", ("E4", "B3"))):
        b = _buf(0.45)
        _sine(b, 0, _hz(notes[0]), 0.4, 0.03, 0.05, partials=((1, 1), (2, 0.06)))
        _sine(b, 0.06, _hz(notes[1]), 0.4, 0.03, 0.07, partials=((1, 1), (2, 0.06)))
        _air(b, 0, 0.3, 0.04, 0.08, 0.07, lp0=0.02, lp1=0.15, seed=9)
        s[name] = _room(b, 0.16)
    b = _buf(0.9)
    for i, n in enumerate(("C6", "E6", "G6", "C7")):
        _bell(b, i * 0.05, _hz(n), 0.11, 0.008, 0.13 + i * 0.03)
    s["favorite"] = _room(b, 0.22)
    # Bump: a dull low knock.
    b = _buf(0.3)
    _sine(b, 0, _hz("G3"), 0.45, 0.008, 0.04, partials=((1, 1), (2, 0.12)))
    _sine(b, 0.008, _hz("F#3"), 0.25, 0.008, 0.04)
    s["bump"] = _room(b, 0.1)
    # Launch (play a video, a song, a disc): a bright C chord over a soft low C.
    b = _buf(1.8)
    _sine(b, 0, _hz("C2"), 0.25, 0.12, 0.35)
    _sine(b, 0, _hz("C3"), 0.15, 0.12, 0.35)
    for i, (n, a) in enumerate((("G4", 0.08), ("C5", 0.14), ("C6", 0.16), ("G6", 0.08), ("C7", 0.1))):
        _bell(b, 0.02 * i, _hz(n), a, 0.06, 0.45)
    _air(b, 0, 0.8, 0.05, 0.15, 0.25, lp0=0.01, lp1=0.2, seed=10)
    s["launch"] = _room(b, 0.28, 1.3)
    # Intro: the startup swell - open fifths on E rising slowly out of nothing.
    b = _buf(4.2)
    for n, a in (("E3", 0.1), ("E4", 0.16), ("B4", 0.08), ("E5", 0.09), ("B5", 0.06), ("E6", 0.08), ("G#6", 0.03)):
        for det in (0.9985, 1.0015):
            _sine(b, 0, _hz(n) * det, a * 0.5, 1.5, 0.8, hold=0.4)
    _air(b, 0, 3.2, 0.06, 1.6, 0.6, lp0=0.004, lp1=0.1, seed=11)
    s["intro"] = _room(b, 0.3, 1.5)
    return s


THEMES = {"games": _games, "mediacenter": _mediacenter}


def _write(path, samples):
    peak = max(1e-9, max(abs(v) for v in samples))
    g = min(1.0, 0.9 / peak)
    fade = int(RATE * 0.01)            # no click at the end
    # Trim the silent end of the reverb tail.
    end = len(samples)
    while end > fade and abs(samples[end - 1]) * g < 1e-4:
        end -= 1
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = bytearray()
        for i in range(end):
            v = samples[i]
            if i > end - fade:
                v *= (end - i) / fade
            frames += struct.pack("<h", int(max(-1, min(1, v * g)) * 32767))
        w.writeframes(bytes(frames))


def render(theme, folder):
    """Write every sound of a theme as WAVs into folder (also handy for auditioning)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, samples in THEMES[theme]().items():
        tmp = folder / f"{name}.tmp"
        _write(tmp, samples)
        tmp.replace(folder / f"{name}.wav")


class Sounds:
    # Quieter sounds sit lower in the mix; the swells are meant to be heard.
    LEVELS = {"move": 0.75, "bump": 0.8, "tab_left": 0.85, "tab_right": 0.85}

    def __init__(self, parent=None, volume=0.6, theme="games"):
        self.effects = {}
        self.enabled = QSoundEffect is not None
        if not self.enabled:
            return
        try:
            cache = _cache(theme)
            if not all((cache / f"{n}.wav").exists() for n in NAMES):
                render(theme, cache)
            for name in NAMES:
                e = QSoundEffect(parent)
                e.setSource(QUrl.fromLocalFile(str(cache / f"{name}.wav")))
                e.setVolume(volume * self.LEVELS.get(name, 1.0))
                self.effects[name] = e
        except OSError:
            self.enabled = False

    def play(self, name):
        e = self.effects.get(name)
        if self.enabled and e:
            e.stop()
            e.play()


if __name__ == "__main__":
    import sys
    render(sys.argv[1] if len(sys.argv) > 1 else "games", sys.argv[2] if len(sys.argv) > 2 else ".")
