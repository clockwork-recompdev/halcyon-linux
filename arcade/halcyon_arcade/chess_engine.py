"""Chess rules and a computer opponent, in plain Python.

Board: 64 squares, a1 = 0 ... h8 = 63. Pieces are letters: uppercase white
(PNBRQK), lowercase black, "." empty. Moves are (from, to, promotion)."""
import random
import time

FILES = "abcdefgh"
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
VALUE = {"P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 0}
KNIGHT = [(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)]
KING = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
ROOK_DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
BISHOP_DIRS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]

# Piece-square tables (white's view, a8..h1 rows as written, flipped for lookup).
PST = {
    "P": [0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50, 10, 10, 20, 30, 30, 20, 10, 10, 5, 5, 10, 25, 25, 10, 5, 5,
          0, 0, 0, 20, 20, 0, 0, 0, 5, -5, -10, 0, 0, -10, -5, 5, 5, 10, 10, -20, -20, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0],
    "N": [-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 0, 0, 0, -20, -40, -30, 0, 10, 15, 15, 10, 0, -30, -30, 5, 15,
          20, 20, 15, 5, -30, -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 10, 15, 15, 10, 5, -30, -40, -20, 0, 5, 5, 0, -20, -40,
          -50, -40, -30, -30, -30, -30, -40, -50],
    "B": [-20, -10, -10, -10, -10, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 10, 10, 5, 0, -10, -10, 5, 5, 10, 10,
          5, 5, -10, -10, 0, 10, 10, 10, 10, 0, -10, -10, 10, 10, 10, 10, 10, 10, -10, -10, 5, 0, 0, 0, 0, 5, -10, -20, -10,
          -10, -10, -10, -10, -10, -20],
    "R": [0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, 10, 10, 10, 10, 5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0,
          0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, 0, 0, 0, 5, 5, 0, 0, 0],
    "Q": [-20, -10, -10, -5, -5, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 5, 5, 5, 0, -10, -5, 0, 5, 5, 5, 5, 0,
          -5, 0, 0, 5, 5, 5, 5, 0, -5, -10, 5, 5, 5, 5, 5, 0, -10, -10, 0, 5, 0, 0, 0, 0, -10, -20, -10, -10, -5, -5, -10,
          -10, -20],
    "K": [-30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40,
          -30, -30, -40, -40, -50, -50, -40, -40, -30, -20, -30, -30, -40, -40, -30, -30, -20, -10, -20, -20, -20, -20, -20,
          -20, -10, 20, 20, 0, 0, 0, 0, 20, 20, 20, 30, 10, 0, 0, 10, 30, 20],
    "K_END": [-50, -40, -30, -20, -20, -30, -40, -50, -30, -20, -10, 0, 0, -10, -20, -30, -30, -10, 20, 30, 30, 20, -10, -30,
              -30, -10, 30, 40, 40, 30, -10, -30, -30, -10, 30, 40, 40, 30, -10, -30, -30, -10, 20, 30, 30, 20, -10, -30,
              -30, -30, 0, 0, 0, 0, -30, -30, -50, -30, -30, -30, -30, -30, -30, -50],
}


def sq(name):
    return FILES.index(name[0]) + 8 * (int(name[1]) - 1)


def name(s):
    return FILES[s % 8] + str(s // 8 + 1)


def white(p):
    return p.isupper()


class Position:
    def __init__(self, fen=START):
        self.set_fen(fen)

    def set_fen(self, fen):
        parts = fen.split()
        rows = parts[0].split("/")
        self.b = ["."] * 64
        for r, row in enumerate(rows):
            f = 0
            for ch in row:
                if ch.isdigit():
                    f += int(ch)
                else:
                    self.b[(7 - r) * 8 + f] = ch
                    f += 1
        self.turn = parts[1] if len(parts) > 1 else "w"
        self.castle = parts[2] if len(parts) > 2 and parts[2] != "-" else ""
        self.ep = sq(parts[3]) if len(parts) > 3 and parts[3] != "-" else None
        self.half = int(parts[4]) if len(parts) > 4 else 0
        self.full = int(parts[5]) if len(parts) > 5 else 1
        self.history = []                      # undo records
        self.keys = [self.key()]

    def fen(self):
        rows = []
        for r in range(7, -1, -1):
            row, empty = "", 0
            for f in range(8):
                p = self.b[r * 8 + f]
                if p == ".":
                    empty += 1
                else:
                    row += (str(empty) if empty else "") + p
                    empty = 0
            rows.append(row + (str(empty) if empty else ""))
        return (f"{'/'.join(rows)} {self.turn} {self.castle or '-'} {name(self.ep) if self.ep is not None else '-'} "
                f"{self.half} {self.full}")

    def key(self):
        return "".join(self.b) + self.turn + self.castle + (str(self.ep) if self.ep is not None else "")

    def copy(self):
        p = Position.__new__(Position)
        p.b, p.turn, p.castle, p.ep = self.b[:], self.turn, self.castle, self.ep
        p.half, p.full, p.history, p.keys = self.half, self.full, [], self.keys[:]
        return p

    # --- attacks
    def attacked(self, s, by_white):
        b = self.b
        f, r = s % 8, s // 8
        pawn = "P" if by_white else "p"
        dr = -1 if by_white else 1                 # a white pawn attacks from one rank below
        for df in (-1, 1):
            ff, rr = f + df, r + dr
            if 0 <= ff < 8 and 0 <= rr < 8 and b[rr * 8 + ff] == pawn:
                return True
        for df, dr2 in KNIGHT:
            ff, rr = f + df, r + dr2
            if 0 <= ff < 8 and 0 <= rr < 8 and b[rr * 8 + ff] == ("N" if by_white else "n"):
                return True
        for df, dr2 in KING:
            ff, rr = f + df, r + dr2
            if 0 <= ff < 8 and 0 <= rr < 8 and b[rr * 8 + ff] == ("K" if by_white else "k"):
                return True
        for dirs, pieces in ((ROOK_DIRS, "RQ"), (BISHOP_DIRS, "BQ")):
            want = pieces if by_white else pieces.lower()
            for df, dr2 in dirs:
                ff, rr = f + df, r + dr2
                while 0 <= ff < 8 and 0 <= rr < 8:
                    p = b[rr * 8 + ff]
                    if p != ".":
                        if p in want:
                            return True
                        break
                    ff += df
                    rr += dr2
        return False

    def king(self, w):
        return self.b.index("K" if w else "k")

    def in_check(self, w=None):
        w = (self.turn == "w") if w is None else w
        return self.attacked(self.king(w), not w)

    # --- move generation
    def pseudo(self, captures_only=False):
        b, w = self.b, self.turn == "w"
        out = []
        for s, p in enumerate(b):
            if p == "." or white(p) != w:
                continue
            f, r = s % 8, s // 8
            kind = p.upper()
            if kind == "P":
                d = 1 if w else -1
                last = 7 if w else 0
                t = s + 8 * d
                if 0 <= t < 64 and b[t] == "." and not captures_only:
                    if t // 8 == last:
                        out += [(s, t, q) for q in "QRBN"]
                    else:
                        out.append((s, t, None))
                        start = 1 if w else 6
                        if r == start and b[t + 8 * d] == ".":
                            out.append((s, t + 8 * d, None))
                for df in (-1, 1):
                    ff = f + df
                    if not 0 <= ff < 8:
                        continue
                    t = (r + d) * 8 + ff
                    if not 0 <= t < 64:
                        continue
                    if (b[t] != "." and white(b[t]) != w) or t == self.ep:
                        if t // 8 == last:
                            out += [(s, t, q) for q in "QRBN"]
                        else:
                            out.append((s, t, None))
            elif kind in "NK":
                for df, dr in (KNIGHT if kind == "N" else KING):
                    ff, rr = f + df, r + dr
                    if 0 <= ff < 8 and 0 <= rr < 8:
                        t = rr * 8 + ff
                        if b[t] == "." and not captures_only or (b[t] != "." and white(b[t]) != w):
                            out.append((s, t, None))
                if kind == "K" and not captures_only:
                    out += self.castles(w)
            else:
                dirs = ROOK_DIRS if kind == "R" else BISHOP_DIRS if kind == "B" else ROOK_DIRS + BISHOP_DIRS
                for df, dr in dirs:
                    ff, rr = f + df, r + dr
                    while 0 <= ff < 8 and 0 <= rr < 8:
                        t = rr * 8 + ff
                        if b[t] == ".":
                            if not captures_only:
                                out.append((s, t, None))
                        else:
                            if white(b[t]) != w:
                                out.append((s, t, None))
                            break
                        ff += df
                        rr += dr
        return out

    def castles(self, w):
        out, b = [], self.b
        home = 0 if w else 56
        if self.in_check(w):
            return out
        if ("K" if w else "k") in self.castle and b[home + 5] == b[home + 6] == "." and b[home + 7] == ("R" if w else "r") \
                and not self.attacked(home + 5, not w) and not self.attacked(home + 6, not w):
            out.append((home + 4, home + 6, None))
        if ("Q" if w else "q") in self.castle and b[home + 1] == b[home + 2] == b[home + 3] == "." \
                and b[home] == ("R" if w else "r") and not self.attacked(home + 3, not w) \
                and not self.attacked(home + 2, not w):
            out.append((home + 4, home + 2, None))
        return out

    def legal(self):
        w = self.turn == "w"
        out = []
        for m in self.pseudo():
            self.push(m)
            if not self.in_check(w):
                out.append(m)
            self.pop()
        return out

    # --- making moves
    def push(self, m):
        s, t, promo = m
        b = self.b
        p, cap = b[s], b[t]
        rec = (m, p, cap, self.castle, self.ep, self.half, self.full, None)
        w = white(p)
        kind = p.upper()
        ep_cap = None
        if kind == "P" and t == self.ep:
            ep_cap = t - 8 if w else t + 8
            cap = b[ep_cap]
            b[ep_cap] = "."
            rec = rec[:2] + (cap,) + rec[3:7] + (ep_cap,)
        b[t] = (promo if w else promo.lower()) if promo else p
        b[s] = "."
        if kind == "K" and abs(t - s) == 2:
            if t > s:
                b[t - 1], b[t + 1] = b[t + 1], "."
            else:
                b[t + 1], b[t - 2] = b[t - 2], "."
        # castling rights
        c = self.castle
        for corner, right in ((0, "Q"), (7, "K"), (56, "q"), (63, "k")):
            if s == corner or t == corner:
                c = c.replace(right, "")
        if kind == "K":
            c = c.replace("K", "").replace("Q", "") if w else c.replace("k", "").replace("q", "")
        self.castle = c
        self.ep = (s + t) // 2 if kind == "P" and abs(t - s) == 16 else None
        self.half = 0 if kind == "P" or cap != "." else self.half + 1
        if not w:
            self.full += 1
        self.turn = "b" if w else "w"
        self.history.append(rec)
        self.keys.append(self.key())

    def pop(self):
        m, p, cap, castle, ep, half, full, ep_cap = self.history.pop()
        self.keys.pop()
        s, t, promo = m
        b = self.b
        b[s] = p
        if ep_cap is not None:
            b[t] = "."
            b[ep_cap] = cap
        else:
            b[t] = cap
        if p.upper() == "K" and abs(t - s) == 2:
            if t > s:
                b[t + 1], b[t - 1] = b[t - 1], "."
            else:
                b[t - 2], b[t + 1] = b[t + 1], "."
        self.castle, self.ep, self.half, self.full = castle, ep, half, full
        self.turn = "w" if white(p) else "b"

    # --- game end
    def outcome(self):
        """None while the game goes on, else (result, reason): result "1-0", "0-1" or "1/2-1/2"."""
        if not self.legal():
            if self.in_check():
                return ("0-1" if self.turn == "w" else "1-0", "checkmate")
            return ("1/2-1/2", "stalemate")
        if self.half >= 100:
            return ("1/2-1/2", "the fifty-move rule")
        if self.keys.count(self.keys[-1]) >= 3:
            return ("1/2-1/2", "threefold repetition")
        pieces = [p for p in self.b if p not in ".Kk"]
        if not pieces or (len(pieces) == 1 and pieces[0] in "NBnb"):
            return ("1/2-1/2", "insufficient material")
        return None

    # --- notation
    def san(self, m):
        s, t, promo = m
        p = self.b[s]
        kind = p.upper()
        if kind == "K" and abs(t - s) == 2:
            text = "O-O" if t > s else "O-O-O"
        else:
            cap = self.b[t] != "." or (kind == "P" and t == self.ep)
            if kind == "P":
                text = (FILES[s % 8] + "x" if cap else "") + name(t) + (f"={promo}" if promo else "")
            else:
                others = [o for o in self.legal() if o[1] == t and o[0] != s and self.b[o[0]] == p]
                dis = ""
                if others:
                    if all(o[0] % 8 != s % 8 for o in others):
                        dis = FILES[s % 8]
                    elif all(o[0] // 8 != s // 8 for o in others):
                        dis = str(s // 8 + 1)
                    else:
                        dis = name(s)
                text = kind + dis + ("x" if cap else "") + name(t)
        self.push(m)
        if self.in_check():
            text += "#" if not self.legal() else "+"
        self.pop()
        return text


# ---------------------------------------------------------------- the engine ---
def evaluate(pos):
    """Material and placement, from the side to move's view."""
    b = pos.b
    queens = sum(1 for p in b if p in "Qq")
    minors = sum(1 for p in b if p in "NBRnbr")
    endgame = queens == 0 or (queens <= 2 and minors <= 2)
    score = 0
    for s, p in enumerate(b):
        if p == ".":
            continue
        kind = p.upper()
        table = PST["K_END"] if kind == "K" and endgame else PST[kind]
        if white(p):
            score += VALUE[kind] + table[(7 - s // 8) * 8 + s % 8]
        else:
            score -= VALUE[kind] + table[(s // 8) * 8 + s % 8]
    return score if pos.turn == "w" else -score


class Engine:
    """Alpha-beta with iterative deepening, move ordering and a capture search."""
    LEVELS = {1: (1, 0.2, 120), 2: (2, 0.6, 40), 3: (3, 1.5, 12), 4: (5, 3.5, 0)}   # depth, seconds, noise

    def __init__(self, level=2):
        self.level = level
        self.nodes = 0

    def order(self, pos, moves, best=None):
        def key(m):
            if m == best:
                return -100000
            victim = pos.b[m[1]]
            k = 0
            if victim != ".":
                k -= 10 * VALUE[victim.upper()] - VALUE[pos.b[m[0]].upper()] // 10
            if m[2]:
                k -= 800
            return k
        return sorted(moves, key=key)

    def quiesce(self, pos, alpha, beta, depth=0):
        self.nodes += 1
        stand = evaluate(pos)
        if stand >= beta:
            return beta
        alpha = max(alpha, stand)
        if depth > 6:
            return alpha
        w = pos.turn == "w"
        for m in self.order(pos, pos.pseudo(captures_only=True)):
            pos.push(m)
            if pos.in_check(w):
                pos.pop()
                continue
            score = -self.quiesce(pos, -beta, -alpha, depth + 1)
            pos.pop()
            if score >= beta:
                return beta
            alpha = max(alpha, score)
        return alpha

    def search(self, pos, depth, alpha, beta, ply):
        if time.monotonic() > self.deadline:
            raise TimeoutError
        if ply and (pos.keys.count(pos.keys[-1]) >= 2 or pos.half >= 100):
            return 0
        if depth <= 0:
            return self.quiesce(pos, alpha, beta)
        self.nodes += 1
        w = pos.turn == "w"
        best, any_legal = -10 ** 9, False
        for m in self.order(pos, pos.pseudo(), self.tt.get(pos.keys[-1])):
            pos.push(m)
            if pos.in_check(w):
                pos.pop()
                continue
            any_legal = True
            score = -self.search(pos, depth - 1 + (1 if pos.in_check() and ply < 6 else 0), -beta, -alpha, ply + 1)
            pos.pop()
            if score > best:
                best = score
                if score > alpha:
                    alpha = score
                    self.tt[pos.keys[-1]] = m
            if alpha >= beta:
                break
        if not any_legal:
            return -100000 + ply if pos.in_check() else 0
        return best

    def choose(self, pos, rnd=random):
        max_depth, seconds, noise = self.LEVELS[self.level]
        legal = pos.legal()
        if not legal:
            return None
        if len(legal) == 1:
            return legal[0]
        pos = pos.copy()
        self.tt = {}
        self.deadline = time.monotonic() + seconds
        scored = {m: 0 for m in legal}
        best = legal[0]
        for depth in range(1, max_depth + 1):
            try:
                results = {}
                alpha = -10 ** 9
                for m in self.order(pos, legal, best):
                    pos.push(m)
                    s = -self.search(pos, depth - 1, -10 ** 9, -alpha + (noise or 0) + 1, 1)
                    pos.pop()
                    results[m] = s
                    alpha = max(alpha, s)
                scored = results
                best = max(results, key=results.get)
                if results[best] > 90000:
                    break                     # found a mate
            except TimeoutError:
                break
        if noise:                              # weaker levels: pick among the near-best
            top = max(scored.values())
            near = [m for m, s in scored.items() if s >= top - noise]
            return rnd.choice(near)
        return best
