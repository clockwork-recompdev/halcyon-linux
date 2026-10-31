"""Halcyon Chess: a glass board against the computer (four levels) or a
friend. Drag or click to move; legal squares are marked; the move list,
captured pieces, undo, hints and board flipping are at hand, and an
unfinished game is waiting the next time you open it."""
import sys
import threading
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel, QListWidget,
                               QMessageBox, QRadioButton, QVBoxLayout, QWidget)

from . import common
from .chess_engine import Engine, Position, name

GLYPH = {"K": "♚", "Q": "♛", "R": "♜", "B": "♝", "N": "♞", "P": "♟"}
LEVEL_NAMES = {1: "Beginner", 2: "Casual", 3: "Club player", 4: "Strong"}


class Bus(QObject):
    moved = Signal(object, int)


def _lathe(profile):
    """A turned piece: (x, y) points down its right side (x from the middle, 100-unit box), mirrored."""
    path = QPainterPath()
    pts = [QPointF(50 + x, y) for x, y in profile]
    back = [QPointF(50 - x, y) for x, y in reversed(profile)]
    path.moveTo(pts[0])
    for q in pts[1:] + back:
        path.lineTo(q)
    path.closeSubpath()
    return path


def _base():
    b = QPainterPath()
    b.addRoundedRect(QRectF(18, 84, 64, 11), 3, 3)
    return b


_SHAPES = {}


def piece_shape(kind):
    """(silhouette, detail lines) for a piece, in a 100x100 box - our own simple Staunton-like set."""
    if kind in _SHAPES:
        return _SHAPES[kind]
    body, lines = QPainterPath(), QPainterPath()
    if kind == "P":
        body = _lathe([(0, 86), (24, 86), (24, 80), (14, 74), (9, 58), (13, 54), (13, 50), (0, 50)])
        body.addEllipse(QPointF(50, 38), 14, 14)
        lines.moveTo(37, 56)
        lines.lineTo(63, 56)
    elif kind == "R":
        body = _lathe([(0, 86), (26, 86), (26, 79), (18, 74), (17, 36), (23, 32), (23, 14), (0, 14)])
        notch = QPainterPath()
        for x in (35, 47, 59):
            notch.addRect(QRectF(x, 13, 6, 8))
        body = body.subtracted(notch)
        lines.moveTo(33, 36)
        lines.lineTo(67, 36)
        lines.moveTo(33, 74)
        lines.lineTo(67, 74)
    elif kind == "B":
        body = _lathe([(0, 86), (25, 86), (25, 80), (14, 74), (9, 54), (15, 50), (9, 47), (13, 38), (14, 30), (9, 20),
                       (0, 13)])
        body.addEllipse(QPointF(50, 10), 5, 5)
        lines.moveTo(55, 26)
        lines.lineTo(46, 38)
        lines.moveTo(38, 50)
        lines.lineTo(62, 50)
    elif kind == "Q":
        body = _lathe([(0, 86), (27, 86), (27, 79), (17, 73), (11, 50), (16, 44), (13, 40), (24, 22), (0, 30)])
        for x, y in ((26, 18), (37, 13), (50, 11), (63, 13), (74, 18)):
            body.addEllipse(QPointF(x, y), 4.5, 4.5)
        lines.moveTo(34, 44)
        lines.lineTo(66, 44)
    elif kind == "K":
        body = _lathe([(0, 86), (27, 86), (27, 79), (17, 73), (12, 48), (17, 43), (14, 39), (22, 26), (10, 22), (0, 22)])
        cross = QPainterPath()
        cross.addRect(QRectF(46, 3, 8, 20))
        cross.addRect(QRectF(40, 8, 20, 7))
        body = body.united(cross)
        lines.moveTo(34, 43)
        lines.lineTo(66, 43)
        lines.moveTo(30, 28)
        lines.lineTo(70, 28)
    else:                                   # knight: a horse's head, facing left
        body.moveTo(24, 86)
        body.lineTo(76, 86)
        body.lineTo(76, 80)
        body.lineTo(68, 76)
        body.cubicTo(72, 58, 74, 34, 60, 20)
        body.lineTo(58, 10)
        body.lineTo(51, 17)
        body.cubicTo(42, 15, 32, 22, 26, 34)
        body.lineTo(18, 48)
        body.cubicTo(16, 54, 22, 58, 27, 55)
        body.lineTo(34, 50)
        body.cubicTo(40, 50, 44, 47, 47, 44)
        body.cubicTo(44, 56, 34, 64, 32, 76)
        body.lineTo(24, 80)
        body.closeSubpath()
        lines.addEllipse(QPointF(42, 30), 2.5, 2.5)
        lines.moveTo(56, 22)
        lines.cubicTo(64, 34, 66, 52, 62, 72)
    body = body.united(_base()).simplified()
    _SHAPES[kind] = (body, lines)
    return body, lines


class BoardView(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setMinimumSize(420, 420)
        self.setMouseTracking(True)
        self.selected = None
        self.drag = None              # (square, pos)
        self.hover = None
        self.hint = None
        fams = QFontDatabase.families()
        self.piece_font = next((f for f in ("DejaVu Sans", "Noto Sans Symbols2", "Symbola", "FreeSerif") if f in fams),
                               self.font().family())

    @property
    def pos(self):
        return self.win.position

    def geom(self):
        size = min(self.width(), self.height()) - 36
        s = size / 8
        ox, oy = (self.width() - size) / 2, (self.height() - size) / 2
        return s, ox, oy

    def rect_of(self, sq):
        s, ox, oy = self.geom()
        f, r = sq % 8, sq // 8
        if self.win.flipped:
            f, r = 7 - f, 7 - r
        return QRectF(ox + f * s, oy + (7 - r) * s, s, s)

    def square_at(self, p):
        s, ox, oy = self.geom()
        f, r = int((p.x() - ox) // s), 7 - int((p.y() - oy) // s)
        if not (0 <= f < 8 and 0 <= r < 8):
            return None
        if self.win.flipped:
            f, r = 7 - f, 7 - r
        return r * 8 + f

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0, QColor("#1b2b44"))
        bg.setColorAt(1, QColor("#08101c"))
        p.fillRect(self.rect(), bg)
        s, ox, oy = self.geom()
        frame = QRectF(ox - 14, oy - 14, s * 8 + 28, s * 8 + 28)
        fg = QLinearGradient(frame.topLeft(), frame.bottomRight())
        fg.setColorAt(0, QColor("#4b6a93"))
        fg.setColorAt(1, QColor("#1f3657"))
        p.setPen(QPen(QColor(150, 200, 255, 120), 1))
        p.setBrush(fg)
        p.drawRoundedRect(frame, 10, 10)
        legal = [m for m in self.win.legal if self.selected is not None and m[0] == self.selected]
        targets = {m[1] for m in legal}
        last = self.win.last_move
        check_sq = self.pos.king(self.pos.turn == "w") if self.pos.in_check() else None
        for sq in range(64):
            r = self.rect_of(sq)
            light = (sq % 8 + sq // 8) % 2 == 1
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            if light:
                g.setColorAt(0, QColor("#eaf2fb"))
                g.setColorAt(1, QColor("#c9dcf0"))
            else:
                g.setColorAt(0, QColor("#7d9ec6"))
                g.setColorAt(1, QColor("#56789f"))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawRect(r)
            if last and sq in last[:2]:
                p.fillRect(r, QColor(255, 222, 90, 90))
            if sq == self.selected:
                p.fillRect(r, QColor(120, 200, 255, 110))
            if self.hint and sq in self.hint[:2]:
                p.fillRect(r, QColor(120, 255, 160, 110))
            if sq == check_sq:
                glow = QRadialGradient(r.center(), s * 0.6)
                glow.setColorAt(0, QColor(255, 60, 40, 220))
                glow.setColorAt(1, QColor(255, 60, 40, 0))
                p.setBrush(glow)
                p.drawRect(r)
        # glass sheen over the whole board
        sheen = QLinearGradient(0, oy, 0, oy + s * 3)
        sheen.setColorAt(0, QColor(255, 255, 255, 50))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(ox, oy, s * 8, s * 3), sheen)
        # coordinates
        f = QFont(self.font())
        f.setPixelSize(max(9, int(s * 0.16)))
        p.setFont(f)
        p.setPen(QColor(220, 235, 255, 200))
        for i in range(8):
            file = i if not self.win.flipped else 7 - i
            rank = 7 - i if not self.win.flipped else i
            p.drawText(QRectF(ox + i * s, oy + 8 * s + 1, s, 13), Qt.AlignCenter, "abcdefgh"[file])
            p.drawText(QRectF(ox - 13, oy + i * s, 12, s), Qt.AlignCenter, str(rank + 1))
        # pieces
        for sq, piece in enumerate(self.pos.b):
            if piece == "." or (self.drag and self.drag[0] == sq):
                continue
            self.draw_piece(p, piece, self.rect_of(sq))
        for t in targets:
            r = self.rect_of(t)
            p.setPen(Qt.NoPen)
            if self.pos.b[t] != "." or (self.pos.b[self.selected].upper() == "P" and t == self.pos.ep):
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(20, 60, 120, 150), s * 0.07))
                p.drawEllipse(r.center(), s * 0.42, s * 0.42)
            else:
                p.setBrush(QColor(20, 60, 120, 110))
                p.drawEllipse(r.center(), s * 0.14, s * 0.14)
        if self.drag:
            sq, at = self.drag
            r = QRectF(0, 0, s * 1.1, s * 1.1)
            r.moveCenter(at)
            self.draw_piece(p, self.pos.b[sq], r, lifted=True)
        if self.win.thinking:
            p.setPen(QColor(220, 235, 255))
            p.drawText(QRectF(ox, oy - 14, s * 8, 13), Qt.AlignRight | Qt.AlignVCenter, "Thinking…")

    def draw_piece(self, p, piece, r, lifted=False):
        is_white = piece.isupper()
        shape, details = piece_shape(piece.upper())
        k = r.width() / 100
        from PySide6.QtGui import QTransform
        t = QTransform()
        t.translate(r.left(), r.top())
        t.scale(k, k)
        solid, lines = t.map(shape), t.map(details)
        if lifted:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawPath(solid.translated(r.width() * 0.05, r.height() * 0.07))
        br = solid.boundingRect()
        g = QLinearGradient(br.left(), br.top(), br.right(), br.bottom())
        if is_white:
            g.setColorAt(0, QColor("#ffffff"))
            g.setColorAt(0.6, QColor("#e2edf9"))
            g.setColorAt(1, QColor("#9fbbe0"))
            outline, detail = QColor("#2a3e5c"), QColor(42, 62, 92, 150)
        else:
            g.setColorAt(0, QColor("#62748f"))
            g.setColorAt(0.45, QColor("#1d2738"))
            g.setColorAt(1, QColor("#04070c"))
            outline, detail = QColor("#9fc2ee"), QColor(160, 200, 245, 150)
        p.setPen(QPen(outline, max(1.0, r.width() * 0.025), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(g)
        p.drawPath(solid)
        p.setPen(QPen(detail, max(1.0, r.width() * 0.02), Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(lines)
        p.save()                                   # glass glint down the left side
        p.setClipPath(solid)
        hl = QLinearGradient(br.left(), 0, br.center().x(), 0)
        hl.setColorAt(0, QColor(255, 255, 255, 150 if not is_white else 110))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(br.left(), br.top(), br.width() * 0.35, br.height()), hl)
        p.restore()

    # --- mouse
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or not self.win.my_turn():
            return
        sq = self.square_at(e.position())
        if sq is None:
            return
        self.hint = None
        piece = self.pos.b[sq]
        if self.selected is not None and any(m[0] == self.selected and m[1] == sq for m in self.win.legal):
            self.win.human_move(self.selected, sq)
            self.selected = None
        elif piece != "." and piece.isupper() == (self.pos.turn == "w"):
            self.selected = sq
            self.drag = (sq, e.position())
        else:
            self.selected = None
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag:
            self.drag = (self.drag[0], e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if not self.drag:
            return
        src = self.drag[0]
        self.drag = None
        dst = self.square_at(e.position())
        if dst is not None and dst != src and any(m[0] == src and m[1] == dst for m in self.win.legal):
            self.win.human_move(src, dst)
            self.selected = None
        self.update()


class PromotionDialog(QDialog):
    def __init__(self, parent, white):
        super().__init__(parent)
        self.setWindowTitle("Promote Pawn")
        self.choice = "Q"
        lay = QHBoxLayout(self)
        for k in "QRBN":
            b = QRadioButton(GLYPH[k] + " " + {"Q": "Queen", "R": "Rook", "B": "Bishop", "N": "Knight"}[k])
            f = b.font()
            f.setPointSizeF(13)
            b.setFont(f)
            b.setChecked(k == "Q")
            b.toggled.connect(lambda on, k=k: on and setattr(self, "choice", k))
            lay.addWidget(b)
        ok = QDialogButtonBox(QDialogButtonBox.Ok)
        ok.accepted.connect(self.accept)
        lay.addWidget(ok)


class Chess(common.GameWindow):
    NAME = "Chess"
    ICON = "knights"
    HELP = ("Checkmate the other king: attack it so that it has no safe square to go to.\n\n"
            "Drag a piece, or click it and then click where it should go - the dots show where it can move. "
            "Castling, taking en passant and promoting a pawn all work as usual.\n\n"
            "Game ▸ Undo takes back your last move, Game ▸ Hint suggests one, and Options sets the computer's "
            "strength, your color, or a game between two people at one computer.")

    def __init__(self):
        super().__init__("chess")
        self.position = Position()
        self.legal = []
        self.moves_san = []
        self.last_move = None
        self.thinking = False
        self.gen = 0
        self.bus = Bus()
        self.bus.moved.connect(self.engine_moved)
        self.flipped = False
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.board = BoardView(self)
        lay.addWidget(self.board, 1)
        side = QWidget(objectName="side")
        side.setStyleSheet("#side { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f6f9fd, "
                           "stop:1 #e6eef8); border-left: 1px solid #a9bfd8; } QLabel { color: #1e395b; }")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        self.turn_label = QLabel()
        f = self.turn_label.font()
        f.setPointSizeF(11)
        self.turn_label.setFont(f)
        sl.addWidget(self.turn_label)
        self.captured = QLabel(wordWrap=True)
        cf = self.captured.font()
        cf.setPointSizeF(15)
        self.captured.setFont(cf)
        sl.addWidget(self.captured)
        sl.addWidget(QLabel("<b>Moves</b>"))
        self.move_list = QListWidget()
        self.move_list.setStyleSheet("QListWidget { background: white; border: 1px solid #c5d6e8; color: #1e1e1e; }")
        sl.addWidget(self.move_list, 1)
        lay.addWidget(side)
        self.setCentralWidget(w)
        self.standard_menu(undo=self.undo, hint=self.show_hint)
        self.add(self.game_menu, "Flip Board", self.flip, "Ctrl+F")
        self.level_label = QLabel()
        self.statusBar().addPermanentWidget(self.level_label)
        self.resize(900, 700)
        saved = self.store.opt("saved")
        if saved and saved.get("moves"):
            self.load_saved(saved)
        else:
            self.new_game()

    # --- settings
    def level(self):
        return self.store.opt("level", 2)

    def two_player(self):
        return self.store.opt("mode", "computer") == "friend"

    def human_color(self):
        return self.store.opt("color", "w")

    def variant(self):
        return "Two players" if self.two_player() else f"vs. computer: {LEVEL_NAMES[self.level()]}"

    def in_progress(self):
        return bool(self.moves_san) and self.position.outcome() is None

    def options(self):
        d = QDialog(self)
        d.setWindowTitle("Options")
        lay = QVBoxLayout(d)
        mode_box = QGroupBox("Opponent")
        ml = QVBoxLayout(mode_box)
        mg = QButtonGroup(d)
        for key, label in (("computer", "The computer"), ("friend", "A friend at this computer")):
            rb = QRadioButton(label, checked=self.store.opt("mode", "computer") == key)
            rb.key = key
            mg.addButton(rb)
            ml.addWidget(rb)
        lay.addWidget(mode_box)
        lvl_box = QGroupBox("Computer strength")
        ll = QVBoxLayout(lvl_box)
        lg = QButtonGroup(d)
        for n, label in LEVEL_NAMES.items():
            rb = QRadioButton(f"{n}  –  {label}", checked=self.level() == n)
            rb.n = n
            lg.addButton(rb)
            ll.addWidget(rb)
        lay.addWidget(lvl_box)
        col_box = QGroupBox("You play")
        cl = QHBoxLayout(col_box)
        cg = QButtonGroup(d)
        for key, label in (("w", "White (moves first)"), ("b", "Black")):
            rb = QRadioButton(label, checked=self.human_color() == key)
            rb.key = key
            cg.addButton(rb)
            cl.addWidget(rb)
        lay.addWidget(col_box)
        lay.addWidget(QLabel("A new color or opponent takes effect in the next game."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        if not d.exec():
            return
        self.store.set_opt("level", lg.checkedButton().n)
        changed = (mg.checkedButton().key != self.store.opt("mode", "computer") or
                   cg.checkedButton().key != self.human_color())
        self.store.set_opt("mode", mg.checkedButton().key)
        self.store.set_opt("color", cg.checkedButton().key)
        if changed and not self.in_progress():
            self.new_game()
        self.update_side()

    # --- game flow
    def new_game(self):
        self.gen += 1
        self.position = Position()
        self.moves_san, self.last_move = [], None
        self.thinking = False
        self.flipped = not self.two_player() and self.human_color() == "b"
        self.board.selected = self.board.hint = None
        self.store.set_opt("saved", None)
        self.refresh()
        self.maybe_engine()

    def load_saved(self, saved):
        self.position = Position()
        self.moves_san = []
        for uci in saved["moves"]:
            m = (int(uci[0]), int(uci[1]), uci[2])
            self.moves_san.append(self.position.san(m))
            self.position.push(m)
            self.last_move = m
        self.flipped = saved.get("flipped", False)
        self.refresh()
        self.statusBar().showMessage("Your unfinished game is right where you left it.", 5000)
        self.maybe_engine()

    def save(self):
        if self.in_progress():
            self.store.set_opt("saved", {"moves": [list(r[0]) for r in self.position.history], "flipped": self.flipped})
        else:
            self.store.set_opt("saved", None)

    def my_turn(self):
        if self.thinking or self.position.outcome():
            return False
        return self.two_player() or self.position.turn == self.human_color()

    def refresh(self):
        self.legal = self.position.legal()
        self.update_side()
        self.board.update()

    def update_side(self):
        pos = self.position
        out = pos.outcome()
        if out:
            res, why = out
            who = {"1-0": "White wins", "0-1": "Black wins", "1/2-1/2": "Draw"}[res]
            self.turn_label.setText(f"<b>{who}</b><br><span style='font-size:9pt'>by {why}</span>")
        else:
            side = "White" if pos.turn == "w" else "Black"
            you = "" if self.two_player() else (" (you)" if pos.turn == self.human_color() else " (computer)")
            self.turn_label.setText(f"<b>{side} to move</b>{you}" + ("<br><span style='color:#c6282d'>Check!</span>"
                                                                      if pos.in_check() else ""))
        start = "PPPPPPPPNNBBRRQ"
        have = "".join(p for p in pos.b if p != ".")
        lost_w = [k for k in "QRBNP" for _ in range(start.count(k) - have.count(k))]
        lost_b = [k for k in "QRBNP" for _ in range(start.count(k) - have.count(k.lower()))]
        self.captured.setText("".join(GLYPH[k] for k in lost_b) + "<br>" +
                              f"<span style='color:#8aa'>{''.join(GLYPH[k] for k in lost_w)}</span>")
        self.move_list.clear()
        for i in range(0, len(self.moves_san), 2):
            pair = self.moves_san[i:i + 2]
            self.move_list.addItem(f"{i // 2 + 1}.  {pair[0]}" + (f"   {pair[1]}" if len(pair) > 1 else ""))
        self.move_list.scrollToBottom()
        self.level_label.setText("Two players" if self.two_player() else f"Computer: {LEVEL_NAMES[self.level()]}")

    def play(self, m):
        pos = self.position
        capture = pos.b[m[1]] != "." or (pos.b[m[0]].upper() == "P" and m[1] == pos.ep)
        self.moves_san.append(pos.san(m))
        pos.push(m)
        self.last_move = m
        self.sounds.play("capture" if capture else "move")
        self.refresh()
        self.save()
        out = pos.outcome()
        if out:
            QTimer.singleShot(300, lambda: self.finished(out))
            return True
        return False

    def human_move(self, s, t):
        promo = None
        if self.position.b[s].upper() == "P" and t // 8 in (0, 7):
            d = PromotionDialog(self, self.position.turn == "w")
            d.exec()
            promo = d.choice
        m = next((m for m in self.legal if m[0] == s and m[1] == t and m[2] == promo), None)
        if m is None:
            self.sounds.play("bad")
            return
        if not self.play(m):
            self.maybe_engine()

    def maybe_engine(self):
        if self.two_player() or self.position.turn == self.human_color() or self.position.outcome():
            return
        self.thinking = True
        self.board.update()
        gen = self.gen
        snap = self.position.copy()
        level = self.level()

        def work():
            m = Engine(level).choose(snap)
            self.bus.moved.emit(m, gen)
        threading.Thread(target=work, daemon=True).start()

    def engine_moved(self, m, gen):
        if gen != self.gen:
            return                       # a new game (or undo) happened meanwhile
        self.thinking = False
        if m:
            QTimer.singleShot(150, lambda: self.play(m))

    def finished(self, out):
        res, why = out
        if self.two_player():
            self.store.record(self.variant(), res != "1/2-1/2")
            who = {"1-0": "White wins", "0-1": "Black wins", "1/2-1/2": "It's a draw"}[res]
            if QMessageBox.question(self, "Game Over", f"{who} by {why}.\n\nPlay again?") == QMessageBox.Yes:
                self.new_game()
            return
        mine = "1-0" if self.human_color() == "w" else "0-1"
        self.store.set_opt("saved", None)
        if res == mine:
            self.won()
        elif res == "1/2-1/2":
            self.store.record(self.variant(), False)
            if QMessageBox.question(self, "Game Over", f"The game is a draw by {why}.\n\nPlay again?") == QMessageBox.Yes:
                self.new_game()
        else:
            self.lost(f"Checkmate - the computer won." if why == "checkmate" else f"You lost by {why}.")

    def undo(self):
        pos = self.position
        if not pos.history:
            return
        self.gen += 1                    # forget any move the computer was working on
        self.thinking = False
        steps = 1 if self.two_player() else (2 if pos.turn == self.human_color() and len(pos.history) >= 2 else 1)
        for _ in range(steps):
            pos.pop()
            self.moves_san.pop()
        self.last_move = pos.history[-1][0] if pos.history else None
        self.board.selected = self.board.hint = None
        self.sounds.play("click")
        self.refresh()
        self.save()
        self.maybe_engine()

    def show_hint(self):
        if not self.my_turn():
            return
        self.statusBar().showMessage("Thinking of a good move…")
        snap = self.position.copy()
        gen = self.gen

        def work():
            m = Engine(3).choose(snap)
            QTimer.singleShot(0, lambda: None)
            self.hint_ready = (m, gen)
        t = threading.Thread(target=work, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                return QTimer.singleShot(100, poll)
            m, g = self.hint_ready
            if g == self.gen and m:
                self.board.hint = m
                self.statusBar().showMessage(f"Try {self.position.san(m)}.", 4000)
                self.board.update()
        poll()

    def flip(self):
        self.flipped = not self.flipped
        self.save()
        self.board.update()

    def confirm_new(self):
        if self.in_progress() and not self.two_player():
            if QMessageBox.question(self, "New Game", "Resign this game and start a new one? It will count as a "
                                    "loss.") != QMessageBox.Yes:
                return
            self.store.record(self.variant(), False)
        self.new_game()

    def closeEvent(self, e):
        self.save()                      # Chess keeps the game for next time instead of asking
        e.accept()


def main():
    return common.run(Chess, "halcyon-chess")


if __name__ == "__main__":
    sys.exit(main())
