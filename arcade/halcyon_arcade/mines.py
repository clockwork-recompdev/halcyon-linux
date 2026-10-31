"""Halcyon Mines: clear the field without setting off a mine. Glassy blue
tiles; numbers say how many of the eight neighbors hide a mine.

Left-click opens a square (the first one is always safe), right-click puts a
flag on it, and clicking a number whose flags are all placed opens its other
neighbors."""
import random
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QMessageBox, QRadioButton, QSpinBox, QVBoxLayout, QWidget)

from . import common

LEVELS = {"Beginner": (9, 9, 10), "Intermediate": (16, 16, 40), "Advanced": (30, 16, 99)}
NUMBER = {1: "#1f5fbf", 2: "#2e8b3a", 3: "#c8241d", 4: "#1b2f7a", 5: "#7a1b1b", 6: "#14807a", 7: "#222", 8: "#666"}


class Field:
    def __init__(self, w, h, mines):
        self.w, self.h, self.mines = w, h, min(mines, w * h - 9)
        self.mine = set()
        self.open = set()
        self.flag = {}                     # (x, y) -> "flag" | "?"
        self.placed = False
        self.boom = None

    def neighbors(self, x, y):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if (dx or dy) and 0 <= x + dx < self.w and 0 <= y + dy < self.h:
                    yield x + dx, y + dy

    def count(self, x, y):
        return sum(1 for n in self.neighbors(x, y) if n in self.mine)

    def place(self, safe, rnd=random):
        """Mines go anywhere but the first square and its neighbors."""
        keep = {safe, *self.neighbors(*safe)}
        cells = [(x, y) for x in range(self.w) for y in range(self.h) if (x, y) not in keep]
        self.mine = set(rnd.sample(cells, self.mines))
        self.placed = True

    def reveal(self, x, y):
        """Open a square (flooding through zeros). Returns the squares opened, or None on a mine."""
        if (x, y) in self.open or self.flag.get((x, y)) == "flag":
            return []
        if not self.placed:
            self.place((x, y))
        if (x, y) in self.mine:
            self.boom = (x, y)
            return None
        opened, stack = [], [(x, y)]
        while stack:
            c = stack.pop()
            if c in self.open or c in self.mine:
                continue
            if self.flag.get(c) == "flag":
                continue
            self.open.add(c)
            self.flag.pop(c, None)
            opened.append(c)
            if self.count(*c) == 0:
                stack.extend(n for n in self.neighbors(*c) if n not in self.open)
        return opened

    def chord(self, x, y):
        if (x, y) not in self.open:
            return []
        n = self.count(x, y)
        flags = sum(1 for c in self.neighbors(x, y) if self.flag.get(c) == "flag")
        if n == 0 or flags != n:
            return []
        out = []
        for c in self.neighbors(x, y):
            if c not in self.open and self.flag.get(c) != "flag":
                r = self.reveal(*c)
                if r is None:
                    return None
                out += r
        return out

    def won(self):
        return self.placed and len(self.open) == self.w * self.h - self.mines

    def flags_left(self):
        return self.mines - sum(1 for v in self.flag.values() if v == "flag")


class Board(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setMouseTracking(True)
        self.hover = None
        self.pressed = set()
        self.over = False
        self.reveal_t = 0

    @property
    def f(self):
        return self.win.field

    def cell(self):
        f = self.f
        return max(16, min((self.width() - 20) / f.w, (self.height() - 20) / f.h))

    def origin(self):
        s = self.cell()
        return (self.width() - s * self.f.w) / 2, (self.height() - s * self.f.h) / 2

    def at(self, pos):
        s = self.cell()
        ox, oy = self.origin()
        x, y = int((pos.x() - ox) // s), int((pos.y() - oy) // s)
        return (x, y) if 0 <= x < self.f.w and 0 <= y < self.f.h else None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#dfeaf7"))
        g.setColorAt(1, QColor("#b9cfe8"))
        p.fillRect(self.rect(), g)
        f, s = self.f, self.cell()
        ox, oy = self.origin()
        frame = QRectF(ox - 6, oy - 6, s * f.w + 12, s * f.h + 12)
        p.setPen(QPen(QColor("#7f9dc4"), 1))
        p.setBrush(QColor(255, 255, 255, 120))
        p.drawRoundedRect(frame, 8, 8)
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(int(s * 0.62))
        p.setFont(font)
        for x in range(f.w):
            for y in range(f.h):
                r = QRectF(ox + x * s + 1, oy + y * s + 1, s - 2, s - 2)
                c = (x, y)
                if c in f.open or (self.over and c in f.mine):
                    self.open_tile(p, r, c, s)
                else:
                    self.closed_tile(p, r, c in self.pressed, c == self.hover and not self.over)
                    mark = f.flag.get(c)
                    if self.over and mark == "flag" and c not in f.mine:
                        self.draw_flag(p, r)
                        p.setPen(QPen(QColor("#c8241d"), max(2.0, s * 0.08)))
                        p.drawLine(r.topLeft() + QPointF(s * 0.2, s * 0.2), r.bottomRight() - QPointF(s * 0.2, s * 0.2))
                    elif mark == "flag":
                        self.draw_flag(p, r)
                    elif mark == "?":
                        p.setPen(QColor("white"))
                        p.drawText(r, Qt.AlignCenter, "?")

    def closed_tile(self, p, r, pressed, hover):
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        if pressed:
            g.setColorAt(0, QColor("#2a5ea8"))
            g.setColorAt(1, QColor("#4c88d6"))
        else:
            g.setColorAt(0, QColor("#8cc2f5" if hover else "#6fa9e8"))
            g.setColorAt(0.5, QColor("#3d7fd0" if hover else "#2f6cc0"))
            g.setColorAt(0.51, QColor("#2a62b4" if hover else "#2258a8"))
            g.setColorAt(1, QColor("#5a9ae6" if hover else "#3b7ad0"))
        rad = r.width() * 0.16
        p.setPen(QPen(QColor("#1b4a8a"), 1))
        p.setBrush(g)
        p.drawRoundedRect(r, rad, rad)
        sheen = QLinearGradient(0, r.top(), 0, r.center().y())
        sheen.setColorAt(0, QColor(255, 255, 255, 150 if not pressed else 60))
        sheen.setColorAt(1, QColor(255, 255, 255, 10))
        p.setPen(Qt.NoPen)
        p.setBrush(sheen)
        p.drawRoundedRect(QRectF(r.left() + 2, r.top() + 1.5, r.width() - 4, r.height() * 0.48), rad * 0.8, rad * 0.8)
        if hover:
            glow = QRadialGradient(r.center().x(), r.bottom(), r.width() * 0.7)
            glow.setColorAt(0, QColor(160, 235, 255, 140))
            glow.setColorAt(1, QColor(160, 235, 255, 0))
            p.setBrush(glow)
            p.drawRoundedRect(r, rad, rad)

    def open_tile(self, p, r, c, s):
        f = self.f
        is_mine = c in f.mine
        rad = r.width() * 0.12
        p.setPen(QPen(QColor("#a9bfd8"), 1))
        p.setBrush(QColor("#f6c0b8") if c == f.boom else QColor("#eef4fb"))
        p.drawRoundedRect(r, rad, rad)
        if is_mine:
            if f.flag.get(c) == "flag" and c != f.boom:
                self.draw_flag(p, r)
                return
            cx, cy, rr = r.center().x(), r.center().y(), r.width() * 0.3
            orb = QRadialGradient(cx - rr * 0.3, cy - rr * 0.4, rr * 1.4)
            orb.setColorAt(0, QColor("#5d6a7c"))
            orb.setColorAt(0.5, QColor("#1c2430"))
            orb.setColorAt(1, QColor("#05070a"))
            p.setPen(Qt.NoPen)
            p.setBrush(orb)
            p.drawEllipse(QPointF(cx, cy), rr, rr)
            p.setBrush(QColor(255, 255, 255, 190))
            p.drawEllipse(QPointF(cx - rr * 0.35, cy - rr * 0.4), rr * 0.22, rr * 0.16)
            return
        n = f.count(*c)
        if n:
            p.setPen(QColor(NUMBER[n]))
            p.drawText(r, Qt.AlignCenter, str(n))

    def draw_flag(self, p, r):
        s = r.width()
        x0, y0 = r.left() + s * 0.35, r.top() + s * 0.18
        p.setPen(QPen(QColor("#3a3a3a"), max(1.5, s * 0.06)))
        p.drawLine(QPointF(x0, y0), QPointF(x0, r.bottom() - s * 0.18))
        flag = QPainterPath()
        flag.moveTo(x0, y0)
        flag.lineTo(x0 + s * 0.42, y0 + s * 0.16)
        flag.lineTo(x0, y0 + s * 0.32)
        flag.closeSubpath()
        g = QLinearGradient(x0, y0, x0 + s * 0.42, y0 + s * 0.3)
        g.setColorAt(0, QColor("#ff6a55"))
        g.setColorAt(1, QColor("#b8160c"))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawPath(flag)
        p.setBrush(QColor("#3a3a3a"))
        p.drawRect(QRectF(x0 - s * 0.14, r.bottom() - s * 0.2, s * 0.3, s * 0.06))

    # --- mouse
    def mouseMoveEvent(self, e):
        c = self.at(e.position())
        if c != self.hover:
            self.hover = c
            if e.buttons() & Qt.LeftButton and not self.over:
                self.pressed = {c} if c and c not in self.f.open else set()
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        c = self.at(e.position())
        if not c or self.over:
            return
        if e.button() == Qt.RightButton:
            self.win.toggle_flag(c)
        elif e.button() == Qt.LeftButton:
            if c in self.f.open:
                self.pressed = {n for n in self.f.neighbors(*c) if n not in self.f.open and not self.f.flag.get(n)}
            elif not self.f.flag.get(c):
                self.pressed = {c}
        elif e.button() == Qt.MiddleButton:
            self.win.chord(c)
        self.update()

    def mouseReleaseEvent(self, e):
        c = self.at(e.position())
        self.pressed = set()
        if c and e.button() == Qt.LeftButton and not self.over:
            if c in self.f.open:
                self.win.chord(c)
            else:
                self.win.open(c)
        self.update()


class Mines(common.GameWindow):
    NAME = "Mines"
    ICON = "kmines"
    HELP = ("Find every mine without setting one off.\n\n"
            "Click a square to open it. A number tells you how many of the eight squares around it hide a "
            "mine; an empty square opens its neighbors by itself. Your first click is always safe.\n\n"
            "Right-click a square you're sure hides a mine to put a flag on it. Click a number whose mines "
            "are all flagged to open the rest of its neighbors at once.\n\n"
            "You win when every square without a mine is open.")

    def __init__(self):
        super().__init__("mines")
        self.board = Board(self)
        self.setCentralWidget(self.board)
        self.standard_menu()
        self.mines_label = QLabel()
        self.time_label = QLabel()
        self.statusBar().addPermanentWidget(self.mines_label)
        self.statusBar().addPermanentWidget(self.time_label)
        self.clock = QTimer(self, interval=500, timeout=self.update_status)
        self.clock.start()
        self.new_game()

    def level(self):
        name = self.store.opt("level", "Beginner")
        if name == "Custom":
            c = self.store.opt("custom", [20, 14, 45])
            return name, c[0], c[1], c[2]
        return (name, *LEVELS[name])

    def variant(self):
        name, w, h, m = self.level()
        return name if name != "Custom" else f"Custom {w}×{h}, {m} mines"

    def stats_label(self):
        return "Mines"

    def in_progress(self):
        return self.field.placed and not self.board.over

    def new_game(self, same=False):
        name, w, h, m = self.level()
        old = getattr(self, "field", None)
        self.field = Field(w, h, m)
        if same and old is not None and old.placed:
            self.field.mine, self.field.placed = set(old.mine), True
        self.board.over = False
        self.started = None
        s = 34 if w <= 16 else 30
        self.resize(int(w * s + 60), int(h * s + 110))
        self.update_status()
        self.board.update()

    def options(self):
        d = QDialog(self)
        d.setWindowTitle("Options")
        lay = QVBoxLayout(d)
        box = QGroupBox("Difficulty")
        bl = QVBoxLayout(box)
        group = QButtonGroup(d)
        cur = self.store.opt("level", "Beginner")
        for name, (w, h, m) in LEVELS.items():
            rb = QRadioButton(f"{name}  —  {m} mines, {w} × {h}", checked=cur == name)
            rb.level = name
            group.addButton(rb)
            bl.addWidget(rb)
        crb = QRadioButton("Custom", checked=cur == "Custom")
        crb.level = "Custom"
        group.addButton(crb)
        bl.addWidget(crb)
        c = self.store.opt("custom", [20, 14, 45])
        form = QFormLayout()
        sw, sh, sm = QSpinBox(minimum=9, maximum=40, value=c[0]), QSpinBox(minimum=9, maximum=24, value=c[1]), \
            QSpinBox(minimum=10, maximum=600, value=c[2])
        form.addRow("Width:", sw)
        form.addRow("Height:", sh)
        form.addRow("Mines:", sm)
        bl.addLayout(form)
        lay.addWidget(box)
        q = QCheckBox("Allow question marks (right-click twice)", checked=self.store.opt("question", False))
        lay.addWidget(q)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        if not d.exec():
            return
        self.store.set_opt("question", q.isChecked())
        new = group.checkedButton().level
        custom = [sw.value(), sh.value(), min(sm.value(), sw.value() * sh.value() - 9)]
        if new != cur or (new == "Custom" and custom != c):
            if self.in_progress():
                self.store.record(self.variant(), False)
            self.store.set_opt("level", new)
            self.store.set_opt("custom", custom)
            self.new_game()

    # --- play
    def open(self, c):
        f = self.field
        first = not f.placed
        r = f.reveal(*c)
        if first and f.placed:
            self.started = time.monotonic()
        if r is None:
            return self.explode()
        if r:
            self.sounds.play("click" if len(r) == 1 else "flip")
        self.check_win()

    def chord(self, c):
        r = self.field.chord(*c)
        if r is None:
            return self.explode()
        if r:
            self.sounds.play("flip")
        self.check_win()

    def toggle_flag(self, c):
        f = self.field
        if c in f.open:
            return
        cur = f.flag.get(c)
        nxt = "flag" if cur is None else ("?" if cur == "flag" and self.store.opt("question", False) else None)
        if nxt:
            f.flag[c] = nxt
        else:
            f.flag.pop(c, None)
        self.sounds.play("place")
        self.update_status()
        self.board.update()

    def explode(self):
        self.board.over = True
        self.board.update()
        self.sounds.play("boom")
        secs = int(time.monotonic() - self.started) if self.started else 0
        self.started = None
        self.update_status(secs)
        QTimer.singleShot(700, self.lost_dialog)

    def lost_dialog(self):
        self.store.record(self.variant(), False)
        box = QMessageBox(QMessageBox.Information, "Game Lost", "Sorry, you hit a mine. Better luck next time!",
                          parent=self)
        again = box.addButton("Play again", QMessageBox.AcceptRole)
        restart = box.addButton("Restart this game", QMessageBox.ActionRole)
        box.addButton("Exit", QMessageBox.RejectRole)
        box.setDefaultButton(again)
        box.exec()
        if box.clickedButton() is again:
            self.new_game()
        elif box.clickedButton() is restart:
            self.new_game(same=True)
        else:
            self.close()

    def check_win(self):
        self.board.update()
        self.update_status()
        if self.field.won():
            f = self.field
            for m in f.mine:
                f.flag[m] = "flag"
            secs = int(time.monotonic() - self.started) if self.started else 0
            self.started = None
            self.board.over = True
            self.board.update()
            QTimer.singleShot(300, lambda: self.won(secs))

    def update_status(self, frozen=None):
        secs = frozen if frozen is not None else (int(time.monotonic() - self.started) if self.started else 0)
        self.mines_label.setText(f"Mines left: {self.field.flags_left()}")
        self.time_label.setText(f"Time: {secs}")


def main():
    return common.run(Mines, "halcyon-mines")


if __name__ == "__main__":
    sys.exit(main())
