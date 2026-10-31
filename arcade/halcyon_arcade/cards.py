"""Playing cards drawn in code: suits as shapes, classic pip layouts, our own
face-card design, and a glossy blue Aero back. Rendered once per size."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
                           QTransform)

SUITS = "SHDC"                      # spades, hearts, diamonds, clubs
RED = {"H", "D"}
RANKS = {1: "A", 11: "J", 12: "Q", 13: "K"}
INK = {"S": QColor("#1b1b1f"), "C": QColor("#1b1b1f"), "H": QColor("#c8241d"), "D": QColor("#c8241d")}
RATIO = 1.4


def rank_text(r):
    return RANKS.get(r, str(r))


def suit_path(s):
    """A suit symbol in a 1x1 box."""
    p = QPainterPath()
    if s == "H":
        p.moveTo(0.5, 0.92)
        p.cubicTo(0.12, 0.62, -0.02, 0.38, 0.12, 0.18)
        p.cubicTo(0.26, 0.0, 0.46, 0.06, 0.5, 0.26)
        p.cubicTo(0.54, 0.06, 0.74, 0.0, 0.88, 0.18)
        p.cubicTo(1.02, 0.38, 0.88, 0.62, 0.5, 0.92)
    elif s == "D":
        p.moveTo(0.5, 0.0)
        p.quadTo(0.66, 0.28, 0.9, 0.5)
        p.quadTo(0.66, 0.72, 0.5, 1.0)
        p.quadTo(0.34, 0.72, 0.1, 0.5)
        p.quadTo(0.34, 0.28, 0.5, 0.0)
    elif s == "S":
        p.moveTo(0.5, 0.04)
        p.cubicTo(0.74, 0.3, 1.02, 0.42, 0.9, 0.66)
        p.cubicTo(0.8, 0.84, 0.6, 0.8, 0.53, 0.68)
        p.quadTo(0.56, 0.86, 0.68, 0.98)
        p.lineTo(0.32, 0.98)
        p.quadTo(0.44, 0.86, 0.47, 0.68)
        p.cubicTo(0.4, 0.8, 0.2, 0.84, 0.1, 0.66)
        p.cubicTo(-0.02, 0.42, 0.26, 0.3, 0.5, 0.04)
    else:
        for cx, cy in ((0.5, 0.27), (0.25, 0.56), (0.75, 0.56)):
            p.addEllipse(QPointF(cx, cy), 0.2, 0.2)
        stem = QPainterPath()
        stem.moveTo(0.46, 0.5)
        stem.quadTo(0.45, 0.85, 0.32, 0.98)
        stem.lineTo(0.68, 0.98)
        stem.quadTo(0.55, 0.85, 0.54, 0.5)
        stem.closeSubpath()
        p = p.united(stem)
    return p.simplified() if s != "C" else p


_SUIT = {s: suit_path(s) for s in SUITS}


def draw_suit(p, s, x, y, size, flip=False):
    t = QTransform()
    t.translate(x, y)
    if flip:
        t.translate(size, size)
        t.rotate(180)
    t.scale(size, size)
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(INK[s])
    p.drawPath(t.map(_SUIT[s]))
    p.restore()


# pip positions in a card's middle area (x 0..1, y 0..1); y > 0.5 drawn upside down
PIPS = {
    1: [(0.5, 0.5)], 2: [(0.5, 0.0), (0.5, 1.0)], 3: [(0.5, 0.0), (0.5, 0.5), (0.5, 1.0)],
    4: [(0, 0), (1, 0), (0, 1), (1, 1)], 5: [(0, 0), (1, 0), (0.5, 0.5), (0, 1), (1, 1)],
    6: [(0, 0), (1, 0), (0, 0.5), (1, 0.5), (0, 1), (1, 1)],
    7: [(0, 0), (1, 0), (0.5, 0.25), (0, 0.5), (1, 0.5), (0, 1), (1, 1)],
    8: [(0, 0), (1, 0), (0.5, 0.25), (0, 0.5), (1, 0.5), (0.5, 0.75), (0, 1), (1, 1)],
    9: [(0, 0), (1, 0), (0, 1 / 3), (1, 1 / 3), (0.5, 0.5), (0, 2 / 3), (1, 2 / 3), (0, 1), (1, 1)],
    10: [(0, 0), (1, 0), (0.5, 1 / 6), (0, 1 / 3), (1, 1 / 3), (0, 2 / 3), (1, 2 / 3), (0.5, 5 / 6), (0, 1), (1, 1)],
}


class Deck:
    """Pixmaps for one card width."""
    def __init__(self, width):
        self.w = int(width)
        self.h = int(round(width * RATIO))
        self.cache = {}

    def face(self, rank, suit):
        key = (rank, suit)
        if key not in self.cache:
            self.cache[key] = self._face(rank, suit)
        return self.cache[key]

    def back(self):
        if "back" not in self.cache:
            self.cache["back"] = self._back()
        return self.cache["back"]

    def _canvas(self):
        pm = QPixmap(self.w * 2, self.h * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        return pm, p

    def _outline(self, p, fill):
        r = QRectF(0.5, 0.5, self.w - 1, self.h - 1)
        rad = self.w * 0.07
        p.setPen(QPen(QColor(0, 0, 0, 110), 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, rad, rad)
        return r, rad

    def _face(self, rank, suit):
        pm, p = self._canvas()
        g = QLinearGradient(0, 0, 0, self.h)
        g.setColorAt(0, QColor("#ffffff"))
        g.setColorAt(1, QColor("#f1f3f6"))
        r, rad = self._outline(p, g)
        w, h = self.w, self.h
        ink = INK[suit]
        # corner index, both ends
        f = QFont("DejaVu Sans")
        f.setBold(True)
        f.setPixelSize(max(8, int(w * 0.2)))
        p.setFont(f)
        txt = rank_text(rank)
        if len(txt) > 1:
            f.setStretch(78)                 # "10" fits the corner
            f.setLetterSpacing(QFont.PercentageSpacing, 88)
            p.setFont(f)
        cw = w * 0.2
        for flip in (False, True):
            p.save()
            if flip:
                p.translate(w, h)
                p.rotate(180)
            p.setPen(ink)
            p.drawText(QRectF(w * 0.03, h * 0.025, cw, w * 0.22), Qt.AlignCenter, txt)
            draw_suit(p, suit, w * 0.03 + cw * 0.18, h * 0.025 + w * 0.23, cw * 0.64)
            p.restore()
        # middle
        if rank <= 10:
            if rank == 1:
                size = w * 0.42
                draw_suit(p, suit, (w - size) / 2, (h - size) / 2, size)
            else:
                size = w * 0.19
                left, right = w * 0.34, w * 0.66
                top, bottom = h * 0.12, h * 0.88 - size
                for x, y in PIPS[rank]:
                    cx = left + (right - left) * x - size / 2 if rank > 3 else w / 2 - size / 2
                    cy = top + (bottom - top) * y
                    draw_suit(p, suit, cx, cy, size, flip=y > 0.5)
        else:
            self._court(p, rank, suit)
        p.end()
        return pm

    def _court(self, p, rank, suit):
        """Our own court cards: a framed panel with a crest - a crown, a jewel or a plume
        - over the suit, mirrored top and bottom like a real court card."""
        w, h = self.w, self.h
        frame = QRectF(w * 0.2, h * 0.11, w * 0.6, h * 0.78)
        red = suit in RED
        base = QColor("#f6e3c4") if not red else QColor("#fbe3dc")
        accent = QColor("#2b5fa8") if not red else QColor("#c8241d")
        gold = QColor("#d9a62b")
        g = QLinearGradient(frame.topLeft(), frame.bottomRight())
        g.setColorAt(0, base.lighter(108))
        g.setColorAt(1, base.darker(104))
        p.setPen(QPen(accent, max(1.0, w * 0.012)))
        p.setBrush(g)
        p.drawRect(frame)
        p.save()
        p.setClipRect(frame.adjusted(1, 1, -1, -1))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 28), 1))
        step = w * 0.09
        k = -frame.height()
        while k < frame.width() + frame.height():
            p.drawLine(QPointF(frame.left() + k, frame.top()), QPointF(frame.left() + k + frame.height(), frame.bottom()))
            p.drawLine(QPointF(frame.left() + k, frame.bottom()), QPointF(frame.left() + k + frame.height(), frame.top()))
            k += step
        p.restore()
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 120), 1))
        p.drawLine(QPointF(frame.left(), frame.center().y()), QPointF(frame.right(), frame.center().y()))
        for flip in (False, True):
            p.save()
            if flip:
                p.translate(w, h)
                p.rotate(180)
            cx, top = w / 2, frame.top() + frame.height() * 0.07
            s = w * 0.16
            p.setPen(QPen(accent.darker(120), max(1.0, w * 0.01)))
            p.setBrush(gold)
            if rank == 13:          # crown
                path = QPainterPath()
                path.moveTo(cx - s, top + s * 0.9)
                path.lineTo(cx - s, top + s * 0.25)
                path.lineTo(cx - s * 0.5, top + s * 0.6)
                path.lineTo(cx, top)
                path.lineTo(cx + s * 0.5, top + s * 0.6)
                path.lineTo(cx + s, top + s * 0.25)
                path.lineTo(cx + s, top + s * 0.9)
                path.closeSubpath()
                p.drawPath(path)
                p.setBrush(accent)
                for dx in (-0.5, 0, 0.5):
                    p.drawEllipse(QPointF(cx + dx * s, top + s * 0.72), s * 0.09, s * 0.09)
            elif rank == 12:        # jewel on a band
                p.drawRoundedRect(QRectF(cx - s, top + s * 0.55, s * 2, s * 0.35), s * 0.1, s * 0.1)
                p.setBrush(accent)
                jewel = QPainterPath()
                jewel.moveTo(cx, top)
                jewel.lineTo(cx + s * 0.4, top + s * 0.4)
                jewel.lineTo(cx, top + s * 0.8)
                jewel.lineTo(cx - s * 0.4, top + s * 0.4)
                jewel.closeSubpath()
                p.drawPath(jewel)
            else:                   # plume
                plume = QPainterPath()
                plume.moveTo(cx - s * 0.2, top + s * 0.9)
                plume.cubicTo(cx - s * 0.9, top + s * 0.4, cx - s * 0.2, top - s * 0.1, cx + s * 0.6, top)
                plume.cubicTo(cx + s * 0.1, top + s * 0.3, cx + s * 0.3, top + s * 0.6, cx + s * 0.2, top + s * 0.9)
                plume.closeSubpath()
                p.setBrush(accent)
                p.drawPath(plume)
                p.setBrush(gold)
                p.drawRect(QRectF(cx - s * 0.6, top + s * 0.85, s * 1.2, s * 0.18))
            # the suit beneath the crest
            draw_suit(p, suit, cx - w * 0.08, top + s * 1.05, w * 0.16)
            p.restore()

    def _back(self):
        pm, p = self._canvas()
        g = QLinearGradient(0, 0, 0, self.h)
        g.setColorAt(0, QColor("#4f9fe8"))
        g.setColorAt(0.5, QColor("#1f63bd"))
        g.setColorAt(1, QColor("#0f3f86"))
        r, rad = self._outline(p, QColor("white"))
        inner = r.adjusted(self.w * 0.06, self.w * 0.06, -self.w * 0.06, -self.w * 0.06)
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(inner, rad * 0.6, rad * 0.6)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(inner, rad * 0.6, rad * 0.6)
        p.setClipPath(clip)
        # soft diagonal light bands, an aurora glow and the glass sheen on top
        for k in range(-6, 14):
            x = inner.left() + k * self.w * 0.14
            band = QPainterPath()
            band.moveTo(x, inner.bottom())
            band.lineTo(x + self.w * 0.06, inner.bottom())
            band.lineTo(x + self.w * 0.06 + self.h * 0.6, inner.top())
            band.lineTo(x + self.h * 0.6, inner.top())
            band.closeSubpath()
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawPath(band)
        glow = QRadialGradient(inner.center().x(), inner.bottom(), inner.width() * 0.9)
        glow.setColorAt(0, QColor(140, 230, 255, 120))
        glow.setColorAt(1, QColor(140, 230, 255, 0))
        p.setBrush(glow)
        p.drawRect(inner)
        swirl = QPainterPath()
        c = inner.center()
        swirl.moveTo(c.x() - inner.width() * 0.6, c.y() + inner.height() * 0.15)
        swirl.cubicTo(c.x() - inner.width() * 0.1, c.y() - inner.height() * 0.25,
                      c.x() + inner.width() * 0.1, c.y() + inner.height() * 0.3, c.x() + inner.width() * 0.6,
                      c.y() - inner.height() * 0.1)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 110), self.w * 0.035, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(swirl)
        p.setPen(QPen(QColor(200, 240, 255, 70), self.w * 0.09, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(swirl)
        sheen = QLinearGradient(0, inner.top(), 0, inner.top() + inner.height() * 0.45)
        sheen.setColorAt(0, QColor(255, 255, 255, 120))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(sheen)
        p.drawRect(QRectF(inner.left(), inner.top(), inner.width(), inner.height() * 0.45))
        p.restore()
        p.end()
        return pm

    def slot(self, label=""):
        """An empty place on the table."""
        key = ("slot", label)
        if key not in self.cache:
            pm, p = self._canvas()
            r = QRectF(1, 1, self.w - 2, self.h - 2)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1.5))
            p.setBrush(QColor(0, 0, 0, 40))
            p.drawRoundedRect(r, self.w * 0.07, self.w * 0.07)
            if label:
                f = QFont("DejaVu Sans")
                f.setBold(True)
                f.setPixelSize(int(self.w * 0.3))
                p.setFont(f)
                p.setPen(QColor(255, 255, 255, 80))
                if label in SUITS:
                    s = self.w * 0.36
                    p.setOpacity(0.35)
                    draw_suit(p, label, (self.w - s) / 2, (self.h - s) / 2, s)
                else:
                    p.drawText(r, Qt.AlignCenter, label)
            p.end()
            self.cache[key] = pm
        return self.cache[key]
