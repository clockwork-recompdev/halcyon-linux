"""Halcyon Tiles: match pairs of free tiles until the pyramid is gone.

A tile is free when nothing lies on it and its left or right side is open.
Every deal can be cleared - it's built by playing a game backwards."""
import math
import random
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

from . import common


def turtle():
    """The traditional 144-tile pyramid: (layer, x, y) in tile units."""
    pos = []
    rows = {0: (1, 12), 1: (3, 10), 2: (2, 11), 3: (1, 12), 4: (1, 12), 5: (2, 11), 6: (3, 10), 7: (1, 12)}
    for y, (a, b) in rows.items():
        pos += [(0, x, y) for x in range(a, b + 1)]
    pos += [(0, 0, 3.5), (0, 13, 3.5), (0, 14, 3.5)]
    pos += [(1, x, y) for x in range(4, 10) for y in range(1, 7)]
    pos += [(2, x, y) for x in range(5, 9) for y in range(2, 6)]
    pos += [(3, x, y) for x in range(6, 8) for y in range(3, 5)]
    pos.append((4, 6.5, 3.5))
    return pos


def faces():
    """144 faces: three suits 1-9 (x4), four winds (x4), three emblems (x4), four flowers and four seasons."""
    out = [(s, n) for s in ("orb", "bamboo", "crystal") for n in range(1, 10) for _ in range(4)]
    out += [("wind", w) for w in "NESW" for _ in range(4)]
    out += [("emblem", e) for e in ("fire", "leaf", "wave") for _ in range(4)]
    out += [("flower", k) for k in range(4)] + [("season", k) for k in range(4)]
    return out


def matches(a, b):
    if a[0] in ("flower", "season"):
        return a[0] == b[0]
    return a == b


class Layout:
    def __init__(self, seed=None):
        self.positions = turtle()
        self.tiles = {}                          # position index -> face
        self.deal(seed)

    def overlaps(self, a, b):
        return abs(a[1] - b[1]) < 1 and abs(a[2] - b[2]) < 1

    def free(self, i, present=None):
        present = self.tiles if present is None else present
        L, x, y = self.positions[i]
        left = right = False
        for j in present:
            if j == i:
                continue
            l2, x2, y2 = self.positions[j]
            if l2 == L + 1 and abs(x2 - x) < 1 and abs(y2 - y) < 1:
                return False                     # something lies on it
            if l2 == L and abs(y2 - y) < 1:
                if abs(x2 - (x - 1)) < 0.01:
                    left = True
                elif abs(x2 - (x + 1)) < 0.01:
                    right = True
        return not (left and right)

    def deal(self, seed=None):
        """Remove pairs of free positions at random from the full pyramid, giving each pair a matching
        face - played forwards, that order always clears the board."""
        rnd = random.Random(seed)
        pairs = []
        fs = faces()
        groups = {}
        for f in fs:
            groups.setdefault(f[0] if f[0] in ("flower", "season") else f, []).append(f)
        pair_faces = []
        for g in groups.values():
            rnd.shuffle(g)
            pair_faces += [(g[k], g[k + 1]) for k in range(0, len(g), 2)]
        for attempt in range(200):
            present = set(range(len(self.positions)))
            order = []
            ok = True
            while present:
                free = [i for i in present if self.free(i, present)]
                if len(free) < 2:
                    ok = False
                    break
                a, b = rnd.sample(free, 2)
                order.append((a, b))
                present -= {a, b}
            if ok:
                break
        rnd.shuffle(pair_faces)
        self.tiles = {}
        for (a, b), (fa, fb) in zip(order, pair_faces):
            self.tiles[a], self.tiles[b] = fa, fb

    def free_tiles(self):
        return [i for i in self.tiles if self.free(i)]

    def moves(self):
        fr = self.free_tiles()
        return [(a, b) for k, a in enumerate(fr) for b in fr[k + 1:] if matches(self.tiles[a], self.tiles[b])]

    def shuffle(self, rnd=random):
        """Redeal the faces still on the table so the rest can be cleared."""
        left = list(self.tiles)
        fs = list(self.tiles.values())
        groups = {}
        for f in fs:
            groups.setdefault(f[0] if f[0] in ("flower", "season") else f, []).append(f)
        pair_faces = [(g[k], g[k + 1]) for g in groups.values() for k in range(0, len(g), 2)]
        for _ in range(200):
            present, order, ok = set(left), [], True
            while present:
                free = [i for i in present if self.free(i, present)]
                if len(free) < 2:
                    ok = False
                    break
                a, b = rnd.sample(free, 2)
                order.append((a, b))
                present -= {a, b}
            if ok:
                rnd.shuffle(pair_faces)
                self.tiles = {}
                for (a, b), (fa, fb) in zip(order, pair_faces):
                    self.tiles[a], self.tiles[b] = fa, fb
                return True
        return False


# --------------------------------------------------------------- tile faces ---
ORB = [(0.5, 0.5)]
PIP = {1: [(.5, .5)], 2: [(.5, .25), (.5, .75)], 3: [(.25, .2), (.5, .5), (.75, .8)],
       4: [(.28, .25), (.72, .25), (.28, .75), (.72, .75)], 5: [(.28, .22), (.72, .22), (.5, .5), (.28, .78), (.72, .78)],
       6: [(.3, .2), (.7, .2), (.3, .5), (.7, .5), (.3, .8), (.7, .8)],
       7: [(.22, .16), (.5, .26), (.78, .36), (.3, .6), (.7, .6), (.3, .84), (.7, .84)],
       8: [(.3, .14), (.7, .14), (.3, .38), (.7, .38), (.3, .62), (.7, .62), (.3, .86), (.7, .86)],
       9: [(.22, .18), (.5, .18), (.78, .18), (.22, .5), (.5, .5), (.78, .5), (.22, .82), (.5, .82), (.78, .82)]}
SUIT_COLOR = {"orb": ("#9fd2ff", "#1f63bd"), "bamboo": ("#b8f0a0", "#2e8b3a"), "crystal": ("#ffd79a", "#c8641d")}


def draw_face(p, r, face):
    kind, val = face
    w, h = r.width(), r.height()
    if kind in SUIT_COLOR:
        light, dark = (QColor(c) for c in SUIT_COLOR[kind])
        size = w * (0.36 if val == 1 else 0.24 if val <= 4 else 0.2)
        for fx, fy in PIP[val]:
            c = QPointF(r.left() + w * (0.1 + 0.8 * fx), r.top() + h * (0.1 + 0.8 * fy))
            if kind == "orb":
                g = QRadialGradient(c.x() - size * 0.15, c.y() - size * 0.2, size * 0.7)
                g.setColorAt(0, QColor("white"))
                g.setColorAt(0.35, light)
                g.setColorAt(1, dark)
                p.setPen(QPen(dark.darker(130), 0.8))
                p.setBrush(g)
                p.drawEllipse(c, size / 2, size / 2)
            elif kind == "bamboo":
                g = QLinearGradient(c.x() - size * 0.2, 0, c.x() + size * 0.2, 0)
                g.setColorAt(0, dark)
                g.setColorAt(0.5, light)
                g.setColorAt(1, dark)
                p.setPen(QPen(dark.darker(140), 0.8))
                p.setBrush(g)
                stick = QRectF(c.x() - size * 0.17, c.y() - size * 0.6, size * 0.34, size * 1.2)
                p.drawRoundedRect(stick, size * 0.12, size * 0.12)
                p.drawLine(QPointF(stick.left(), c.y()), QPointF(stick.right(), c.y()))
            else:
                gem = QPainterPath()
                s2 = size * 0.55
                gem.moveTo(c.x(), c.y() - s2)
                gem.lineTo(c.x() + s2 * 0.7, c.y() - s2 * 0.2)
                gem.lineTo(c.x(), c.y() + s2)
                gem.lineTo(c.x() - s2 * 0.7, c.y() - s2 * 0.2)
                gem.closeSubpath()
                g = QLinearGradient(c.x(), c.y() - s2, c.x(), c.y() + s2)
                g.setColorAt(0, QColor("white"))
                g.setColorAt(0.4, light)
                g.setColorAt(1, dark)
                p.setPen(QPen(dark.darker(130), 0.8))
                p.setBrush(g)
                p.drawPath(gem)
        if val > 1:                                   # small index in the corner
            f = QFont("DejaVu Sans")
            f.setBold(True)
            f.setPixelSize(max(7, int(w * 0.2)))
            p.setFont(f)
            p.setPen(dark.darker(130))
            p.drawText(QRectF(r.right() - w * 0.28, r.top() + 1, w * 0.26, w * 0.24), Qt.AlignCenter, str(val))
    elif kind == "wind":
        f = QFont("DejaVu Serif")
        f.setBold(True)
        f.setPixelSize(int(h * 0.48))
        p.setFont(f)
        p.setPen(QColor("#1b2f5a"))
        p.drawText(r, Qt.AlignCenter, val)
        ang = {"N": -90, "E": 0, "S": 90, "W": 180}[val]
        c = QPointF(r.center().x(), r.bottom() - h * 0.14)
        a = math.radians(ang)
        tip = c + QPointF(math.cos(a), math.sin(a)) * w * 0.12
        p.setPen(QPen(QColor("#c8241d"), max(1.2, w * 0.04)))
        p.drawLine(c - QPointF(math.cos(a), math.sin(a)) * w * 0.12, tip)
        p.setBrush(QColor("#c8241d"))
        p.drawEllipse(tip, w * 0.035, w * 0.035)
    elif kind == "emblem":
        c, s = r.center(), w * 0.32
        path = QPainterPath()
        if val == "fire":
            path.moveTo(c.x(), c.y() - s * 1.2)
            path.cubicTo(c.x() + s * 1.1, c.y() - s * 0.2, c.x() + s * 0.8, c.y() + s, c.x(), c.y() + s)
            path.cubicTo(c.x() - s * 0.8, c.y() + s, c.x() - s * 1.0, c.y() - s * 0.1, c.x() - s * 0.2, c.y() - s * 0.4)
            path.cubicTo(c.x() - s * 0.1, c.y() - s * 0.1, c.x() + s * 0.1, c.y() - s * 0.6, c.x(), c.y() - s * 1.2)
            cols = ("#ffe08a", "#e8432e")
        elif val == "leaf":
            path.moveTo(c.x(), c.y() + s * 1.1)
            path.cubicTo(c.x() - s * 1.3, c.y(), c.x() - s * 0.6, c.y() - s * 1.0, c.x() + s * 0.2, c.y() - s * 1.1)
            path.cubicTo(c.x() + s * 1.1, c.y() - s * 0.6, c.x() + s * 0.9, c.y() + s * 0.5, c.x(), c.y() + s * 1.1)
            cols = ("#c8f5a6", "#2e8b3a")
        else:
            path.moveTo(c.x() - s * 1.1, c.y() + s * 0.5)
            path.cubicTo(c.x() - s * 0.6, c.y() - s * 1.0, c.x() + s * 0.6, c.y() - s * 0.9, c.x() + s * 0.4, c.y() - s * 0.1)
            path.cubicTo(c.x() + s * 0.2, c.y() + s * 0.3, c.x() - s * 0.3, c.y(), c.x() - s * 0.1, c.y() - s * 0.3)
            path.cubicTo(c.x() + s * 1.4, c.y() - s * 0.5, c.x() + s * 1.2, c.y() + s * 0.9, c.x() - s * 1.1, c.y() + s * 0.5)
            cols = ("#bfe6ff", "#1f63bd")
        g = QLinearGradient(c.x(), c.y() - s, c.x(), c.y() + s)
        g.setColorAt(0, QColor(cols[0]))
        g.setColorAt(1, QColor(cols[1]))
        p.setPen(QPen(QColor(cols[1]).darker(140), 1))
        p.setBrush(g)
        p.drawPath(path)
    else:
        c, s = r.center(), w * 0.3
        hues = ["#e85a9a", "#f2b01e", "#7a5ad6", "#e8432e"] if kind == "flower" else \
            ["#f2b01e", "#3a9d4f", "#c8641d", "#5aa7e8"]
        col = QColor(hues[val])
        p.setPen(QPen(col.darker(140), 0.8))
        if kind == "flower":
            p.setBrush(col.lighter(130))
            for k in range(5):
                a = math.radians(k * 72 - 90)
                p.drawEllipse(c + QPointF(math.cos(a), math.sin(a)) * s * 0.55, s * 0.38, s * 0.38)
            p.setBrush(QColor("#ffe08a"))
            p.drawEllipse(c, s * 0.28, s * 0.28)
        else:                                   # seasons: sun, sprout, leaf, snowflake
            p.setBrush(col)
            if val == 0:
                p.drawEllipse(c, s * 0.5, s * 0.5)
                for k in range(8):
                    a = math.radians(k * 45)
                    p.drawLine(c + QPointF(math.cos(a), math.sin(a)) * s * 0.65, c + QPointF(math.cos(a), math.sin(a)) * s)
            elif val == 3:
                p.setPen(QPen(col, max(1.5, w * 0.05), Qt.SolidLine, Qt.RoundCap))
                for k in range(3):
                    a = math.radians(k * 60)
                    d = QPointF(math.cos(a), math.sin(a)) * s
                    p.drawLine(c - d, c + d)
            else:
                leaf = QPainterPath()
                leaf.moveTo(c.x(), c.y() + s)
                leaf.cubicTo(c.x() - s, c.y(), c.x() - s * 0.4, c.y() - s, c.x() + s * 0.2, c.y() - s)
                leaf.cubicTo(c.x() + s * 0.9, c.y() - s * 0.3, c.x() + s * 0.6, c.y() + s * 0.5, c.x(), c.y() + s)
                p.drawPath(leaf)
        f = QFont("DejaVu Sans")
        f.setPixelSize(max(7, int(w * 0.16)))
        p.setFont(f)
        p.setPen(col.darker(150))
        p.drawText(QRectF(r.left(), r.bottom() - w * 0.24, w - 3, w * 0.22), Qt.AlignRight, str(val + 1))


# -------------------------------------------------------------------- board ---
class Board(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.selected = None
        self.hint = None
        self.cache = {}
        self.setMouseTracking(True)
        self.hover = None

    @property
    def lay(self):
        return self.win.layout_

    def metrics(self):
        tw = min((self.width() - 40) / 16.2, (self.height() - 40) / (8.6 * 1.3))
        th = tw * 1.3
        depth = tw * 0.14
        ox = (self.width() - 15 * tw) / 2 + depth * 2
        oy = (self.height() - 8 * th) / 2 + depth * 2
        return tw, th, depth, ox, oy

    def tile_rect(self, i):
        tw, th, depth, ox, oy = self.metrics()
        L, x, y = self.lay.positions[i]
        return QRectF(ox + x * tw - L * depth, oy + y * th - L * depth, tw, th)

    def face_pixmap(self, face, tw, th):
        key = (face, int(tw))
        if key not in self.cache:
            pm = QPixmap(int(tw * 2), int(th * 2))
            pm.setDevicePixelRatio(2)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            draw_face(p, QRectF(tw * 0.06, th * 0.05, tw * 0.88, th * 0.9), face)
            p.end()
            self.cache[key] = pm
        return self.cache[key]

    def order(self):
        return sorted(self.lay.tiles, key=lambda i: (self.lay.positions[i][0], self.lay.positions[i][2],
                                                      -self.lay.positions[i][1]))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        common.felt(p, self.rect(), "#1d4f8a")
        tw, th, depth, ox, oy = self.metrics()
        free = set(self.lay.free_tiles())
        for i in self.order():
            r = self.tile_rect(i)
            # the tile's body: a thick ivory block, sides down and to the left
            body = QPainterPath()
            body.addRoundedRect(r.translated(-depth, depth), tw * 0.1, tw * 0.1)
            p.setPen(QPen(QColor(0, 0, 0, 90), 1))
            p.setBrush(QColor("#c9b78f"))
            p.drawPath(body)
            mid = QPainterPath()
            mid.addRoundedRect(r.translated(-depth * 0.5, depth * 0.5), tw * 0.1, tw * 0.1)
            p.setBrush(QColor("#e8dcc0"))
            p.drawPath(mid)
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            sel = i == self.selected or (self.hint and i in self.hint)
            if sel:
                g.setColorAt(0, QColor("#fff6c4"))
                g.setColorAt(1, QColor("#f6d76a"))
            else:
                g.setColorAt(0, QColor("#ffffff"))
                g.setColorAt(1, QColor("#efe9dc"))
            p.setPen(QPen(QColor("#8a7a5a") if not sel else QColor("#c8961a"), 1.2 if sel else 1))
            p.setBrush(g)
            p.drawRoundedRect(r, tw * 0.1, tw * 0.1)
            p.drawPixmap(r.topLeft(), self.face_pixmap(self.lay.tiles[i], tw, th))
            if i == self.hover and i in free and not sel:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(90, 170, 255, 45))
                p.drawRoundedRect(r, tw * 0.1, tw * 0.1)
            if self.win.store.opt("dim", True) and i not in free:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(20, 30, 50, 32))
                p.drawRoundedRect(r, tw * 0.1, tw * 0.1)

    def tile_at(self, pos):
        for i in reversed(self.order()):
            if self.tile_rect(i).contains(pos):
                return i
        return None

    def mouseMoveEvent(self, e):
        h = self.tile_at(e.position())
        if h != self.hover:
            self.hover = h
            self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        i = self.tile_at(e.position())
        self.hint = None
        if i is None:
            self.selected = None
        elif not self.lay.free(i):
            self.win.sounds.play("bad")
        elif self.selected is None or self.selected == i:
            self.selected = None if self.selected == i else i
            if self.selected is not None:
                self.win.sounds.play("click")
        elif matches(self.lay.tiles[self.selected], self.lay.tiles[i]):
            self.win.remove(self.selected, i)
            self.selected = None
        else:
            self.selected = i
            self.win.sounds.play("click")
        self.update()


class Tiles(common.GameWindow):
    NAME = "Tiles"
    ICON = "kmahjongg"
    HELP = ("Clear the pyramid by matching pairs of tiles.\n\n"
            "Click two matching tiles to take them away. Only free tiles can be taken: nothing may lie on top "
            "of a tile, and its left or right side must be open. Free tiles are the bright ones.\n\n"
            "Every flower matches every other flower, and every season matches every season. Every deal can be "
            "cleared; if you get stuck, Undo, ask for a Hint, or shuffle the tiles that are left.")

    def __init__(self):
        super().__init__("tiles")
        self.board = Board(self)
        self.setCentralWidget(self.board)
        self.standard_menu(undo=self.undo, hint=self.show_hint)
        self.add(self.game_menu, "Shuffle", self.shuffle, "Ctrl+S")
        self.left_label = QLabel()
        self.pairs_label = QLabel()
        self.time_label = QLabel()
        for l in (self.left_label, self.pairs_label, self.time_label):
            self.statusBar().addPermanentWidget(l)
        self.clock = QTimer(self, interval=1000, timeout=self.update_status)
        self.clock.start()
        self.resize(1100, 760)
        self.new_game()

    def in_progress(self):
        return len(self.layout_.tiles) < 144 and bool(self.layout_.tiles)

    def options(self):
        dim = self.store.opt("dim", True)
        r = QMessageBox.question(self, "Options", "Shade the tiles that aren't free?",
                                 QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                                 QMessageBox.Yes if dim else QMessageBox.No)
        if r != QMessageBox.Cancel:
            self.store.set_opt("dim", r == QMessageBox.Yes)
            self.board.update()

    def new_game(self):
        self.layout_ = Layout()
        self.history = []
        self.started = None
        self.board.selected = self.board.hint = None
        self.sounds.play("deal")
        self.update_status()
        self.board.update()

    def remove(self, a, b):
        L = self.layout_
        self.history.append({a: L.tiles[a], b: L.tiles[b]})
        del L.tiles[a]
        del L.tiles[b]
        if self.started is None:
            self.started = time.monotonic()
        self.sounds.play("place")
        self.update_status()
        self.board.update()
        if not L.tiles:
            secs = int(time.monotonic() - self.started)
            self.started = None
            QTimer.singleShot(300, lambda: self.won(secs))
        elif not L.moves():
            QTimer.singleShot(250, self.stuck)

    def undo(self):
        if self.history:
            self.layout_.tiles.update(self.history.pop())
            self.board.selected = self.board.hint = None
            self.sounds.play("click")
            self.update_status()
            self.board.update()

    def show_hint(self):
        m = self.layout_.moves()
        if m:
            self.board.hint = m[0]
            self.board.update()
            QTimer.singleShot(1600, lambda: (setattr(self.board, "hint", None), self.board.update()))
        else:
            self.stuck()

    def shuffle(self):
        if self.layout_.tiles and self.layout_.shuffle():
            self.history = []
            self.board.selected = self.board.hint = None
            self.sounds.play("deal")
            self.update_status()
            self.board.update()

    def stuck(self):
        if not self.layout_.tiles or self.layout_.moves():
            return
        box = QMessageBox(QMessageBox.Information, "No More Moves", "There are no more matching pairs.", parent=self)
        sh = box.addButton("Shuffle", QMessageBox.AcceptRole)
        un = box.addButton("Undo", QMessageBox.ActionRole)
        nw = box.addButton("New Game", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is sh:
            self.shuffle()
        elif box.clickedButton() is un:
            self.undo()
        elif box.clickedButton() is nw:
            self.store.record(self.variant(), False)
            self.new_game()

    def update_status(self):
        secs = int(time.monotonic() - self.started) if self.started else 0
        self.left_label.setText(f"Tiles left: {len(self.layout_.tiles)}")
        self.pairs_label.setText(f"Matches available: {len(self.layout_.moves())}")
        self.time_label.setText(f"Time: {common.fmt_time(secs)}")


def main():
    return common.run(Tiles, "halcyon-tiles")


if __name__ == "__main__":
    sys.exit(main())
