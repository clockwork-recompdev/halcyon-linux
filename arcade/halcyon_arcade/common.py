"""What every Halcyon game shares: the window and its Game / Help menus,
statistics, the "you won" dialog, options storage and sounds."""
import json
import math
import os
import random
import struct
import sys
import time
import wave
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QLinearGradient, QPainter, QPalette
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

try:
    from PySide6.QtMultimedia import QSoundEffect
except ImportError:
    QSoundEffect = None

DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "games"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "halcyon-games-pack"
TEXT = "#1e395b"
SOUND_VERSION = 1


# ------------------------------------------------------------- persistence ---
class Store:
    """One JSON file per game: options and statistics per variant."""
    def __init__(self, game):
        self.path = DATA / f"{game}.json"
        try:
            self.d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.d = {}
        self.d.setdefault("options", {})
        self.d.setdefault("stats", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.d, indent=1))

    def opt(self, key, default=None):
        return self.d["options"].get(key, default)

    def set_opt(self, key, value):
        self.d["options"][key] = value
        self.save()

    def stats(self, variant):
        return self.d["stats"].setdefault(variant, {"played": 0, "won": 0, "streak": 0, "best_streak": 0,
                                                    "worst_streak": 0, "best_time": None, "best_score": None})

    def record(self, variant, won, seconds=None, score=None):
        s = self.stats(variant)
        s["played"] += 1
        if won:
            s["won"] += 1
            s["streak"] = s["streak"] + 1 if s["streak"] > 0 else 1
            s["best_streak"] = max(s["best_streak"], s["streak"])
            if seconds is not None and (s["best_time"] is None or seconds < s["best_time"]):
                s["best_time"] = seconds
            if score is not None and (s["best_score"] is None or score > s["best_score"]):
                s["best_score"] = score
        else:
            s["streak"] = s["streak"] - 1 if s["streak"] < 0 else -1
            s["worst_streak"] = min(s["worst_streak"], s["streak"])
        self.save()
        return s


def fmt_time(s):
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d}"


# ------------------------------------------------------------------ sounds ---
RATE = 44100


def _tone(n, f, amp, attack, decay, glide=None, start=0):
    out = [0.0] * n
    o = int(start * RATE)
    ph = 0.0
    for i in range(o, n):
        t = (i - o) / RATE
        fr = f * (glide ** min(1.0, t / 0.06)) if glide else f
        ph += 2 * math.pi * fr / RATE
        env = min(1.0, t / attack) * math.exp(-t / decay)
        out[i] = amp * env * (math.sin(ph) + 0.25 * math.sin(2 * ph))
    return out


def _noise(n, amp, decay, lp, seed, start=0):
    rnd = random.Random(seed)
    out, y = [0.0] * n, 0.0
    o = int(start * RATE)
    for i in range(o, n):
        t = (i - o) / RATE
        y += lp * (rnd.uniform(-1, 1) - y)
        out[i] = amp * y * math.exp(-t / decay) * min(1.0, t / 0.002)
    return out


def _mix(*xs):
    n = max(len(x) for x in xs)
    return [sum(x[i] for x in xs if i < len(x)) for i in range(n)]


def _sounds():
    n = lambda s: int(RATE * s)
    E5, G5, B5, E6, G6, B6 = 659.3, 784.0, 987.8, 1318.5, 1568.0, 1975.5
    return {
        # a card landing: a short papery tick with a soft glassy click on top
        "place": _mix(_noise(n(0.09), 0.5, 0.012, 0.55, 1), _tone(n(0.09), 2200, 0.08, 0.001, 0.01)),
        "flip": _mix(_noise(n(0.12), 0.35, 0.02, 0.35, 2), _tone(n(0.12), 900, 0.12, 0.002, 0.02, glide=1.5)),
        "deal": _mix(*[_noise(n(0.4), 0.3, 0.012, 0.5, 3 + k, start=k * 0.05) for k in range(6)]),
        "bad": _mix(_tone(n(0.2), 196, 0.4, 0.004, 0.04), _tone(n(0.2), 185, 0.25, 0.004, 0.04, start=0.05)),
        "click": _tone(n(0.1), 1250, 0.25, 0.002, 0.02),
        "boom": _mix(_noise(n(0.9), 0.9, 0.25, 0.08, 9), _tone(n(0.9), 60, 0.6, 0.005, 0.2, glide=0.6)),
        "win": _mix(*[_tone(n(1.6), f, 0.16, 0.006, 0.45, start=k * 0.09)
                      for k, f in enumerate((E5, G5, B5, E6, G6, B6))]),
        "move": _tone(n(0.12), 520, 0.3, 0.004, 0.03, glide=1.25),
        "capture": _mix(_noise(n(0.15), 0.4, 0.03, 0.3, 5), _tone(n(0.15), 330, 0.3, 0.003, 0.04)),
    }


def _write(path, samples):
    peak = max(1e-9, max(abs(v) for v in samples))
    g = 0.85 / peak
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v * g)) * 32767)) for v in samples))


class Sounds:
    def __init__(self, parent, store):
        self.store = store
        self.fx = {}
        if QSoundEffect is None:
            return
        folder = CACHE / f"sfx-{SOUND_VERSION}"
        want = ("place", "flip", "deal", "bad", "click", "boom", "win", "move", "capture")
        if not all((folder / f"{k}.wav").exists() for k in want):
            folder.mkdir(parents=True, exist_ok=True)
            for k, v in _sounds().items():
                _write(folder / f"{k}.wav", v)
        for k in want:
            e = QSoundEffect(parent)
            e.setSource(QUrl.fromLocalFile(str(folder / f"{k}.wav")))
            e.setVolume(0.55)
            self.fx[k] = e

    def play(self, name):
        if self.store.opt("sound", True) and name in self.fx:
            self.fx[name].stop()
            self.fx[name].play()


# ------------------------------------------------------------------ window ---
QSS = f"""
QMainWindow {{ background: #1d5a2c; }}
QMenuBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f6f9fd, stop:1 #dbe6f4);
    border-bottom: 1px solid #a9bfd8; color: {TEXT}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: #d4e6fa; border: 1px solid #a8c8ec; border-radius: 2px; }}
QMenu {{ background: #f7f9fc; border: 1px solid #97a8c0; padding: 2px; color: black; }}
QMenu::item {{ padding: 4px 26px 4px 26px; }}
QMenu::item:selected {{ background: #d9e9fb; border: 1px solid #a8c8ec; border-radius: 2px; }}
QMenu::separator {{ height: 1px; background: #d0d8e4; margin: 3px 4px 3px 26px; }}
QStatusBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f1f5fa, stop:1 #dfe8f2);
    color: {TEXT}; border-top: 1px solid #a9bfd8; }}
QStatusBar QLabel {{ color: {TEXT}; padding: 0 10px; }}
QDialog {{ background: #f3f7fc; }}
QDialog QLabel {{ color: #1e1e1e; }}
QPushButton {{ border: 1px solid #8ea6c4; border-radius: 3px; padding: 4px 16px; color: #1e1e1e;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:0.5 #eef3f9, stop:0.51 #dfe8f3,
    stop:1 #e9f0f8); }}
QPushButton:hover {{ border-color: #3c7fb1; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 #f4faff, stop:0.5 #dcefff, stop:0.51 #c6e3fc, stop:1 #d9ecfc); }}
QPushButton:default {{ border-color: #3c7fb1; }}
QRadioButton, QCheckBox {{ color: #1e1e1e; }}
"""


def felt(p, rect, color="#1f6b33"):
    """The green table, lit from the middle."""
    from PySide6.QtGui import QRadialGradient
    c = QColor(color)
    g = QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.75)
    g.setColorAt(0, c.lighter(125))
    g.setColorAt(1, c.darker(150))
    p.fillRect(rect, g)


class GameWindow(QMainWindow):
    """Base window: subclasses fill self.board and implement new_game()/options()."""
    NAME = "Game"
    ICON = "applications-games"

    def __init__(self, game_id):
        super().__init__()
        self.store = Store(game_id)
        self.sounds = Sounds(self, self.store)
        self.setWindowTitle(self.NAME)
        self.setWindowIcon(QIcon.fromTheme(self.ICON))
        self.game_menu = self.menuBar().addMenu("&Game")
        self.help_menu = self.menuBar().addMenu("&Help")
        self.help_menu.addAction("How to Play", self.how_to_play, QKeySequence("F1"))
        self.help_menu.addAction(f"About {self.NAME}", lambda: QMessageBox.about(
            self, self.NAME, f"<b>{self.NAME}</b><p>Part of the Halcyon games pack.</p>"))

    def add(self, menu, text, slot, key=None, checkable=False, checked=False):
        a = QAction(text, self)
        if key:
            a.setShortcut(QKeySequence(key))
        a.setCheckable(checkable)
        a.setChecked(checked)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def standard_menu(self, undo=None, hint=None):
        self.add(self.game_menu, "New Game", self.confirm_new, "F2")
        self.game_menu.addSeparator()
        if undo:
            self.add(self.game_menu, "Undo", undo, "Ctrl+Z")
        if hint:
            self.add(self.game_menu, "Hint", hint, "H")
        self.game_menu.addSeparator()
        self.add(self.game_menu, "Statistics", self.show_stats, "F4")
        self.add(self.game_menu, "Options", self.options, "F5")
        snd = self.add(self.game_menu, "Sound", lambda on: self.store.set_opt("sound", on), checkable=True,
                       checked=self.store.opt("sound", True))
        snd.setShortcut(QKeySequence("F6"))
        self.game_menu.addSeparator()
        self.add(self.game_menu, "Exit", self.close)

    # subclasses
    def in_progress(self):
        return False

    def variant(self):
        return "default"

    def confirm_new(self):
        if self.in_progress():
            r = QMessageBox.question(self, "New Game", "This game is not finished. Start a new one? "
                                     "It will count as a loss.", QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
            if r == QMessageBox.Yes:            # Yes = give this one up (it counts as a loss) and deal again
                self.store.record(self.variant(), False)
        self.new_game()

    def closeEvent(self, e):
        if self.in_progress():
            r = QMessageBox.question(self, "Exit Game", "This game is not finished. Do you want to quit? "
                                     "It will count as a loss.", QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.store.record(self.variant(), False)
        e.accept()

    def show_stats(self, highlight=None):
        StatsDialog(self, self.store, self.variant(), self.stats_label()).exec()

    def stats_label(self):
        return self.NAME

    def won(self, seconds=None, score=None):
        s = self.store.record(self.variant(), True, seconds, score)
        self.sounds.play("win")
        d = QDialog(self)
        d.setWindowTitle("Game Won")
        lay = QVBoxLayout(d)
        title = QLabel("Congratulations, you won!")
        f = title.font()
        f.setPointSizeF(14)
        title.setFont(f)
        title.setStyleSheet(f"color: {TEXT};")
        lay.addWidget(title)
        rows = []
        if seconds is not None:
            rows.append(("Time", fmt_time(seconds) + ("  (best!)" if s["best_time"] == seconds else "")))
        if score is not None:
            rows.append(("Score", f"{score}" + ("  (best!)" if s["best_score"] == score else "")))
        rows += [("Games won", f"{s['won']} of {s['played']} ({round(100 * s['won'] / max(1, s['played']))}%)"),
                 ("Winning streak", str(max(0, s["streak"])))]
        grid = QGridLayout()
        for i, (k, v) in enumerate(rows):
            grid.addWidget(QLabel(k + ":"), i, 0)
            grid.addWidget(QLabel(f"<b>{v}</b>"), i, 1)
        lay.addLayout(grid)
        bb = QDialogButtonBox()
        again = bb.addButton("Play again", QDialogButtonBox.AcceptRole)
        bb.addButton("Exit", QDialogButtonBox.RejectRole)
        again.setDefault(True)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        if d.exec():
            self.new_game()
        else:
            self.close()

    def lost(self, message="Sorry, you lost this game."):
        self.store.record(self.variant(), False)
        r = QMessageBox.question(self, "Game Lost", message + "\n\nPlay again?", QMessageBox.Yes | QMessageBox.No)
        if r == QMessageBox.Yes:
            self.new_game()

    def how_to_play(self):
        QMessageBox.information(self, f"How to Play {self.NAME}", self.HELP)

    HELP = ""


class StatsDialog(QDialog):
    def __init__(self, parent, store, variant, label):
        super().__init__(parent)
        self.setWindowTitle(f"{label} Statistics")
        self.store, self.variant = store, variant
        lay = QVBoxLayout(self)
        self.text = QLabel()
        lay.addWidget(self.text)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        reset = bb.addButton("Reset", QDialogButtonBox.ResetRole)
        reset.clicked.connect(self.reset)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.fill()

    def fill(self):
        s = self.store.stats(self.variant)
        pct = round(100 * s["won"] / s["played"]) if s["played"] else 0
        rows = [("Games played", s["played"]), ("Games won", s["won"]), ("Win percentage", f"{pct}%"),
                ("Longest winning streak", s["best_streak"]), ("Longest losing streak", -s["worst_streak"]),
                ("Current streak", f"{abs(s['streak'])} {'win' if s['streak'] > 0 else 'loss'}"
                 f"{'es' if s['streak'] < -1 else 's' if s['streak'] > 1 else ''}" if s["streak"] else "0")]
        if s.get("best_time") is not None:
            rows.append(("Best time", fmt_time(s["best_time"])))
        if s.get("best_score") is not None:
            rows.append(("High score", s["best_score"]))
        v = "" if self.variant == "default" else f"<p style='color:{TEXT}'><b>{self.variant}</b></p>"
        self.text.setText(v + "<table cellspacing=4>" + "".join(f"<tr><td>{k}:</td><td><b>{val}</b></td></tr>"
                                                                for k, val in rows) + "</table>")

    def reset(self):
        if QMessageBox.question(self, "Reset Statistics", "Reset the statistics for this game?") == QMessageBox.Yes:
            self.store.d["stats"].pop(self.variant, None)
            self.store.save()
            self.fill()


def light_palette():
    pal = QPalette()
    for role, color in ((QPalette.Window, "#f3f7fc"), (QPalette.WindowText, "#000000"), (QPalette.Base, "#ffffff"),
                        (QPalette.Text, "#000000"), (QPalette.Button, "#eef3f9"), (QPalette.ButtonText, "#000000"),
                        (QPalette.Highlight, "#3399ff"), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.ToolTipBase, "#ffffe1"), (QPalette.ToolTipText, "#000000")):
        pal.setColor(role, QColor(color))
    return pal


def run(window_cls, app_id):
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(app_id)
    app.setDesktopFileName(app_id)
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(QSS)
    f = app.font()
    for fam in ("Segoe UI", "Selawik", "Noto Sans"):
        if fam in QFontDatabase.families():
            f.setFamily(fam)
            break
    f.setPointSizeF(9)
    app.setFont(f)
    w = window_cls()
    w.show()
    return app.exec()
