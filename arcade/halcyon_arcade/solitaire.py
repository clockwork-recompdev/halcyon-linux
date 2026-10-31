"""Halcyon Solitaire: Klondike (draw one or three), Spider (one, two or
four suits) and FreeCell (the classic numbered deals), on one green table.

    halcyon-solitaire [--game klondike|spider|freecell]

Drag cards (or whole runs) to move them, double-click to send a card home,
click the deck to deal. Ctrl+Z undoes, H shows a hint."""
import copy
import math
import random
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QGroupBox, QInputDialog, QLabel,
                               QMessageBox, QRadioButton, QVBoxLayout, QWidget)

from . import cards as C
from . import common

RED = C.RED


def color(card):
    return "r" if card[1] in RED else "b"


# --------------------------------------------------------------------- rules ---
class Game:
    """Rules and state for one deal. Piles are lists of [rank, suit, face_up]."""
    kind = ""
    tableau_count = 7

    def __init__(self, seed=None):
        self.seed = seed if seed is not None else random.randrange(1, 1_000_000)
        self.piles = {}
        self.score = 0
        self.moves = 0
        self.deal()

    def snapshot(self):
        return copy.deepcopy((self.piles, self.score, self.moves))

    def restore(self, snap):
        self.piles, self.score, self.moves = copy.deepcopy(snap)

    def tableau(self):
        return [f"t{i}" for i in range(self.tableau_count)]

    def foundations(self):
        return [k for k in self.piles if k.startswith("f")]

    def runnable(self, cards):
        """Can these cards move together?"""
        return len(cards) == 1

    def can_pick(self, pile, i):
        cards = self.piles[pile][i:]
        if not cards or not all(c[2] for c in cards):
            return False
        if pile in ("stock",):
            return False
        if pile == "waste" or pile.startswith("c"):
            return i == len(self.piles[pile]) - 1
        if pile.startswith("f"):
            return i == len(self.piles[pile]) - 1 and self.kind != "spider"
        return self.runnable(cards)

    def foundation_ok(self, card, pile):
        top = self.piles[pile][-1] if self.piles[pile] else None
        return (top is None and card[0] == 1) or (top is not None and top[1] == card[1] and top[0] + 1 == card[0])

    def can_drop(self, cards, pile, source):
        raise NotImplementedError

    def move(self, src, i, dst):
        cards = self.piles[src][i:]
        del self.piles[src][i:]
        self.piles[dst].extend(cards)
        self.moves += 1
        self.scored(src, dst, len(cards))
        return self.after_move(src)

    def scored(self, src, dst, n):
        pass

    def after_move(self, src):
        """Flip the card a move uncovered; returns events for sounds."""
        p = self.piles[src]
        if src.startswith("t") and p and not p[-1][2]:
            p[-1][2] = True
            self.flipped()
            return ["flip"]
        return []

    def flipped(self):
        pass

    def legal_moves(self):
        """[(src, index, dst, priority)] - the hint list."""
        out = []
        sources = [k for k in self.piles if k != "stock" and self.piles[k]]
        for src in sources:
            pile = self.piles[src]
            for i in range(len(pile)):
                if not self.can_pick(src, i):
                    continue
                cards = pile[i:]
                for dst in self.piles:
                    if dst in (src, "stock", "waste") or not self.can_drop(cards, dst, src):
                        continue
                    pr = self.priority(src, i, dst)
                    if pr is not None:
                        out.append((src, i, dst, pr))
        out.sort(key=lambda m: -m[3])
        return out

    def priority(self, src, i, dst):
        pile = self.piles[src]
        if dst.startswith("f"):
            return 100 if not src.startswith("f") else None
        if src.startswith("f"):
            return None                                      # don't suggest taking cards back
        reveals = i > 0 and not pile[i - 1][2]
        if dst.startswith("t") and not self.piles[dst]:
            if i == 0 and src.startswith("t"):
                return None                                  # moving a whole column to an empty one
            return 30
        if dst.startswith("c"):
            return 10 if src.startswith("t") and (reveals or len(pile) > 1) else None
        if src.startswith("t") and i > 0 and pile[i - 1][2] and not reveals:
            # pointless shuffle between two equally good spots
            prev = pile[i - 1]
            if self.kind != "spider" and prev[0] == pile[i][0] + 1 and color(prev) != color(pile[i]):
                return None
            if self.kind == "spider" and prev[0] == pile[i][0] + 1 and prev[1] == pile[i][1]:
                return None
        return 80 if reveals else 60 if src in ("waste",) or src.startswith("c") else 40

    def won(self):
        return all(not self.piles[k] for k in self.piles if not k.startswith("f"))

    def can_deal(self):
        return False

    def stuck(self):
        return not self.legal_moves() and not self.can_deal()


class Klondike(Game):
    kind = "klondike"

    def __init__(self, seed=None, draw=1):
        self.draw = draw
        super().__init__(seed)
        self.score = 0

    def deal(self):
        rnd = random.Random(self.seed)
        deck = [[r, s, False] for s in C.SUITS for r in range(1, 14)]
        rnd.shuffle(deck)
        self.piles = {"stock": [], "waste": [], **{f"f{i}": [] for i in range(4)}, **{f"t{i}": [] for i in range(7)}}
        for i in range(7):
            for j in range(i + 1):
                c = deck.pop()
                c[2] = j == i
                self.piles[f"t{i}"].append(c)
        self.piles["stock"] = deck

    def runnable(self, cards):
        return all(a[0] == b[0] + 1 and color(a) != color(b) for a, b in zip(cards, cards[1:]))

    def can_drop(self, cards, pile, source):
        first = cards[0]
        if pile.startswith("f"):
            return len(cards) == 1 and self.foundation_ok(first, pile)
        if pile.startswith("t"):
            p = self.piles[pile]
            if not p:
                return first[0] == 13
            top = p[-1]
            return top[2] and top[0] == first[0] + 1 and color(top) != color(first)
        return False

    def scored(self, src, dst, n):
        if dst.startswith("f") and not src.startswith("f"):
            self.score += 10
        elif src == "waste" and dst.startswith("t"):
            self.score += 5
        elif src.startswith("f") and dst.startswith("t"):
            self.score = max(0, self.score - 15)

    def flipped(self):
        self.score += 5

    def can_deal(self):
        return bool(self.piles["stock"]) or (bool(self.piles["waste"]) and self.waste_useful())

    def waste_useful(self):
        """Would going through the deck again ever give a card a home?"""
        for c in self.piles["waste"] + self.piles["stock"]:
            for dst in self.piles:
                if (dst.startswith("f") or dst.startswith("t")) and self.can_drop([[c[0], c[1], True]], dst, "waste"):
                    return True
        return False

    def deal_stock(self):
        st, w = self.piles["stock"], self.piles["waste"]
        if not st:
            if not w:
                return None
            self.piles["stock"] = [[c[0], c[1], False] for c in reversed(w)]
            self.piles["waste"] = []
            self.score = max(0, self.score - (100 if self.draw == 1 else 20))
            self.moves += 1
            return "recycle"
        for _ in range(min(self.draw, len(st))):
            c = st.pop()
            c[2] = True
            w.append(c)
        self.moves += 1
        return "deal"

    def auto_finishable(self):
        return not self.piles["stock"] and not self.piles["waste"] and \
            all(c[2] for t in self.tableau() for c in self.piles[t])


class Spider(Game):
    kind = "spider"
    tableau_count = 10

    def __init__(self, seed=None, suits=1):
        self.suits = suits
        super().__init__(seed)
        self.score = 500

    def deal(self):
        rnd = random.Random(self.seed)
        use = {1: "S", 2: "SH", 4: "SHDC"}[self.suits]
        deck = [[r, use[k % len(use)], False] for k in range(8) for r in range(1, 14)]
        rnd.shuffle(deck)
        self.piles = {"stock": [], **{f"f{i}": [] for i in range(8)}, **{f"t{i}": [] for i in range(10)}}
        for i in range(54):
            self.piles[f"t{i % 10}"].append(deck.pop())
        for t in self.tableau():
            self.piles[t][-1][2] = True
        self.piles["stock"] = deck

    def runnable(self, cards):
        return all(a[0] == b[0] + 1 and a[1] == b[1] for a, b in zip(cards, cards[1:]))

    def can_drop(self, cards, pile, source):
        if not pile.startswith("t"):
            return False
        p = self.piles[pile]
        return not p or (p[-1][2] and p[-1][0] == cards[0][0] + 1)

    def scored(self, src, dst, n):
        self.score -= 1

    def after_move(self, src):
        ev = super().after_move(src)
        ev += self.collect()
        return ev

    def collect(self):
        """A full King-to-Ace run of one suit goes home by itself."""
        ev = []
        for t in self.tableau():
            p = self.piles[t]
            if len(p) >= 13:
                run = p[-13:]
                if all(c[2] for c in run) and [c[0] for c in run] == list(range(13, 0, -1)) and \
                        len({c[1] for c in run}) == 1:
                    dst = next(f for f in self.foundations() if not self.piles[f])
                    self.piles[dst] = run
                    del p[-13:]
                    self.score += 100
                    ev.append("home")
                    if p and not p[-1][2]:
                        p[-1][2] = True
                        ev.append("flip")
        return ev

    def can_deal(self):
        return bool(self.piles["stock"])

    def deal_stock(self):
        if not self.piles["stock"]:
            return None
        if any(not self.piles[t] for t in self.tableau()):
            return "empty"
        for t in self.tableau():
            c = self.piles["stock"].pop()
            c[2] = True
            self.piles[t].append(c)
        self.moves += 1
        return "deal"

    def priority(self, src, i, dst):
        pr = super().priority(src, i, dst)
        if pr is None:
            return None
        cards = self.piles[src][i:]
        top = self.piles[dst][-1] if self.piles[dst] else None
        if top and top[1] == cards[0][1]:
            pr += 15                              # same-suit builds first
        return pr


class FreeCell(Game):
    kind = "freecell"
    tableau_count = 8

    def deal(self):
        """The classic numbered deals (game #1 is the same deal it always was)."""
        state = self.seed
        cards = list(range(51, -1, -1))
        for i in range(52):
            state = (state * 214013 + 2531011) & 0x7FFFFFFF
            r = state >> 16
            j = 51 - r % (52 - i)
            cards[i], cards[j] = cards[j], cards[i]
        self.piles = {**{f"c{i}": [] for i in range(4)}, **{f"f{i}": [] for i in range(4)},
                      **{f"t{i}": [] for i in range(8)}}
        for k, c in enumerate(cards):
            self.piles[f"t{k % 8}"].append([c // 4 + 1, "CDHS"[c % 4], True])

    def capacity(self, to_empty):
        free = sum(1 for i in range(4) if not self.piles[f"c{i}"])
        empty = sum(1 for t in self.tableau() if not self.piles[t]) - (1 if to_empty else 0)
        return (free + 1) * (2 ** max(0, empty))

    def runnable(self, cards):
        return all(a[0] == b[0] + 1 and color(a) != color(b) for a, b in zip(cards, cards[1:]))

    def can_pick(self, pile, i):
        if pile.startswith("f"):
            return False
        return super().can_pick(pile, i)

    def can_drop(self, cards, pile, source):
        first = cards[0]
        if pile.startswith("c"):
            return len(cards) == 1 and not self.piles[pile]
        if pile.startswith("f"):
            return len(cards) == 1 and self.foundation_ok(first, pile)
        if pile.startswith("t"):
            p = self.piles[pile]
            if len(cards) > self.capacity(not p):
                return False
            if not p:
                return True
            top = p[-1]
            return top[0] == first[0] + 1 and color(top) != color(first)
        return False

    def safe_home(self):
        """A card that can go home without ever being needed on the table (FreeCell plays these itself)."""
        homes = {self.piles[f][-1][1]: self.piles[f][-1][0] for f in self.foundations() if self.piles[f]}
        for src in [k for k in self.piles if (k.startswith("t") or k.startswith("c")) and self.piles[k]]:
            c = self.piles[src][-1]
            other = [homes.get(s, 0) for s in ("SC" if c[1] in RED else "HD")]
            if c[0] <= 2 or c[0] <= min(other) + 1:
                for f in self.foundations():
                    if self.foundation_ok(c, f):
                        return src, len(self.piles[src]) - 1, f
        return None


# ---------------------------------------------------------------- the table ---
class Table(QWidget):
    """Draws the piles and handles dragging, clicking and the win fountain."""
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setMouseTracking(True)
        self.setMinimumSize(640, 460)
        self.deck = C.Deck(80)
        self.rects = {}
        self.drag = None              # (src, index, cards, grab offset, pos)
        self.hint = None
        self.fountain = []
        self.fountain_timer = QTimer(self, interval=16, timeout=self.step_fountain)

    @property
    def g(self):
        return self.win.game

    # --- layout
    def layout_piles(self):
        g = self.g
        cols = g.tableau_count
        W, H = self.width(), self.height()
        cw = min((W - 20) / (cols + (cols + 1) * 0.16), (H - 20) / 4.2)
        cw = max(40, cw)
        if abs(cw - self.deck.w) > 1:
            self.deck = C.Deck(cw)
        cw, ch = self.deck.w, self.deck.h
        gap = (W - cols * cw) / (cols + 1)
        top = max(10, gap * 0.6)
        self.rects = {}
        xs = [gap + i * (cw + gap) for i in range(cols)]
        if g.kind == "klondike":
            self.rects["stock"] = QRectF(xs[0], top, cw, ch)
            self.rects["waste"] = QRectF(xs[1], top, cw, ch)
            for i in range(4):
                self.rects[f"f{i}"] = QRectF(xs[3 + i], top, cw, ch)
        elif g.kind == "spider":
            self.rects["stock"] = QRectF(xs[9], H - ch - top, cw, ch)
            for i in range(8):
                self.rects[f"f{i}"] = QRectF(xs[0] + i * cw * 0.28, H - ch - top, cw, ch)
        else:
            for i in range(4):
                self.rects[f"c{i}"] = QRectF(xs[i], top, cw, ch)
                self.rects[f"f{i}"] = QRectF(xs[4 + i], top, cw, ch)
        ty = top if g.kind == "spider" else top + ch + gap * 0.7      # Spider keeps its deck at the bottom
        bottom = H - (ch + top * 2 if g.kind == "spider" else top)
        for i in range(cols):
            self.rects[f"t{i}"] = QRectF(xs[i], ty, cw, max(ch, bottom - ty))
        return cw, ch

    def offsets(self, pile):
        """y offset of each card in a tableau pile (squeezed if the column gets long)."""
        cards = self.g.piles[pile]
        r = self.rects[pile]
        ch = self.deck.h
        down, up = ch * 0.11, ch * 0.25
        need = sum(up if c[2] else down for c in cards[:-1])
        room = r.height() - ch
        f = min(1.0, room / need) if need > 0 else 1.0
        out, y = [], 0.0
        for c in cards:
            out.append(y)
            y += (up if c[2] else down) * f
        return out

    def card_rects(self, pile):
        r = self.rects[pile]
        cards = self.g.piles[pile]
        cw, ch = self.deck.w, self.deck.h
        if pile.startswith("t"):
            return [QRectF(r.left(), r.top() + y, cw, ch) for y in self.offsets(pile)]
        if pile == "waste" and getattr(self.g, "draw", 1) == 3:
            n = len(cards)
            return [QRectF(r.left() + max(0, i - (n - 3)) * cw * 0.22, r.top(), cw, ch) for i in range(n)]
        if pile == "stock" and self.g.kind == "spider":
            n = len(cards) // 10
            return [QRectF(r.left() - min(i // 10, n - 1) * cw * 0.16, r.top(), cw, ch) for i in range(len(cards))]
        return [QRectF(r.left(), r.top(), cw, ch) for _ in cards]

    # --- painting
    def paintEvent(self, e):
        self.layout_piles()
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        common.felt(p, self.rect(), self.win.store.opt("felt", "#1f6b33"))
        g = self.g
        dragging = self.drag[2] if self.drag and self.drag[5] else []
        for pile, r in self.rects.items():
            label = ""
            if pile.startswith("f") and g.kind != "spider":
                label = "A"
            elif pile == "stock" and g.kind == "klondike":
                label = "↻" if g.piles["waste"] else ""
            if not (g.kind == "spider" and (pile.startswith("f") or pile == "stock")):
                p.drawPixmap(QRectF(r.left(), r.top(), self.deck.w, self.deck.h).toRect(), self.deck.slot(label))
            cards = g.piles[pile]
            rects = self.card_rects(pile)
            for k, (c, cr) in enumerate(zip(cards, rects)):
                if self.drag and self.drag[5] and pile == self.drag[0] and k >= self.drag[1]:
                    break
                if g.kind == "spider" and pile.startswith("f") and k < len(cards) - 1:
                    continue
                pm = self.deck.face(c[0], c[1]) if c[2] else self.deck.back()
                p.drawPixmap(cr.toRect(), pm)
        if self.hint:
            src, i, dst = self.hint
            p.setPen(QPen(QColor(255, 230, 90), 3))
            p.setBrush(QColor(255, 230, 90, 50))
            rects = self.card_rects(src)
            if src in rects or i < len(rects):
                a = rects[i] if i < len(rects) else self.rects[src]
                p.drawRoundedRect(QRectF(a.left(), a.top(), self.deck.w, rects[-1].bottom() - a.top()).adjusted(-2, -2, 2, 2),
                                  6, 6)
            d = self.card_rects(dst)
            b = d[-1] if d else QRectF(self.rects[dst].topLeft(), QRectF(0, 0, self.deck.w, self.deck.h).size())
            p.drawRoundedRect(b.adjusted(-2, -2, 2, 2), 6, 6)
        if dragging:
            pos = self.drag[4] - self.drag[3]
            for k, c in enumerate(dragging):
                y = pos.y() + k * self.deck.h * 0.25
                p.setOpacity(0.35)
                p.fillRect(QRectF(pos.x() + 4, y + 5, self.deck.w, self.deck.h), QColor(0, 0, 0))
                p.setOpacity(1)
                p.drawPixmap(QRectF(pos.x(), y, self.deck.w, self.deck.h).toRect(), self.deck.face(c[0], c[1]))
        for f in self.fountain:
            p.drawPixmap(QRectF(f["x"], f["y"], self.deck.w, self.deck.h).toRect(), f["pm"])

    # --- hit testing
    def hit(self, pos):
        """(pile, card index or None) under a point; topmost card wins."""
        for pile in self.rects:
            rects = self.card_rects(pile)
            for k in range(len(rects) - 1, -1, -1):
                if rects[k].contains(pos):
                    return pile, k
            r = self.rects[pile]
            if QRectF(r.left(), r.top(), self.deck.w, self.deck.h).contains(pos):
                return pile, None
        return None, None

    def drop_target(self, cards_rect):
        best, area = None, 0
        for pile, r in self.rects.items():
            rects = self.card_rects(pile)
            target = rects[-1] if rects else QRectF(r.left(), r.top(), self.deck.w, self.deck.h)
            inter = target.intersected(cards_rect)
            a = inter.width() * inter.height()
            if a > area:
                best, area = pile, a
        return best

    # --- mouse
    def mousePressEvent(self, e):
        if self.fountain:
            return
        self.hint = None
        pos = e.position()
        pile, k = self.hit(pos)
        if pile is None or e.button() != Qt.LeftButton:
            return
        if pile == "stock":
            self.win.deal_stock()
            return
        if k is not None and self.g.can_pick(pile, k):
            r = self.card_rects(pile)[k]
            self.drag = [pile, k, self.g.piles[pile][k:], pos - r.topLeft(), pos, False]
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag:
            if not self.drag[5] and (e.position() - self.drag[4]).manhattanLength() > 4:
                self.drag[5] = True
            self.drag[4] = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        d, self.drag = self.drag, None
        if not d or not d[5]:
            self.update()
            return
        src, k, cards, grab, pos, _ = d
        topleft = pos - grab
        span = QRectF(topleft.x(), topleft.y(), self.deck.w, self.deck.h + (len(cards) - 1) * self.deck.h * 0.25)
        dst = self.drop_target(QRectF(topleft.x(), topleft.y(), self.deck.w, self.deck.h))
        if dst and dst != src and self.g.can_drop(cards, dst, src):
            self.win.do_move(src, k, dst)
        else:
            if dst and dst != src:
                self.win.sounds.play("bad")
        self.update()

    def mouseDoubleClickEvent(self, e):
        pile, k = self.hit(e.position())
        if pile is None or k is None or pile == "stock":
            return
        cards = self.g.piles[pile]
        if k != len(cards) - 1 or not self.g.can_pick(pile, k):
            return
        for f in self.g.foundations():
            if self.g.can_drop(cards[k:], f, pile):
                self.win.do_move(pile, k, f)
                return
        if self.g.kind == "freecell":                     # double-click parks a card in a free cell
            for c in (f"c{i}" for i in range(4)):
                if self.g.can_drop(cards[k:], c, pile):
                    self.win.do_move(pile, k, c)
                    return

    # --- the win
    def start_fountain(self, done):
        self.fountain_done = done
        self.fountain_queue = []
        for f in self.g.foundations():
            for c in reversed(self.g.piles[f] or []):
                self.fountain_queue.append((f, c))
        if self.g.kind == "spider":
            self.fountain_queue = [(f, c) for f in self.g.foundations() for c in self.g.piles[f]]
        self.fountain_t = 0
        self.fountain_timer.start()

    def step_fountain(self):
        H = self.height()
        self.fountain_t += 1
        if self.fountain_queue and self.fountain_t % 4 == 0:
            pile, c = self.fountain_queue.pop()
            r = self.rects[pile]
            self.fountain.append({"x": r.left(), "y": r.top(), "vx": random.uniform(-7, 7), "vy": random.uniform(-14, -6),
                                  "pm": self.deck.face(c[0], c[1])})
        for f in self.fountain:
            f["vy"] += 0.6
            f["x"] += f["vx"]
            f["y"] += f["vy"]
            if f["y"] > H - self.deck.h and f["vy"] > 0:
                f["y"] = H - self.deck.h
                f["vy"] *= -0.62
        self.fountain = [f for f in self.fountain if -self.deck.w < f["x"] < self.width()]
        self.update()
        if (not self.fountain_queue and not self.fountain) or self.fountain_t > 60 * 7:
            self.fountain_timer.stop()
            self.fountain = []
            self.update()
            self.fountain_done()


# ------------------------------------------------------------------- window ---
HELP = {
    "klondike": "Build four stacks in the top right, one per suit, from Ace up to King.\n\n"
                "On the table, stack cards downward in alternating colors (a red 6 on a black 7). "
                "Only a King can go into an empty column. Click the deck to turn over one card (or three).\n\n"
                "Drag a card or a run of cards to move it; double-click a card to send it up to its stack.",
    "spider": "Clear the table by building runs from King down to Ace in one suit - a finished run "
              "is put away by itself.\n\nAny card can go on a card one higher, but only runs of one suit "
              "can be moved together. Click the deck to deal a new card onto every column (every column "
              "needs a card first).",
    "freecell": "Move every card to the four stacks in the top right, by suit from Ace up.\n\n"
                "On the table, stack downward in alternating colors. The four cells in the top left each "
                "hold one card. You can move several cards at once if there are enough free cells and "
                "empty columns to have done it one at a time.\n\nEvery deal (1 to 1,000,000) can be won "
                "but a famous few. Game ▸ Select Game picks one by number.",
}


class Solitaire(common.GameWindow):
    NAME = "Solitaire"
    ICON = "kpat"

    def __init__(self, kind=None):
        super().__init__("solitaire")
        self.kind = kind or self.store.opt("game", "klondike")
        self.table = Table(self)
        self.setCentralWidget(self.table)
        self.undo_stack = []
        self.started = None
        self.standard_menu(undo=self.undo, hint=self.show_hint)
        games = self.game_menu.addMenu("Change Game")
        for label, k in (("Klondike", "klondike"), ("Spider", "spider"), ("FreeCell", "freecell")):
            self.add(games, label, lambda _=False, k=k: self.switch(k))
        self.select_action = self.add(self.game_menu, "Select Game…", self.select_game, "F3")
        self.score_label = QLabel()
        self.time_label = QLabel()
        self.moves_label = QLabel()
        sb = self.statusBar()
        sb.addPermanentWidget(self.score_label)
        sb.addPermanentWidget(self.time_label)
        sb.addPermanentWidget(self.moves_label)
        self.clock = QTimer(self, interval=1000, timeout=self.update_status)
        self.clock.start()
        self.resize(1000, 720)
        self.new_game()

    def variant(self):
        if self.kind == "klondike":
            return f"Klondike (draw {self.store.opt('draw', 1)})"
        if self.kind == "spider":
            n = self.store.opt("suits", 1)
            return f"Spider ({n} suit{'s' if n > 1 else ''})"
        return "FreeCell"

    def stats_label(self):
        return {"klondike": "Solitaire", "spider": "Spider Solitaire", "freecell": "FreeCell"}[self.kind]

    def in_progress(self):
        return self.game.moves > 0 and not self.game.won()

    def new_game(self, seed=None):
        if self.kind == "klondike":
            self.game = Klondike(seed, self.store.opt("draw", 1))
        elif self.kind == "spider":
            self.game = Spider(seed, self.store.opt("suits", 1))
        else:
            self.game = FreeCell(seed if seed is not None else random.randrange(1, 32001))
        self.undo_stack = []
        self.started = None
        self.table.hint = None
        name = self.stats_label()
        self.setWindowTitle(name + (f"  —  Game #{self.game.seed}" if self.kind == "freecell" else ""))
        self.select_action.setVisible(self.kind == "freecell")
        self.sounds.play("deal")
        self.update_status()
        self.table.update()
        self.autoplay()

    def switch(self, kind):
        if kind == self.kind:
            return
        if self.in_progress() and QMessageBox.question(self, "Change Game", "Leave this game? It will count as a "
                                                       "loss.") != QMessageBox.Yes:
            return
        if self.in_progress():
            self.store.record(self.variant(), False)
        self.kind = kind
        self.store.set_opt("game", kind)
        self.new_game()

    def select_game(self):
        n, ok = QInputDialog.getInt(self, "Select Game", "Game number (1 – 1000000):", self.game.seed, 1, 1_000_000)
        if ok:
            if self.in_progress():
                self.store.record(self.variant(), False)
            self.new_game(n)

    def options(self):
        d = QDialog(self)
        d.setWindowTitle("Options")
        lay = QVBoxLayout(d)
        group = QButtonGroup(d)
        if self.kind == "klondike":
            box = QGroupBox("Draw")
            bl = QVBoxLayout(box)
            for n, label in ((1, "Draw one card"), (3, "Draw three cards")):
                rb = QRadioButton(label, checked=self.store.opt("draw", 1) == n)
                rb.n = n
                group.addButton(rb)
                bl.addWidget(rb)
        elif self.kind == "spider":
            box = QGroupBox("Difficulty")
            bl = QVBoxLayout(box)
            for n, label in ((1, "Beginner: one suit"), (2, "Intermediate: two suits"), (4, "Advanced: four suits")):
                rb = QRadioButton(label, checked=self.store.opt("suits", 1) == n)
                rb.n = n
                group.addButton(rb)
                bl.addWidget(rb)
        else:
            box = QGroupBox("FreeCell")
            QVBoxLayout(box).addWidget(QLabel("Cards that are safe to put away go home by themselves."))
        lay.addWidget(box)
        felt_box = QGroupBox("Table")
        fl = QVBoxLayout(felt_box)
        fgroup = QButtonGroup(d)
        for col, label in (("#1f6b33", "Green felt"), ("#1d4f8a", "Blue felt"), ("#6b1f2a", "Burgundy felt")):
            rb = QRadioButton(label, checked=self.store.opt("felt", "#1f6b33") == col)
            rb.col = col
            fgroup.addButton(rb)
            fl.addWidget(rb)
        lay.addWidget(felt_box)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        if not d.exec():
            return
        if fgroup.checkedButton():
            self.store.set_opt("felt", fgroup.checkedButton().col)
        b = group.checkedButton()
        key = {"klondike": "draw", "spider": "suits"}.get(self.kind)
        if key and b and b.n != self.store.opt(key, 1):
            if self.in_progress():
                if QMessageBox.question(self, "Options", "The new setting starts a new game; this one counts as a "
                                        "loss. Continue?") != QMessageBox.Yes:
                    return
                self.store.record(self.variant(), False)
            self.store.set_opt(key, b.n)
            self.new_game()
        self.table.update()

    # --- playing
    def remember(self):
        self.undo_stack.append(self.game.snapshot())
        if self.started is None:
            self.started = time.monotonic()

    def do_move(self, src, i, dst, quiet=False):
        self.remember()
        ev = self.game.move(src, i, dst)
        if not quiet:
            self.sounds.play("flip" if "flip" in ev else "place")
        if "home" in ev:
            self.sounds.play("win" if self.game.won() else "click")
        self.after()

    def deal_stock(self):
        g = self.game
        snap = g.snapshot()
        r = g.deal_stock()
        if r is None:
            return
        if r == "empty":
            QMessageBox.information(self, "Spider Solitaire", "Every column needs at least one card before you can "
                                                              "deal.")
            return
        self.undo_stack.append(snap)
        if self.started is None:
            self.started = time.monotonic()
        if isinstance(g, Spider):
            g.collect()
        self.sounds.play("deal")
        self.after()

    def undo(self):
        if not self.undo_stack:
            return
        self.game.restore(self.undo_stack.pop())
        if self.kind == "spider":
            self.game.score = max(0, self.game.score - 1)
        self.table.hint = None
        self.sounds.play("click")
        self.table.update()
        self.update_status()

    def after(self):
        self.table.hint = None
        self.update_status()
        self.table.update()
        if self.game.won():
            return self.finish()
        if self.kind == "freecell":
            QTimer.singleShot(120, self.autoplay)
        elif self.kind == "klondike" and self.game.auto_finishable():
            QTimer.singleShot(150, self.auto_finish)
        elif self.game.stuck():
            QTimer.singleShot(200, self.no_moves)

    def autoplay(self):
        if self.kind != "freecell" or self.game.won():
            return
        m = self.game.safe_home()
        if m:
            self.game.move(*m)
            self.sounds.play("place")
            self.table.update()
            if self.game.won():
                return self.finish()
            QTimer.singleShot(90, self.autoplay)
        elif self.game.stuck() and self.game.moves:
            QTimer.singleShot(200, self.no_moves)

    def auto_finish(self):
        g = self.game
        for src in [t for t in g.tableau() if g.piles[t]]:
            k = len(g.piles[src]) - 1
            for f in g.foundations():
                if g.can_drop(g.piles[src][k:], f, src):
                    g.move(src, k, f)
                    self.sounds.play("place")
                    self.table.update()
                    self.update_status()
                    if g.won():
                        return self.finish()
                    QTimer.singleShot(70, self.auto_finish)
                    return

    def finish(self):
        secs = int(time.monotonic() - self.started) if self.started else 0
        score = None
        if self.kind == "klondike":
            score = self.game.score + (700000 // secs if secs > 30 else 0)
        elif self.kind == "spider":
            score = self.game.score
        self.started = None
        self.table.start_fountain(lambda: self.won(secs, score))

    def no_moves(self):
        if self.game.won() or not self.game.stuck():
            return
        box = QMessageBox(QMessageBox.Information, "No More Moves", "There are no more moves left.", parent=self)
        undo = box.addButton("Undo", QMessageBox.RejectRole)
        new = box.addButton("New Game", QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() is new:
            self.store.record(self.variant(), False)
            self.new_game()
        elif box.clickedButton() is undo:
            self.undo()

    def show_hint(self):
        moves = self.game.legal_moves()
        if moves:
            src, i, dst, _ = moves[0]
            self.table.hint = (src, i, dst)
            self.sounds.play("click")
            QTimer.singleShot(1500, lambda: (setattr(self.table, "hint", None), self.table.update()))
        elif self.game.can_deal():
            self.statusBar().showMessage("Deal more cards from the deck.", 2500)
        else:
            self.no_moves()
        self.table.update()

    def update_status(self):
        secs = int(time.monotonic() - self.started) if self.started else 0
        self.score_label.setText(f"Score: {self.game.score}" if self.kind != "freecell" else
                                 f"Cards left: {sum(len(self.game.piles[k]) for k in self.game.piles if not k.startswith('f'))}")
        self.time_label.setText(f"Time: {common.fmt_time(secs)}")
        self.moves_label.setText(f"Moves: {self.game.moves}")

    HELP = ""

    def how_to_play(self):
        QMessageBox.information(self, f"How to Play {self.stats_label()}", HELP[self.kind])


def main():
    kind = None
    if "--game" in sys.argv:
        i = sys.argv.index("--game")
        kind = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    return common.run(lambda: Solitaire(kind if kind in ("klondike", "spider", "freecell") else None),
                      "halcyon-solitaire")


if __name__ == "__main__":
    sys.exit(main())
