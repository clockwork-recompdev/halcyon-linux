"""Visualizations for skin EFFECTS areas.

mpv runs ffmpeg's astats filter on the audio and reports loudness (RMS) and
peaks about 20 times a second. The effects below are driven by those levels:
they follow the music's loudness and hits, but the bar shapes are stylised,
not a true frequency spectrum.
"""
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

STYLES = ("Bars", "Scope", "Ambience")


class Levels:
    def __init__(self, player):
        self.player = player
        self.rms = self.peak = 0.0
        self.smooth = 0.0

    def update(self):
        try:
            md = self.player.mpv._get_property("af-metadata/vis") or {}
        except Exception:
            md = {}
        def norm(key):
            try:
                return max(0.0, min(1.0, (float(md.get(key, "-90")) + 60.0) / 60.0))
            except ValueError:
                return 0.0
        playing = not (self.player.mpv.pause or self.player.mpv.idle_active)
        self.rms = norm("lavfi.astats.Overall.RMS_level") if playing else 0.0
        self.peak = norm("lavfi.astats.Overall.Peak_level") if playing else 0.0
        self.smooth += (self.rms - self.smooth) * 0.25


class Painter:
    def __init__(self, levels):
        self.levels = levels
        self.caps = {}
        self.t0 = time.monotonic()

    def __call__(self, el, p: QPainter):
        s = el.size()
        w, h = s.width(), s.height()
        if w <= 0 or h <= 0:
            return
        t = time.monotonic() - self.t0
        style = STYLES[getattr(el, "vis_style", 0) % len(STYLES)]
        lv = self.levels
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if style == "Bars":
            n = max(8, min(32, w // 7))
            bw = w / n
            caps = self.caps.setdefault(id(el), [0.0] * n)
            for i in range(n):
                # Each bar breathes at its own rate; loudness sets the height,
                # peaks (hits) push the higher bars.
                wobble = 0.55 + 0.45 * math.sin(t * (2.1 + i * 0.37) + i * 1.3)
                tilt = 1.0 - 0.45 * (i / n)
                v = min(1.0, lv.smooth * tilt * (0.6 + 0.6 * wobble) + (lv.peak - lv.rms) * 0.5 * (i / n))
                bh = v * h
                g = QLinearGradient(0, h, 0, h - bh)
                g.setColorAt(0, QColor("#0a78c2"))
                g.setColorAt(1, QColor("#7fe0ff"))
                p.fillRect(QRectF(i * bw + 1, h - bh, bw - 2, bh), g)
                caps[i] = max(v, caps[i] - 0.015)
                p.fillRect(QRectF(i * bw + 1, h - caps[i] * h - 2, bw - 2, 2), QColor("#e6f6ff"))
        elif style == "Scope":
            path = QPainterPath()
            amp = h * 0.42 * (0.08 + lv.smooth)
            for x in range(0, w + 1, 2):
                y = h / 2 + amp * math.sin(x / w * 12 + t * 6) * math.sin(x / w * 3.1 - t * 2.3)
                (path.moveTo if x == 0 else path.lineTo)(QPointF(x, y))
            p.setPen(QPen(QColor("#7fe0ff"), 2))
            p.drawPath(path)
        else:   # Ambience
            r = min(w, h) * (0.25 + 0.6 * lv.smooth)
            g = QRadialGradient(QPointF(w / 2, h / 2), max(1.0, r))
            g.setColorAt(0, QColor(127, 224, 255, int(80 + 175 * lv.peak)))
            g.setColorAt(0.6, QColor(30, 155, 224, int(60 + 120 * lv.smooth)))
            g.setColorAt(1, QColor(10, 40, 70, 0))
            p.fillRect(QRectF(0, 0, w, h), g)
        p.restore()
