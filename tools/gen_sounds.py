#!/usr/bin/env python3
"""Generate the Halcyon desktop sound theme (freedesktop sound theme spec).

    gen_sounds.py OUTDIR        -> OUTDIR/index.theme, OUTDIR/stereo/*.wav

Original sounds in the same glassy Aero palette as Halcyon Games and Media
Center (key of E): logon swells up through a glass cascade, logoff falls back,
notices are soft rising fourths, warnings and errors sit lower and firmer,
devices bubble up or down. Anything not made here falls back to Ocean."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "games"))
from halcyon_games.sfx import _air, _bell, _buf, _hz, _room, _sine, _write, RATE  # noqa: E402


def pad(b, start, notes, amp, attack, decay, hold=0.0):
    """Soft chorused chord - the Aero 'glow' under the glass."""
    for n in notes:
        for det in (0.998, 1.002):
            _sine(b, start, _hz(n) * det, amp * 0.5, attack, decay, hold=hold, partials=((1, 1), (2, 0.12)))


def login():
    b = _buf(5.0)
    pad(b, 0, ("E2", "B2", "E3", "B3", "E4", "G#4"), 0.09, 0.9, 1.0, hold=0.6)
    _sine(b, 0, _hz("E1"), 0.18, 0.8, 0.9, hold=0.4)
    _air(b, 0, 3.0, 0.07, 1.2, 0.7, lp0=0.003, lp1=0.12, seed=21)
    for t, n, a in ((0.35, "B4", 0.12), (0.55, "E5", 0.12), (0.75, "G#5", 0.11), (0.98, "B5", 0.11),
                    (1.22, "E6", 0.1), (1.5, "G#6", 0.06), (1.62, "B6", 0.05)):
        _bell(b, t, _hz(n), a, 0.01, 0.6)
    return _room(b, 0.32, 1.5)


def logout():
    b = _buf(3.6)
    pad(b, 0, ("E3", "B3", "E4"), 0.08, 0.25, 0.7, hold=0.3)
    for t, n, a in ((0.0, "E6", 0.09), (0.2, "B5", 0.1), (0.42, "G#5", 0.1), (0.66, "E5", 0.11),
                    (0.95, "B4", 0.11)):
        _bell(b, t, _hz(n), a, 0.01, 0.5)
    _sine(b, 0.9, _hz("E2"), 0.16, 0.2, 0.6)
    return _room(b, 0.3, 1.4)


def chime(notes, gap=0.07, amp=0.16, decay=0.3, body=None):
    b = _buf(0.4 + gap * len(notes) + decay * 3)
    for i, n in enumerate(notes):
        _bell(b, i * gap, _hz(n), amp, 0.006, decay)
    if body:
        _sine(b, 0, _hz(body), amp * 0.6, 0.02, decay * 0.8)
    return _room(b, 0.24, 1.1)


def warning():
    # Exclamation: one firm two-hand chord, A over E.
    b = _buf(1.6)
    for n, a in (("A3", 0.12), ("E4", 0.1), ("A4", 0.14), ("C#5", 0.1), ("E5", 0.12)):
        _bell(b, 0, _hz(n), a, 0.004, 0.42)
    _sine(b, 0, _hz("A2"), 0.16, 0.01, 0.3)
    return _room(b, 0.25, 1.2)


def error(weight=1.0):
    # Critical stop: two low, darker hits falling a fourth.
    b = _buf(1.6)
    for t, root in ((0.0, "C4"), (0.16, "G3")):
        for n, a in ((root, 0.18), (root[:-1] + str(int(root[-1]) + 1), 0.08)):
            _bell(b, t, _hz(n), a * weight, 0.003, 0.32)
        _sine(b, t, _hz(root) / 2, 0.2 * weight, 0.005, 0.18, partials=((1, 1), (2, 0.25), (3, 0.08)))
    return _room(b, 0.22, 1.1)


def bubble(up, f0="B3", f1="E5", extra=None):
    b = _buf(0.9)
    a, z = (f0, f1) if up else (f1, f0)
    _sine(b, 0, _hz(a), 0.4, 0.008, 0.08, f_end=_hz(z), glide=0.07, partials=((1, 1), (2, 0.2)))
    _bell(b, 0.08, _hz(extra or (f1 if up else f0)) * 2, 0.08, 0.006, 0.18)
    return _room(b, 0.22)


def crumple():
    """Emptying the trash: paper crunches as bursts of bright noise."""
    rnd = random.Random(31)
    b = _buf(0.9)
    t = 0.0
    while t < 0.55:
        _air(b, t, 0.05, 0.12 * rnd.uniform(0.4, 1.0), 0.002, 0.012 * rnd.uniform(0.6, 1.6),
             lp0=0.5, lp1=0.8, seed=rnd.randrange(10 ** 6))
        t += rnd.uniform(0.012, 0.05)
    _air(b, 0, 0.6, 0.04, 0.05, 0.15, lp0=0.08, lp1=0.2, seed=32)
    return _room(b, 0.12, 0.7)


def tick():
    b = _buf(0.25)
    _bell(b, 0, _hz("E6"), 0.2, 0.002, 0.035)
    return _room(b, 0.1, 0.6)


def repeat(samples, times, gap):
    n = int(RATE * gap)
    out = [0.0] * (n * (times - 1) + len(samples))
    for k in range(times):
        for i, v in enumerate(samples):
            out[k * n + i] += v
    return out


def sounds():
    info = chime(("B5", "E6"), gap=0.08, amp=0.15, decay=0.28)
    im = chime(("E6", "G#6", "B6"), gap=0.06, amp=0.13, decay=0.22, body="E5")
    success = chime(("E5", "G#5", "B5", "E6"), gap=0.07, amp=0.14, decay=0.35)
    failure = chime(("E5", "C5", "A4"), gap=0.11, amp=0.14, decay=0.3)
    question = chime(("B5", "D#6"), gap=0.12, amp=0.14, decay=0.3)
    low = chime(("E5", "C#5", "A4"), gap=0.14, amp=0.14, decay=0.3)
    s = {
        "desktop-login": login(), "desktop-logout": logout(),
        "service-login": info, "service-logout": chime(("E6", "B5"), gap=0.08, amp=0.13, decay=0.25),
        "dialog-information": info, "message-attention": info, "message-highlight": info,
        "dialog-question": question, "media-insert-request": question,
        "dialog-warning": warning(), "dialog-warning-auth": warning(),
        "dialog-error": error(0.85), "dialog-error-serious": error(1.0), "dialog-error-critical": error(1.1),
        "message-new-instant": im, "message-contact-in": chime(("B5", "E6", "G#6"), gap=0.07, amp=0.12),
        "message-contact-out": chime(("G#6", "E6", "B5"), gap=0.07, amp=0.1),
        "message-sent-instant": chime(("G#6",), amp=0.1, decay=0.12),
        "message-new-email": chime(("G#5", "C#6", "G#5", "C#6"), gap=0.09, amp=0.12, decay=0.22),
        "phone-incoming-call": repeat(im, 3, 0.9),
        "device-added": bubble(True), "device-removed": bubble(False),
        "power-plug": bubble(True, "E4", "B5"), "power-unplug": bubble(False, "E4", "B5"),
        "trash-empty": crumple(), "audio-volume-change": tick(),
        "bell": chime(("E6",), amp=0.16, decay=0.25), "bell-window-system": chime(("E6",), amp=0.16, decay=0.25),
        "completion-success": success, "outcome-success": success, "complete-media-burn": success,
        "battery-full": success, "game-over-winner": success,
        "completion-fail": failure, "outcome-failure": failure, "complete-media-error": failure,
        "game-over-loser": failure,
        "battery-low": low, "battery-caution": repeat(low, 2, 0.7),
        "alarm-clock-elapsed": repeat(chime(("B5", "E6", "G#6"), gap=0.09, amp=0.14), 3, 1.0),
        "theme-demo": login(),
    }
    return s


def main():
    out = Path(sys.argv[1])
    (out / "stereo").mkdir(parents=True, exist_ok=True)
    (out / "index.theme").write_text(
        "[Sound Theme]\nName=Halcyon\nComment=Glassy Aero sounds for Halcyon\n"
        "Inherits=ocean,freedesktop\nDirectories=stereo\nExample=theme-demo\n\n[stereo]\nOutputProfile=stereo\n")
    for name, samples in sounds().items():
        _write(out / "stereo" / f"{name}.wav", samples)


if __name__ == "__main__":
    main()
