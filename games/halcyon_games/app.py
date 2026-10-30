"""Halcyon Games: one list for Steam, Windows (Wine) and Linux games, in a
classic game-client window, plus a full-screen menu for playing with a gamepad."""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPointF, QProcess, QProcessEnvironment, QRect, QRectF, QSize,
                            Qt, QThread, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QFontMetrics, QIcon, QKeySequence, QLinearGradient,
                           QPainter, QPen, QPixmap, QRadialGradient, QShortcut)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QSizeGrip, QStackedWidget, QTabBar,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import controller, library, sfx, sgdb, style

try:
    from .gamepad import Gamepads
except ImportError:              # python-evdev missing: keyboard and mouse only
    Gamepads = None

APP = "Halcyon Games"
TABS = ("All Games", "Favorites", "Recently Played")


# ------------------------------------------------------------- helpers ---
_pix = {}


def pixmap(path):
    """Cached QPixmap for a file path (null pixmap if missing)."""
    if not path:
        return QPixmap()
    p = _pix.get(path)
    if p is None:
        if len(_pix) > 120:
            _pix.clear()
        p = _pix[path] = QPixmap(path)
    return p


def game_icon(g):
    if g.icon and os.path.isabs(g.icon):
        if os.path.exists(g.icon):
            return QIcon(g.icon)
    elif g.icon:
        icon = QIcon.fromTheme(g.icon)
        if not icon.isNull():
            return icon
        # Wine's icons live in the user's hicolor theme, which not every platform theme searches.
        found = sorted(Path.home().glob(f".local/share/icons/hicolor/*/apps/{g.icon}.png"),
                       key=lambda p: int(p.parts[-3].split("x")[0]) if "x" in p.parts[-3] else 0)
        if found:
            return QIcon(str(found[-1]))
    return QIcon.fromTheme("applications-games", QIcon.fromTheme("input-gaming"))


def when(ts):
    if not ts:
        return "Never"
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    if day == time.strftime("%Y-%m-%d"):
        return "Today"
    if day == time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)):
        return "Yesterday"
    return time.strftime("%b %-d, %Y", time.localtime(ts))


def show_keyboard():
    """Open the on-screen keyboard (halcyon-osk), starting it if needed."""
    try:
        subprocess.Popen([sys.executable, "-m", "halcyon_games.osk", "show"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class Item(QTreeWidgetItem):
    """Sorts the 'Last played' column by time, not by its text."""
    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        if col == 2:
            return self.data(2, Qt.UserRole) < other.data(2, Qt.UserRole)
        return self.text(col).casefold() < other.text(col).casefold()


# ----------------------------------------------------------- title bar ---
class TitleBar(QWidget):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 4, 2)
        lay.setSpacing(2)
        icon = QLabel()
        icon.setPixmap(QIcon.fromTheme("applications-games").pixmap(16, 16))
        lay.addWidget(icon)
        lay.addSpacing(4)
        t = QLabel(APP, objectName="title")
        lay.addWidget(t)
        lay.addStretch(1)
        big = QPushButton("▣  BIG PICTURE", objectName="bigpic", toolTip="Full-screen mode for TVs and gamepads (F11)")
        big.setFocusPolicy(Qt.NoFocus)
        big.clicked.connect(lambda: win.toggle_big())
        lay.addWidget(big)
        lay.addSpacing(8)
        for glyph, tip, slot in (("_", "Minimize", win.showMinimized), ("□", "Maximize", self.toggle_max),
                                 ("×", "Close", win.close)):
            b = QToolButton(text=glyph, objectName="cap", toolTip=tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            lay.addWidget(b)

    def toggle_max(self):
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        self.toggle_max()


# --------------------------------------------------------- desktop view ---
class Details(QFrame):
    """The selected game: banner, name, where it's from, Play."""
    play = Signal()

    def __init__(self):
        super().__init__(objectName="panel")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 12, 8)
        lay.setSpacing(12)
        self.banner = QLabel(objectName="banner", alignment=Qt.AlignCenter)
        self.banner.setFixedSize(276, 129)
        lay.addWidget(self.banner)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.name = QLabel(objectName="gamename", wordWrap=True)
        self.info = QLabel(objectName="gameinfo", wordWrap=True)
        col.addStretch(1)
        col.addWidget(self.name)
        col.addWidget(self.info)
        col.addStretch(1)
        row = QHBoxLayout()
        self.btn = QPushButton("PLAY", objectName="play")
        self.btn.clicked.connect(self.play)
        row.addWidget(self.btn)
        row.addStretch(1)
        col.addLayout(row)
        col.addStretch(1)
        lay.addLayout(col, 1)
        self.set_game(None)

    def set_game(self, g):
        self.btn.setEnabled(g is not None)
        if g is None:
            self.name.setText("No game selected")
            self.info.setText("")
            self.banner.clear()
            return
        self.name.setText(g.name)
        bits = [g.source, f"Last played: {when(g.last_played)}"]
        if g.favorite:
            bits.insert(0, "★ Favorite")
        self.info.setText("   ·   ".join(bits))
        pm = pixmap(g.header)
        if not pm.isNull():
            self.banner.setPixmap(pm.scaled(self.banner.size(), Qt.KeepAspectRatioByExpanding,
                                            Qt.SmoothTransformation))
        else:
            self.banner.setPixmap(game_icon(g).pixmap(64, 64))


class DesktopView(QWidget):
    def __init__(self, launcher):
        super().__init__()
        self.l = launcher
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        self.details = Details()
        self.details.play.connect(lambda: self.l.launch(self.current()))
        lay.addWidget(self.details)

        self.tabs = QTabBar(drawBase=False, expanding=False)
        self.tabs.setFocusPolicy(Qt.NoFocus)
        for t in TABS:
            self.tabs.addTab(t)
        self.tabs.currentChanged.connect(lambda _i: self.refill())
        lay.addWidget(self.tabs)

        self.tree = QTreeWidget(rootIsDecorated=False, uniformRowHeights=True, alternatingRowColors=True,
                                sortingEnabled=True, iconSize=QSize(20, 20))
        self.tree.setHeaderLabels(["Game", "Source", "Last played"])
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Fixed)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        h.resizeSection(1, 100)
        h.resizeSection(2, 120)
        h.setStretchLastSection(False)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.tree.currentItemChanged.connect(lambda *_: self.details.set_game(self.current()))
        self.tree.itemActivated.connect(lambda *_: self.l.launch(self.current()))
        lay.addWidget(self.tree, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        add = QPushButton("Add a Game…")
        add.clicked.connect(self.l.add_game)
        bar.addWidget(add)
        more = QToolButton(text="View ▾", popupMode=QToolButton.InstantPopup)
        m = QMenu(more)
        self.show_hidden = m.addAction("Show hidden games")
        self.show_hidden.setCheckable(True)
        self.show_hidden.toggled.connect(lambda _c: self.refill())
        m.addAction("Refresh library", lambda: self.l.reload(), QKeySequence("F5"))
        m.addAction("Big Picture mode", lambda: self.l.toggle_big(), QKeySequence("F11"))
        m.addAction("SteamGridDB artwork…", lambda: self.l.set_sgdb_key())
        m.addAction("On-screen keyboard", show_keyboard)
        more.setMenu(m)
        bar.addWidget(more)
        bar.addStretch(1)
        bar.addWidget(QLabel("Search:"))
        self.search = QLineEdit(placeholderText="Find a game", clearButtonEnabled=True)
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(lambda _t: self.refill())
        self.search.returnPressed.connect(self.tree.setFocus)
        bar.addWidget(self.search)
        kb = QToolButton(text="⌨", toolTip="On-screen keyboard")
        kb.setFocusPolicy(Qt.NoFocus)
        kb.clicked.connect(self.keyboard_search)
        bar.addWidget(kb)
        lay.addLayout(bar)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel(objectName="status")
        foot.addWidget(self.status, 1)
        foot.addWidget(QSizeGrip(self), 0, Qt.AlignBottom | Qt.AlignRight)
        lay.addLayout(foot)

        QShortcut(QKeySequence.Find, self, self.search.setFocus)
        QShortcut(QKeySequence("Ctrl+D"), self, lambda: self.l.toggle_favorite(self.current()))

    def keyboard_search(self):
        self.search.setFocus()
        self.search.selectAll()
        show_keyboard()

    def current(self):
        it = self.tree.currentItem()
        return self.l.by_id.get(it.data(0, Qt.UserRole)) if it else None

    def visible_games(self):
        tab = self.tabs.currentIndex()
        q = self.search.text().strip().casefold()
        out = []
        for g in self.l.games:
            if g.hidden and not self.show_hidden.isChecked():
                continue
            if tab == 1 and not g.favorite:
                continue
            if tab == 2 and not g.last_played:
                continue
            if q and q not in g.name.casefold():
                continue
            out.append(g)
        return out

    def refill(self):
        keep = self.current()
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        games = self.visible_games()
        sel = None
        for g in games:
            it = Item([("★ " if g.favorite else "") + g.name + (" (hidden)" if g.hidden else ""),
                       g.source, when(g.last_played)])
            it.setIcon(0, game_icon(g))
            it.setData(0, Qt.UserRole, g.id)
            it.setData(2, Qt.UserRole, g.last_played)
            self.tree.addTopLevelItem(it)
            if keep and g.id == keep.id:
                sel = it
        self.tree.setSortingEnabled(True)
        if self.tabs.currentIndex() == 2:
            self.tree.sortByColumn(2, Qt.DescendingOrder)
        if sel is None and self.tree.topLevelItemCount():
            sel = self.tree.topLevelItem(0)
        self.tree.setCurrentItem(sel)
        self.details.set_game(self.current())
        n = len(games)
        self.status.setText(f"{n} game{'s' if n != 1 else ''}" +
                            ("   ·   Gamepad connected" if self.l.pads and self.l.pads.connected else ""))

    def context_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it:
            self.tree.setCurrentItem(it)
        g = self.current()
        m = QMenu(self)
        if g:
            m.addAction("Play", lambda: self.l.launch(g)).setFont(QFont(m.font().family(), weight=QFont.Bold))
            m.addAction("Remove from Favorites" if g.favorite else "Add to Favorites",
                        lambda: self.l.toggle_favorite(g))
            if g.folder:
                m.addAction("Open install folder",
                            lambda: QProcess.startDetached("xdg-open", [g.folder]))
            if g.source == "Windows":
                m.addAction("Find patches…", lambda: self.l.find_patches(g))
            if g.source != "Steam":
                m.addAction("Controller…", lambda: self.l.controller_dialog(g))
            m.addSeparator()
            if g.id.startswith("custom:"):
                m.addAction("Rename…", lambda: self.l.rename(g))
                m.addAction("Remove from library", lambda: self.l.remove_custom(g))
            else:
                m.addAction("Unhide" if g.hidden else "Hide from library", lambda: self.l.toggle_hidden(g))
        m.addSeparator()
        m.addAction("Add a Game…", self.l.add_game)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def pad(self, name):
        t = self.tree
        if name in ("up", "down"):
            row = t.indexOfTopLevelItem(t.currentItem()) if t.currentItem() else -1
            row = max(0, min(t.topLevelItemCount() - 1, row + (1 if name == "down" else -1)))
            if t.topLevelItemCount():
                t.setCurrentItem(t.topLevelItem(row))
                t.setFocus()
        elif name in ("left", "lb", "right", "rb"):
            d = 1 if name in ("right", "rb") else -1
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % self.tabs.count())
        elif name == "a":
            self.l.launch(self.current())
        elif name == "x":
            self.l.toggle_favorite(self.current())
        elif name == "y":
            self.keyboard_search()
        elif name == "b" and self.search.hasFocus():
            self.search.clear()
            t.setFocus()


# ----------------------------------------------------- big picture view ---
BP_FONT = "Segoe UI, Open Sans, Noto Sans, DejaVu Sans"
BP_TEXT = QColor("#e4edf3")
BP_DIM = QColor("#8ea6b9")
BP_PALE = QColor("#c6d4df")        # selected tab / focused button
BP_INK = QColor("#1a2c3b")         # text on pale
FILTERS = (("All Games", None), ("Favorites", "fav"), ("Recently Played", "recent"),
           ("Steam", "Steam"), ("Windows", "Windows"), ("Linux", "Linux"))
_scaled = {}
_logo = []


def halcyon_logo():
    """The user's Halcyon logo: installed icon, else the repo asset in a dev checkout."""
    if not _logo:
        here = Path(__file__).resolve().parent
        for path in ("/usr/share/pixmaps/halcyon-logo.png", here.parent.parent / "branding/assets/logo.png"):
            if os.path.exists(path):
                _logo.append(QPixmap(str(path)))
                break
        else:
            _logo.append(QIcon.fromTheme("halcyon-logo").pixmap(512, 512))
    return _logo[0]


def bp_font(px, weight=QFont.Normal):
    f = QFont(BP_FONT)
    f.setPixelSize(max(9, int(px)))
    f.setWeight(weight)
    return f


def cover(path, w, h):
    """Art cropped to fill w x h exactly (cached; null if missing)."""
    key = (path, w, h)
    s = _scaled.get(key)
    if s is None:
        src = pixmap(path)
        if src.isNull():
            s = QPixmap()
        else:
            s = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            s = s.copy((s.width() - w) // 2, (s.height() - h) // 2, w, h)
        if len(_scaled) > 300:
            _scaled.clear()
        _scaled[key] = s
    return s


class BigView(QWidget):
    """Full-screen gamepad view in the style of 2012-era console-like game
    clients: slate-blue screens, a tab strip, a grid of wide banner tiles,
    and a game page with a big Play button.

    Screens: home (recently played), library (grid + filter), game (details).
    Focus moves between rows of each screen: tabs / filter / grid, etc."""

    TABS = ("HOME", "LIBRARY", "DESKTOP")
    ROWS = 3

    def __init__(self, launcher):
        super().__init__()
        self.l = launcher
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.games = []          # library list (filtered, sorted)
        self.index = 0           # selection in self.games
        self.recent = []         # home row
        self.rindex = 0
        self.screen = "home"
        self.focus = "row"       # home: tabs|row|all  library: tabs|filter|grid  game: buttons
        self.filter = 0
        self.button = 0          # game page button
        self.game = None         # game shown on the game page
        self.back_to = "library"
        self.hits = []           # (QRect, callable) from the last paint, for the mouse
        self.scroll = 0.0
        self.pop = 1.0
        self.hpos = 0.0          # animated home-row position (follows rindex)
        self._hpos_anim = QVariantAnimation(self, duration=240, easingCurve=QEasingCurve.OutCubic)
        self._hpos_anim.valueChanged.connect(self._set_hpos)
        # Screen transitions: the old frame zooms/slides out while the new one comes in.
        self.trans = 1.0
        self._from = None
        self._tab_from = None
        self._kind = "fade"
        self._trans_anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=380,
                                             easingCurve=QEasingCurve.OutCubic)
        self._trans_anim.valueChanged.connect(self._set_trans)
        self._trans_anim.finished.connect(self._end_trans)
        self.sfx = sfx.Sounds(self)
        self._scroll_anim = QVariantAnimation(self, duration=220, easingCurve=QEasingCurve.OutCubic)
        self._scroll_anim.valueChanged.connect(self._set_scroll)
        self._pop_anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=140,
                                           easingCurve=QEasingCurve.OutCubic)
        self._pop_anim.valueChanged.connect(self._set_pop)

        self.search = QLineEdit(self, objectName="bigsearch", placeholderText="Search games")
        self.search.hide()
        self.search.setStyleSheet(
            "QLineEdit { background: #e4edf3; color: #1a2c3b; border: 1px solid #ffffff;"
            " padding: 3px 10px; font-size: 15pt; }")
        self.search.textChanged.connect(lambda _t: self.refill())
        self.search.returnPressed.connect(self.close_search_keep)
        self.search.installEventFilter(self)
        self.clock = QTimer(self, interval=20000, timeout=self.update)
        self.clock.start()
        # Logo intro each time the view opens; any input skips it.
        self.intro = 1.0
        self._intro_anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=1700)
        self._intro_anim.valueChanged.connect(self._set_intro)

    def play_intro(self):
        if not halcyon_logo().isNull():
            self._intro_anim.stop()
            self._intro_anim.start()
            self.sfx.play("intro")

    def skip_intro(self):
        if self.intro < 1:
            self._intro_anim.stop()
            self.intro = 1.0
            self.update()
            return True
        return False

    def _set_intro(self, v):
        self.intro = v
        self.update()

    def _set_scroll(self, v):
        self.scroll = v
        self.update()

    def _set_hpos(self, v):
        self.hpos = v
        self.update()

    def _set_trans(self, v):
        self.trans = v
        self.update()

    def _end_trans(self):
        self._from = None
        self.trans = 1.0
        self.update()

    def transition(self, kind):
        """Snapshot the current frame; the next screen animates in over it."""
        if not self.isVisible() or self.intro < 1:
            return
        self._trans_anim.stop()
        self.trans = 1.0
        self._from = None
        W, H = self.width(), self.height()
        if kind in ("left", "right"):
            self._tab_from = 0 if self.screen == "home" else 1
            self._from = self._layer(self._paint_content, W, H)
        else:
            self._from = self.grab()
        self._kind = kind
        self.trans = 0.0
        self._trans_anim.start()

    def _home_follow(self):
        self._hpos_anim.stop()
        self._hpos_anim.setStartValue(float(self.hpos))
        self._hpos_anim.setEndValue(float(self.rindex))
        self._hpos_anim.start()

    def _set_pop(self, v):
        self.pop = v
        self.update()

    # --- data
    def refill(self):
        keep = self.current() if self.screen != "game" else None
        q = self.search.text().strip().casefold()
        key = FILTERS[self.filter][1]
        games = [g for g in self.l.games if not g.hidden and (not q or q in g.name.casefold())]
        if key == "fav":
            games = [g for g in games if g.favorite]
        elif key == "recent":
            games = sorted((g for g in games if g.last_played), key=lambda g: -g.last_played)
        elif key:
            games = [g for g in games if g.source == key or (key == "Linux" and g.source not in
                                                             ("Steam", "Windows"))]
        if key != "recent":
            games.sort(key=lambda g: g.name.casefold())
        self.games = games
        self.index = next((i for i, g in enumerate(games) if keep and g.id == keep.id), 0)
        visible = [g for g in self.l.games if not g.hidden]
        played = sorted((g for g in visible if g.last_played), key=lambda g: -g.last_played)
        self.recent = (played or sorted(visible, key=lambda g: (not g.favorite, g.name.casefold())))[:8]
        self.rindex = min(self.rindex, max(0, len(self.recent) - 1))
        self._hpos_anim.stop()
        self.hpos = float(self.rindex)
        self._follow(animate=False)
        self.update()

    def current(self):
        if self.screen == "game":
            return self.game
        if self.screen == "home":
            return self.recent[self.rindex] if 0 <= self.rindex < len(self.recent) else None
        return self.games[self.index] if 0 <= self.index < len(self.games) else None

    # --- navigation
    def go(self, screen, kind=None):
        if screen == "desktop":
            self.l.toggle_big()
            return
        if screen != self.screen:
            order = ("home", "library")
            if kind is None and screen in order and self.screen in order:
                kind = "left" if order.index(screen) > order.index(self.screen) else "right"
            self.transition(kind or "fade")
        self.screen = screen
        self.focus = {"home": "row", "library": "grid", "game": "buttons"}[screen]
        if screen == "library":
            self._follow(animate=False)
        self._bump()

    def open_game(self, g):
        if g:
            self.back_to = self.screen
            self.game = g
            self.button = 0
            self.go("game", "in")

    def back(self):
        self.sfx.play("back")
        if self.search.text():
            self.close_search()
        elif self.screen == "game":
            self.go(self.back_to, "out")
        elif self.screen == "library":
            self.go("home")
        else:
            self.l.toggle_big()

    def tab_step(self, d):
        if self.screen == "game":
            return
        cur = 0 if self.screen == "home" else 1
        t = max(0, min(1, cur + d))
        if t == cur:
            self.sfx.play("bump")
            return
        self.sfx.play("tab_right" if t > cur else "tab_left")
        self.go(("home", "library")[t])

    def _bump(self):
        self._pop_anim.stop()
        self._pop_anim.start()

    def select(self, i):
        if self.games:
            i = max(0, min(len(self.games) - 1, i))
            if i != self.index:
                self.index = i
                self._follow()
                self._bump()

    def _grid(self):
        W, H = self.width(), self.height()
        th = int(H * 0.165)
        tw = int(th * 460 / 215)
        gap = max(6, int(H * 0.014))
        return int(W * 0.03), int(H * 0.235), tw, th, gap

    def _follow(self, animate=True):
        """Scroll the grid so the selected column stays comfortably on screen."""
        x0, _y0, tw, _th, gap = self._grid()
        colw = tw + gap
        col = self.index // self.ROWS
        cols = (len(self.games) + self.ROWS - 1) // self.ROWS
        left = col * colw - self.scroll
        lo, hi = colw * 0.6, self.width() - 2 * x0 - colw * 1.6
        target = self.scroll
        if left < lo:
            target = col * colw - lo
        elif left > hi:
            target = col * colw - hi
        target = max(0.0, min(target, max(0.0, cols * colw - (self.width() - 2 * x0) + gap)))
        if not animate:
            self._scroll_anim.stop()
            self.scroll = target
        elif target != self.scroll:
            self._scroll_anim.stop()
            self._scroll_anim.setStartValue(float(self.scroll))
            self._scroll_anim.setEndValue(float(target))
            self._scroll_anim.start()

    def _state(self):
        return (self.screen, self.focus, self.index, self.rindex, self.filter, self.button)

    def nav(self, d):
        before = self._state()
        self._nav(d)
        after = self._state()
        if after[0] != before[0]:
            pass                                  # tab_step played its own sound
        elif after != before:
            self.sfx.play("move")
        else:
            self.sfx.play("bump")

    def _nav(self, d):
        """d is 'up', 'down', 'left' or 'right'."""
        s, f = self.screen, self.focus
        if s == "game":
            n = 2
            if d in ("up", "left"):
                self.button = max(0, self.button - 1)
            elif d in ("down", "right"):
                self.button = min(n - 1, self.button + 1)
        elif s == "home":
            if f == "tabs":
                if d in ("left", "right"):
                    self.tab_step(-1 if d == "left" else 1)
                elif d == "down":
                    self.focus = "row"
            elif f == "row":
                if d in ("left", "right") and self.recent:
                    self.rindex = max(0, min(len(self.recent) - 1, self.rindex + (-1 if d == "left" else 1)))
                    self._home_follow()
                    self._bump()
                elif d == "up":
                    self.focus = "tabs"
                elif d == "down":
                    self.focus = "all"
            elif f == "all" and d == "up":
                self.focus = "row"
        else:
            if f == "tabs":
                if d in ("left", "right"):
                    self.tab_step(-1 if d == "left" else 1)
                elif d == "down":
                    self.focus = "filter"
            elif f == "filter":
                if d in ("left", "right"):
                    self.set_filter(self.filter + (-1 if d == "left" else 1))
                elif d == "up":
                    self.focus = "tabs"
                elif d == "down":
                    self.focus = "grid"
            elif f == "grid":
                row = self.index % self.ROWS
                if d == "up":
                    if row == 0:
                        self.focus = "filter"
                    else:
                        self.select(self.index - 1)
                elif d == "down":
                    if row < self.ROWS - 1:
                        self.select(self.index + 1)
                elif d == "left":
                    self.select(self.index - self.ROWS)
                elif d == "right":
                    nxt = self.index + self.ROWS
                    if nxt >= len(self.games):      # last column may be short
                        nxt = len(self.games) - 1 if nxt // self.ROWS > self.index // self.ROWS else self.index
                    self.select(nxt)
        self.update()

    def set_filter(self, i):
        self.filter = i % len(FILTERS)
        self.index = 0
        self.refill()

    def activate(self):
        s, f = self.screen, self.focus
        if s == "game":
            if self.button == 0:
                self.sfx.play("launch")
                self.l.launch(self.game)
            else:
                self.sfx.play("favorite")
                self.l.toggle_favorite(self.game)
        elif f == "tabs":
            return
        elif f == "filter":
            self.sfx.play("move")
            self.set_filter(self.filter + 1)
        elif s == "home" and f == "all":
            self.sfx.play("select")
            self.go("library")
        elif self.current():
            self.sfx.play("select")
            self.open_game(self.current())
        self.update()

    # --- search
    def open_search(self, text=""):
        if not self.search.isVisible():
            self.sfx.play("select")
        if self.screen != "library":
            self.go("library")
        self.search.show()
        self.search.setFocus()
        if text:
            self.search.setText(self.search.text() + text)
        self.layout_search()
        show_keyboard()

    def close_search_keep(self):
        self.search.hide()
        self.focus = "grid"
        self.setFocus()
        self.update()

    def close_search(self):
        self.search.clear()
        self.close_search_keep()
        self.refill()

    def layout_search(self):
        r = self._filter_rect()
        self.search.setGeometry(r.x(), r.y() - 2, int(self.width() * 0.26), r.height() + 4)

    def _filter_rect(self):
        W, H = self.width(), self.height()
        return QRect(int(W * 0.3), int(H * 0.15), int(W * 0.2), max(22, int(H * 0.036)))

    def resizeEvent(self, e):
        _scaled.clear()
        self.layout_search()
        self._follow(animate=False)

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == QEvent.KeyPress:
            if e.key() == Qt.Key_Escape:
                self.close_search()
                return True
            if e.key() == Qt.Key_Down:
                self.close_search_keep()
                return True
        return False

    # --- input
    def keyPressEvent(self, e):
        if self.skip_intro():
            return
        k = e.key()
        arrows = {Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right"}
        if k in arrows:
            self.nav(arrows[k])
        elif k in (Qt.Key_PageUp, Qt.Key_PageDown):
            self.tab_step(-1 if k == Qt.Key_PageUp else 1)
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activate()
        elif k in (Qt.Key_Escape, Qt.Key_Backspace):
            self.back()
        elif e.text() and e.text().isprintable() and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            self.open_search(e.text())
        else:
            super().keyPressEvent(e)

    def pad(self, name):
        if self.skip_intro():
            return
        if name in ("up", "down", "left", "right"):
            self.nav(name)
        elif name == "a":
            self.activate()
        elif name == "b":
            self.back()
        elif name in ("lb", "rb"):
            self.tab_step(-1 if name == "lb" else 1)
        elif name == "x":
            g = self.current()
            if g and self.focus not in ("tabs", "filter", "all"):
                self.sfx.play("favorite")
                self.l.toggle_favorite(g)
                if FILTERS[self.filter][1] == "fav":
                    self.refill()
        elif name in ("y", "rt"):
            self.open_search()
        elif name in ("start", "select", "lt"):
            self.l.toggle_big()

    def wheelEvent(self, e):
        d = e.angleDelta().y() or -e.angleDelta().x()
        if self.screen == "library":
            self.focus = "grid"
            self.select(self.index + (-self.ROWS if d > 0 else self.ROWS))
        elif self.screen == "home":
            self.nav("left" if d > 0 else "right")
        self.update()

    def mousePressEvent(self, e):
        if self.skip_intro():
            return
        pos = e.position().toPoint()
        for rect, act in reversed(self.hits):
            if rect.contains(pos):
                act(e.button())
                self.update()
                return
        if e.button() == Qt.RightButton:
            self.back()

    # --- drawing helpers
    def hit(self, rect, act):
        self.hits.append((QRect(rect), act))

    def _text(self, p, rect, text, font, color, align=Qt.AlignLeft | Qt.AlignVCenter, shadow=True):
        p.setFont(font)
        if shadow:
            p.setPen(QColor(0, 0, 0, 120))
            p.drawText(QRect(rect).translated(1, 1), align, text)
        p.setPen(color)
        p.drawText(rect, align, text)

    def _button(self, p, rect, text, focused, font, pale_idle=False):
        """Flat console-style button: pale when focused, dark glass otherwise."""
        p.setPen(Qt.NoPen)
        if focused:
            p.setBrush(BP_PALE)
        else:
            p.setBrush(QColor(255, 255, 255, 40) if pale_idle else QColor(12, 24, 34, 150))
        p.drawRect(rect)
        self._text(p, rect, text, font, BP_INK if focused else BP_TEXT, Qt.AlignCenter, shadow=not focused)

    def _background(self, p, W, H):
        bg = QLinearGradient(0, 0, 0, H)
        bg.setColorAt(0, QColor("#355d7a"))
        bg.setColorAt(0.35, QColor("#21405a"))
        bg.setColorAt(1, QColor("#10202e"))
        p.fillRect(self.rect(), bg)
        # Faded strip of recently played art behind the tabs.
        band = int(H * 0.11)
        th = band
        tw = int(th * 460 / 215)
        p.setOpacity(0.16)
        x = int(W * 0.18)
        for g in self.recent[:6]:
            art = cover(g.header, tw, th)
            if not art.isNull():
                p.drawPixmap(x, 0, art)
            x += tw + 4
        p.setOpacity(1)
        fade = QLinearGradient(0, 0, 0, band)
        fade.setColorAt(0, QColor(16, 32, 46, 120))
        fade.setColorAt(1, QColor(33, 64, 90, 255))
        p.fillRect(0, 0, W, band, fade)
        p.fillRect(0, band, W, 1, QColor(255, 255, 255, 25))

    def _tabs(self, p, W, H):
        th = max(22, int(H * 0.036))
        tw = int(W * 0.085)
        x = (W - tw * len(self.TABS)) // 2
        y = int(H * 0.035)
        f = bp_font(th * 0.5)
        active = 0 if self.screen == "home" else 1 if self.screen == "library" else -1
        pos = float(active)
        if self.trans < 1 and self._kind in ("left", "right") and self._tab_from is not None:
            pos = self._tab_from + (active - self._tab_from) * self.trans
        p.fillRect(QRect(x, y, tw * len(self.TABS), th), QColor(10, 20, 30, 130))
        if active >= 0:
            p.fillRect(QRectF(x + pos * tw, y, tw, th), BP_PALE if self.focus == "tabs" else QColor(198, 212, 223, 200))
        for i, name in enumerate(self.TABS):
            r = QRect(x + i * tw, y, tw, th)
            on = active >= 0 and abs(pos - i) < 0.5
            self._text(p, r, name, f, BP_INK if on else BP_DIM, Qt.AlignCenter, shadow=not on)
            self.hit(r, lambda _b, i=i: self._tab_click(i))
        # Halcyon logo, top left
        logo = halcyon_logo()
        if not logo.isNull():
            s = logo.scaled(int(th * 1.5), int(th * 1.5), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(int(W * 0.03), y + (th - s.height()) // 2, s)
            self._text(p, QRect(int(W * 0.03) + s.width() + 10, y, int(W * 0.2), th), "Halcyon",
                       bp_font(th * 0.55, QFont.Light), BP_TEXT)
        # Clock, top right
        f2 = bp_font(H * 0.024)
        self._text(p, QRect(W - int(W * 0.2) - int(W * 0.03), y, int(W * 0.2), th),
                   time.strftime("%-I:%M %p"), f2, BP_TEXT, Qt.AlignRight | Qt.AlignVCenter)

    def _hints(self, p, W, H, center, right=None, left=("LT", "DESKTOP")):
        f = bp_font(H * 0.019)
        fm = QFontMetrics(f)
        y = int(H * 0.925)
        h = int(fm.height() * 1.25)
        p.fillRect(0, y - int(H * 0.025), W, 1, QColor(255, 255, 255, 18))

        def glyph(x, label, round_):
            gw = h if round_ else int(h * 1.5)
            r = QRect(x, y, gw, h)
            p.setPen(QPen(QColor(220, 232, 240, 170), 1.2))
            p.setBrush(Qt.NoBrush)
            if round_:
                p.drawEllipse(r.adjusted(1, 1, -1, -1))
            else:
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), h * 0.3, h * 0.3)
            gf = bp_font(h * 0.5, QFont.DemiBold)
            p.setFont(gf)
            p.setPen(QColor(220, 232, 240, 200))
            p.drawText(r, Qt.AlignCenter, label)
            return gw

        def item(x, key, text, key_first=True):
            round_ = key in ("A", "B", "X", "Y")
            if key_first:
                x += glyph(x, key, round_) + 8
                self._text(p, QRect(x, y, fm.horizontalAdvance(text) + 4, h), text, f, BP_DIM)
            else:
                tw = fm.horizontalAdvance(text) + 4
                self._text(p, QRect(x, y, tw, h), text, f, BP_DIM)
                glyph(x + tw + 6, key, round_)

        def width(key, text):
            return (h if key in ("A", "B", "X", "Y") else int(h * 1.5)) + 8 + fm.horizontalAdvance(text) + 4

        x0 = int(W * 0.03)
        if left:
            item(x0, *left)
        total = sum(width(k, t) for k, t in center) + 28 * (len(center) - 1)
        x = (W - total) // 2
        for k, t in center:
            item(x, k, t)
            x += width(k, t) + 28
        if right:
            item(W - x0 - width(*right), right[0], right[1], key_first=False)

    def _tile(self, p, g, r, selected, label_h):
        art = cover(g.header, r.width(), r.height())
        if not art.isNull():
            p.drawPixmap(r.topLeft(), art)
        else:
            # No banner: dark tile with the game's icon and name.
            grad = QLinearGradient(0, r.top(), 0, r.bottom())
            grad.setColorAt(0, QColor("#2b3f50"))
            grad.setColorAt(1, QColor("#16232e"))
            p.fillRect(r, grad)
            isz = int(r.height() * 0.42)
            icon = game_icon(g).pixmap(isz, isz)
            f = bp_font(r.height() * 0.13)
            if icon.isNull():
                tr = r.adjusted(10, 0, -10, 0)
                align = Qt.AlignCenter
            else:
                p.drawPixmap(r.x() + int(r.height() * 0.18), r.y() + (r.height() - isz) // 2, icon)
                tr = QRect(r.x() + int(r.height() * 0.3) + isz, r.y(), 0, r.height())
                tr.setRight(r.right() - 8)
                align = Qt.AlignLeft | Qt.AlignVCenter
            self._text(p, tr, QFontMetrics(f).elidedText(g.name, Qt.ElideRight, tr.width()), f, BP_TEXT, align)
        if g.favorite:
            f = bp_font(r.height() * 0.13)
            self._text(p, QRect(r.right() - int(r.height() * 0.22), r.y() + 2, int(r.height() * 0.2),
                                int(r.height() * 0.2)), "★", f, QColor("#ffd75e"), Qt.AlignCenter)
        if selected:
            lr = QRect(r.x(), r.bottom() + 1, r.width(), label_h)
            p.fillRect(lr, QColor("#f2f6f9"))
            f = bp_font(label_h * 0.6)
            name = QFontMetrics(f).elidedText(g.name, Qt.ElideRight, lr.width() - 12)
            self._text(p, lr.adjusted(6, 0, -6, 0), name, f, BP_INK, shadow=False)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRect(r.topLeft(), lr.bottomRight()).adjusted(-1, -1, 1, 1))
        else:
            p.setPen(QPen(QColor(0, 0, 0, 90), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)

    def _grow(self, r, k, label_h=0):
        """r scaled by k around its center (room for a label below is taken from the growth)."""
        w, h = int(r.width() * k), int(r.height() * k)
        c = r.center()
        return QRect(c.x() - w // 2, c.y() - h // 2 - label_h // 2, w, h)

    # --- screens
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        if self.trans < 1 and self._from is not None and self._kind in ("left", "right"):
            # Tab switch: chrome stays, the content slides under it.
            self.hits = []
            self._chrome(p, W, H)
            new = self._layer(self._paint_content, W, H)
            t = self.trans
            d = -1 if self._kind == "left" else 1
            dist = W * 0.35
            p.save()
            p.setClipRect(self._content_clip(W, H))
            # Old content leaves first; the new one follows it in, barely overlapping.
            out_t = min(1.0, t / 0.45)
            in_t = max(0.0, (t - 0.3) / 0.7)
            p.setOpacity(1 - out_t)
            p.drawPixmap(QPointF(d * dist * 0.6 * out_t, 0), self._from)
            p.setOpacity(in_t)
            p.drawPixmap(QPointF(-d * dist * (1 - in_t), 0), new)
            p.restore()
        elif self.trans < 1 and self._from is not None:
            frame = QPixmap(self.size() * self.devicePixelRatioF())
            frame.setDevicePixelRatio(self.devicePixelRatioF())
            q = QPainter(frame)
            q.setRenderHint(QPainter.Antialiasing)
            q.setRenderHint(QPainter.SmoothPixmapTransform)
            self._paint_screen(q, W, H)
            q.end()
            self._composite(p, frame, W, H)
        else:
            self._paint_screen(p, W, H)
        if self.intro < 1:
            self._paint_intro(p, W, H)

    def _composite(self, p, new, W, H):
        t = self.trans
        old = self._from

        def draw(pm, scale, dx, alpha):
            w, h = W * scale, H * scale
            p.setOpacity(max(0.0, min(1.0, alpha)))
            p.drawPixmap(QRectF((W - w) / 2 + dx, (H - h) / 2, w, h), pm, QRectF(pm.rect()))

        p.fillRect(self.rect(), QColor("#10202e"))
        if self._kind == "in":            # zoom into the selection
            draw(old, 1 + 0.08 * t, 0, 1 - t)
            draw(new, 0.94 + 0.06 * t, 0, t)
        elif self._kind == "out":         # pull back out
            draw(new, 1.08 - 0.08 * t, 0, t)
            draw(old, 1 - 0.06 * t, 0, 1 - t)
        else:
            draw(old, 1, 0, 1 - t)
            draw(new, 1, 0, t)
        p.setOpacity(1)

    def _paint_screen(self, p, W, H):
        self.hits = []
        if self.screen == "game" and self.game:
            self._paint_game(p, W, H)
        else:
            self._chrome(p, W, H)
            self._paint_content(p, W, H)

    def _chrome(self, p, W, H):
        """What stays put when switching tabs: background, tab strip, button hints."""
        self._background(p, W, H)
        self._tabs(p, W, H)
        if self.screen == "library":
            center = [("A", "SELECT"), ("X", "FAVORITE")] if self.focus == "grid" else [("A", "SELECT")]
            if self.search.text():
                center.append(("B", "CLEAR SEARCH"))
        else:
            center = [("A", "SELECT"), ("X", "FAVORITE")]
        self._hints(p, W, H, center, ("RT", "SEARCH"))

    def _paint_content(self, p, W, H):
        if self.screen == "library":
            self._paint_library(p, W, H)
        else:
            self._paint_home(p, W, H)

    def _content_clip(self, W, H):
        top = int(H * 0.11) + 1
        return QRect(0, top, W, int(H * 0.9) - top)

    def _layer(self, fn, W, H):
        """Render fn onto a transparent pixmap the size of the view."""
        pm = QPixmap(self.size() * self.devicePixelRatioF())
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        q.setRenderHint(QPainter.SmoothPixmapTransform)
        fn(q, W, H)
        q.end()
        return pm

    def _paint_intro(self, p, W, H):
        """Logo glows in on black, then the black fades away to reveal the view."""
        t = self.intro
        cover_a = 1.0 if t < 0.7 else max(0.0, 1 - (t - 0.7) / 0.3)
        p.setOpacity(cover_a)
        rg = QRadialGradient(W / 2, H / 2, max(W, H) * 0.6)
        rg.setColorAt(0, QColor("#16283a"))
        rg.setColorAt(1, QColor("#03070b"))
        p.fillRect(self.rect(), rg)
        logo_a = min(1.0, t / 0.35) * cover_a
        size = int(min(W, H) * (0.2 + 0.03 * t))
        s = halcyon_logo().scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        glow = QRadialGradient(W / 2, H / 2, size * 0.9)
        glow.setColorAt(0, QColor(120, 190, 255, int(110 * logo_a)))
        glow.setColorAt(1, QColor(120, 190, 255, 0))
        p.setOpacity(1)
        p.fillRect(self.rect(), glow)
        p.setOpacity(logo_a)
        p.drawPixmap((W - s.width()) // 2, (H - s.height()) // 2, s)
        p.setOpacity(1)

    def _paint_home(self, p, W, H):
        th = int(min(H * 0.25, W * 0.13))
        tw = int(th * 460 / 215)
        y = int(H * 0.36)
        title = "Recently Played" if any(g.last_played for g in self.recent) else "Your Games"
        tf = bp_font(min(H * 0.055, W * 0.032), QFont.Light)
        x = max(int(W * 0.3), int(W * 0.05) + QFontMetrics(tf).horizontalAdvance(title))
        if not self.recent:
            self._text(p, QRect(x, y, W - x, th), "No games installed yet", bp_font(H * 0.03), BP_DIM)
        label_h = max(16, int(H * 0.03))
        small_h = int(th * 0.55)
        small_w = int(small_h * 460 / 215)
        gap = 10
        # The row is one strip (title, then tiles) sliding left as the selection
        # moves right; tiles grow smoothly as they reach the selected slot.
        hp = self.hpos
        widths = [small_w + (tw - small_w) * max(0.0, 1 - abs(i - hp)) for i in range(len(self.recent))]
        lefts, acc = [], 0.0
        for w in widths:
            lefts.append(acc)
            acc += w + gap
        fl = max(0, min(len(widths) - 1, int(hp))) if widths else 0
        anchor = (lefts[fl] + (hp - fl) * (widths[fl] + gap)) if widths else 0.0
        shift = x - anchor
        self._text(p, QRect(int(W * 0.03 + shift - x), y, x - int(W * 0.05), th), title, tf,
                   QColor(214, 226, 236, 170), Qt.AlignRight | Qt.AlignVCenter)
        sel = None
        for i, g in enumerate(self.recent):
            w = widths[i]
            h = w * 215 / 460
            r = QRect(int(shift + lefts[i]), int(y + th - h), int(w), int(h))
            if r.right() < 0 or r.left() > W:
                continue
            if i == self.rindex:
                sel = (g, r)
                continue
            self._tile(p, g, r, False, 0)
            self.hit(r, lambda _b, i=i: self._home_click(i))
        if sel:
            g, r = sel
            k = 0.94 + 0.06 * self.pop
            rr = self._grow(r, k)
            if self.focus != "row":
                p.setOpacity(0.85)
            self._tile(p, g, rr, self.focus == "row", label_h)
            p.setOpacity(1)
            self.hit(rr, lambda _b: self._home_click(self.rindex))
        # VIEW ALL GAMES
        bw, bh = int(W * 0.09), int(H * 0.075)
        br = QRect(x + (tw - bw) // 2, y + th + label_h + int(H * 0.05), bw, bh)
        f = bp_font(bh * 0.2)
        p.setPen(Qt.NoPen)
        p.setBrush(BP_PALE if self.focus == "all" else QColor(12, 24, 34, 150))
        p.drawRect(br)
        col = BP_INK if self.focus == "all" else BP_TEXT
        self._text(p, br.adjusted(0, 0, 0, -bh // 2 + 2), "VIEW ALL", f, col, Qt.AlignHCenter | Qt.AlignBottom,
                   shadow=False)
        self._text(p, br.adjusted(0, bh // 2 - 2, 0, 0), "GAMES", bp_font(bh * 0.3), col,
                   Qt.AlignHCenter | Qt.AlignTop, shadow=False)
        self.hit(br, lambda _b: self.go("library"))

    def _tab_click(self, i):
        cur = 0 if self.screen == "home" else 1
        if i == 2:
            self.l.toggle_big()
        elif i != cur:
            self.tab_step(i - cur)

    def _home_click(self, i):
        if i == self.rindex and self.focus == "row":
            self.sfx.play("select")
            self.open_game(self.current())
        else:
            self.sfx.play("move")
            self.rindex = i
            self.focus = "row"
            self._home_follow()
            self._bump()

    def _paint_library(self, p, W, H):
        x0, y0, tw, th, gap = self._grid()
        self._text(p, QRect(x0, int(H * 0.135), int(W * 0.25), int(H * 0.06)), "Games",
                   bp_font(H * 0.045, QFont.Light), BP_TEXT)
        # Filter dropdown
        fr = self._filter_rect()
        if not self.search.isVisible():
            focused = self.focus == "filter"
            p.setPen(QPen(QColor(255, 255, 255, 200 if focused else 70), 1))
            p.setBrush(BP_PALE if focused else QColor(10, 22, 32, 120))
            p.drawRect(fr)
            name = FILTERS[self.filter][0]
            q = self.search.text()
            text = f'"{q}" ({len(self.games)})' if q else f"{name} ({len(self.games)})"
            f = bp_font(fr.height() * 0.5)
            self._text(p, fr.adjusted(10, 0, -24, 0), text, f, BP_INK if focused else BP_TEXT, shadow=not focused)
            self._text(p, fr.adjusted(0, 0, -8, 0), "‹ ›" if focused else "▾", f, BP_INK if focused else BP_DIM,
                       Qt.AlignRight | Qt.AlignVCenter, shadow=False)
            self.hit(fr, lambda b: (self.set_filter(self.filter + (-1 if b == Qt.RightButton else 1)),
                                    setattr(self, "focus", "filter")))
        if not self.games:
            self._text(p, QRect(x0, y0, W - 2 * x0, th), "No games found" if self.search.text() else
                       "No games here yet", bp_font(H * 0.03), BP_DIM)
        label_h = max(16, int(H * 0.028))
        p.save()
        p.setClipRect(0, y0 - int(H * 0.04), W, self.ROWS * (th + gap) + int(H * 0.08))
        colw = tw + gap
        first = max(0, int(self.scroll // colw) - 1)
        last = int((self.scroll + W) // colw) + 1
        sel = None
        for col in range(first, last + 1):
            for row in range(self.ROWS):
                i = col * self.ROWS + row
                if i >= len(self.games):
                    break
                r = QRect(int(x0 + col * colw - self.scroll), y0 + row * (th + gap), tw, th)
                if i == self.index and self.focus == "grid":
                    sel = (i, r)
                    continue
                if i == self.index:
                    p.setOpacity(0.95)
                self._tile(p, self.games[i], r, False, 0)
                p.setOpacity(1)
                self.hit(r, lambda _b, i=i: self._grid_click(i))
        if sel:
            i, r = sel
            k = 1.0 + 0.14 * self.pop
            rr = self._grow(r, k, label_h)
            # Keep the enlarged tile inside the screen.
            rr.moveLeft(max(4, min(rr.left(), W - rr.width() - 4)))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRect(rr.adjusted(-6, -6, 6, label_h + 6))
            self._tile(p, self.games[i], rr, True, label_h)
            self.hit(rr.adjusted(0, 0, 0, label_h), lambda _b, i=i: self._grid_click(i))
        p.restore()
        cols = (len(self.games) + self.ROWS - 1) // self.ROWS
        if cols * colw > W - 2 * x0:
            # Thin scroll indicator under the grid
            y = y0 + self.ROWS * (th + gap) + int(H * 0.03)
            full = W - 2 * x0
            frac = (W - 2 * x0) / (cols * colw)
            pos = self.scroll / max(1, cols * colw - (W - 2 * x0) + gap)
            p.fillRect(x0, y, full, 3, QColor(255, 255, 255, 25))
            p.fillRect(int(x0 + (full - full * frac) * min(1, pos)), y, int(full * frac), 3, QColor(198, 212, 223, 150))

    def _grid_click(self, i):
        if i == self.index and self.focus == "grid":
            self.sfx.play("select")
            self.open_game(self.current())
        else:
            self.sfx.play("move")
            self.focus = "grid"
            self.select(i)

    def _paint_game(self, p, W, H):
        g = self.game
        hero = cover(g.hero or g.header, W, H)
        if not hero.isNull():
            p.drawPixmap(0, 0, hero)
            tint = QLinearGradient(0, 0, W, 0)
            tint.setColorAt(0, QColor(16, 32, 46, 245))
            tint.setColorAt(0.5, QColor(20, 40, 58, 200))
            tint.setColorAt(1, QColor(20, 40, 58, 110))
            p.fillRect(self.rect(), tint)
        else:
            self._background(p, W, H)
        bottom = QLinearGradient(0, H * 0.6, 0, H)
        bottom.setColorAt(0, QColor(10, 20, 30, 0))
        bottom.setColorAt(1, QColor(10, 20, 30, 230))
        p.fillRect(self.rect(), bottom)

        x0 = int(W * 0.06)
        box = QRect(x0, int(H * 0.1), int(W * 0.4), int(H * 0.2))
        logo = pixmap(g.logo)
        if not logo.isNull():
            s = logo.scaled(box.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(box.x(), box.bottom() - s.height(), s)
        else:
            f = bp_font(H * 0.065, QFont.Light)
            self._text(p, box, QFontMetrics(f).elidedText(g.name, Qt.ElideRight, int(W * 0.85)), f, BP_TEXT,
                       Qt.AlignLeft | Qt.AlignBottom)

        bw, bh = int(W * 0.24), int(H * 0.08)
        y = int(H * 0.4)
        play = QRect(x0, y, bw, bh)
        fav = QRect(x0, y + bh + int(H * 0.018), bw, int(bh * 0.7))
        self._button(p, play, "PLAY", self.button == 0, bp_font(bh * 0.4, QFont.DemiBold))
        self._button(p, fav, "REMOVE FAVORITE" if g.favorite else "ADD TO FAVORITES", self.button == 1,
                     bp_font(bh * 0.24))
        self.hit(play, lambda _b: (setattr(self, "button", 0), self.l.launch(g)))
        self.hit(fav, lambda _b: (setattr(self, "button", 1), self.l.toggle_favorite(g)))

        f = bp_font(H * 0.022)
        fb = bp_font(H * 0.022, QFont.DemiBold)
        iy = fav.bottom() + int(H * 0.06)
        for label, value in (("LAST PLAYED", when(g.last_played)), ("SOURCE", g.source)):
            self._text(p, QRect(x0, iy, int(W * 0.12), int(H * 0.035)), label, f, BP_DIM)
            self._text(p, QRect(x0 + int(W * 0.12), iy, int(W * 0.3), int(H * 0.035)), value, fb, BP_TEXT)
            iy += int(H * 0.04)
        self._hints(p, W, H, [("A", "SELECT"), ("B", "BACK")])


class ArtFetcher(QThread):
    got = Signal(str, dict)
    failed = Signal(str)

    def __init__(self, key, games, parent):
        super().__init__(parent)
        self.key, self.games = key, list(games)

    def run(self):
        for g in self.games:
            try:
                art = sgdb.fetch(self.key, g)
            except sgdb.SGDBError as e:
                self.failed.emit(str(e))
                return
            if art:
                self.got.emit(g.id, art)


# ---------------------------------------------------------------- window ---
class Launcher(QWidget):
    EDGE = 5

    def __init__(self, big=False):
        super().__init__(objectName="root")
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon.fromTheme("applications-games"))
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setMouseTracking(True)
        self.resize(920, 640)
        self.setMinimumSize(560, 420)
        self.cfg = library.load_config()
        self.games, self.by_id = [], {}
        self.pads = Gamepads(self) if Gamepads else None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(self.EDGE - 3, 2, self.EDGE - 3, self.EDGE - 3)
        lay.setSpacing(0)
        self.titlebar = TitleBar(self)
        lay.addWidget(self.titlebar)
        self.stack = QStackedWidget()
        self.desktop = DesktopView(self)
        self.big = BigView(self)
        self.stack.addWidget(self.desktop)
        self.stack.addWidget(self.big)
        lay.addWidget(self.stack, 1)

        if self.pads:
            self.pads.pressed.connect(self.on_pad)
        QShortcut(QKeySequence("F11"), self, lambda: self.toggle_big())
        QShortcut(QKeySequence("F5"), self, lambda: self.reload())
        # Steam rewrites its manifests as games install; pick that up quietly.
        self._reload_timer = QTimer(self, interval=60000, timeout=self.reload)
        self._reload_timer.start()
        self.reload()
        if big:
            self.toggle_big(keep_selection=False)

    # --- library
    def reload(self):
        self.games = library.load(self.cfg)
        self.by_id = {g.id: g for g in self.games}
        todo = sgdb.apply_cached(self.games)
        self.desktop.refill()
        self.big.refill()
        self.fetch_art(todo)

    def fetch_art(self, games):
        """Missing banners/backgrounds/logos from SteamGridDB, in the background."""
        key = self.cfg.get("sgdb_key") or os.environ.get("STEAMGRIDDB_KEY")
        if not key or not games or getattr(self, "_art", None) and self._art.isRunning():
            return
        self._art = ArtFetcher(key, games, self)
        self._art.got.connect(self._art_ready)
        self._art.failed.connect(lambda msg: self.desktop.status.setText(msg))
        self._art.start()

    def _art_ready(self, gid, art):
        g = self.by_id.get(gid)
        if g is None:
            return
        for field, path in art.items():
            setattr(g, field, path)
        _scaled.clear()
        self.desktop.details.set_game(self.desktop.current())
        self.big.update()

    def set_sgdb_key(self):
        key, ok = QInputDialog.getText(
            self, "SteamGridDB artwork",
            "Halcyon Games can download banners, backgrounds and logos for games\n"
            "that don't have their own (Big Picture uses them).\n\n"
            "Make a free API key at steamgriddb.com → Preferences → API, and paste it here:",
            QLineEdit.Normal, self.cfg.get("sgdb_key", ""))
        if not ok:
            return
        self.cfg["sgdb_key"] = key.strip()
        self.save()
        if key.strip():
            self.desktop.status.setText("Fetching artwork from SteamGridDB…")
            self.fetch_art(sgdb.apply_cached(self.games))

    def save(self):
        library.save_config(self.cfg)

    def launch(self, g):
        if not g:
            return
        env = QProcessEnvironment.systemEnvironment()
        for k in ("PYTHONPATH", "QT_QPA_PLATFORM"):
            env.remove(k)
        for k, v in g.env.items():
            env.insert(k, v)
        proc = QProcess()
        proc.setProgram(g.cmd[0])
        proc.setArguments(g.cmd[1:])
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(g.cwd if g.cwd and os.path.isdir(g.cwd) else str(Path.home()))
        ok, _pid = proc.startDetached()
        if not ok:
            QMessageBox.warning(self, APP, f"Couldn't start {g.name}.\n\n{' '.join(g.cmd)}")
            return
        g.last_played = int(time.time())
        self.cfg["played"][g.id] = g.last_played
        # Games without controller support get keyboard/mouse from the pad (Steam Input style).
        controller.start_for(g, self.cfg, _pid, self.save)
        self.save()
        self.desktop.status.setText(f"Starting {g.name}…")
        self.desktop.details.set_game(self.desktop.current())
        self.big.update()

    def controller_dialog(self, g):
        from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QGridLayout
        d = QDialog(self)
        d.setWindowTitle(f"Controller: {g.name}")
        lay = QVBoxLayout(d)
        info = self.cfg.get("controller_info", {}).get(g.id)
        if info and info.get("page"):
            sup = {"false": "no controller support", "limited": "limited controller support",
                   "true": "full controller support"}.get(info.get("support"), "unknown controller support")
            auto = controller.auto_layout(info)
            note = (f"PCGamingWiki: {sup} ({', '.join(info.get('genres', [])) or 'genre unknown'}).\n"
                    f"Automatic uses: {auto.title() + ' layout' if auto else 'no mapping'}.")
        else:
            note = "Automatic looks the game up on PCGamingWiki the first time you play it."
        lay.addWidget(QLabel(note, wordWrap=True))
        combo = QComboBox()
        labels = {"auto": "Automatic", "action": "Action (WASD + mouse look)",
                  "mouse": "Mouse (stick moves the pointer)", "off": "Off (the game reads the controller)"}
        for c in controller.CHOICES:
            combo.addItem(labels[c], c)
        cur = self.cfg.get("controller", {}).get(g.id, "auto")
        combo.setCurrentIndex(controller.CHOICES.index(cur))
        lay.addWidget(combo)
        table = QWidget()
        grid = QGridLayout(table)
        lay.addWidget(table)

        def show():
            while grid.count():
                grid.takeAt(0).widget().deleteLater()
            c = combo.currentData()
            layout = c if c in ("action", "mouse") else controller.auto_layout(info) if c == "auto" else None
            if not layout:
                grid.addWidget(QLabel("No mapping: the game gets the controller as it is."), 0, 0)
                return
            try:
                from .padmap import describe
            except ImportError:
                grid.addWidget(QLabel("Controller mapping needs python-evdev."), 0, 0)
                return
            for i, (btn, what) in enumerate(describe(layout)):
                grid.addWidget(QLabel(f"<b>{btn}</b>"), i // 2, (i % 2) * 2)
                grid.addWidget(QLabel(what), i // 2, (i % 2) * 2 + 1)
        combo.currentIndexChanged.connect(lambda _i: show())
        show()
        lay.addWidget(QLabel("While playing: Guide + Back turns the mapping off and on.", objectName="gameinfo"))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        if d.exec() == QDialog.Accepted:
            self.cfg.setdefault("controller", {})[g.id] = combo.currentData()
            self.save()

    def find_patches(self, g, auto=False):
        from .patchui import PatchDialog
        d = PatchDialog(self, g, auto=auto)
        d.setAttribute(Qt.WA_DeleteOnClose)
        d.show()
        return d

    def patches_since(self, ts):
        """After a disc install: offer patches for the Windows game(s) it just added."""
        apps = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"
        new = [g for g in self.games if g.source == "Windows" and g.id.startswith("desktop:")
               and (apps / g.id[8:]).exists() and (apps / g.id[8:]).stat().st_mtime >= ts]
        # Several shortcuts per game are common (game, editor, readme): offer the main one.
        new.sort(key=lambda g: (bool(re.search(r"edit|setup|readme|uninstall|help|manual", g.name, re.I)),
                                len(g.name)))
        if new:
            self.find_patches(new[0], auto=True)
        return bool(new)

    def toggle_favorite(self, g):
        if not g:
            return
        g.favorite = not g.favorite
        fav = set(self.cfg["favorites"])
        fav.add(g.id) if g.favorite else fav.discard(g.id)
        self.cfg["favorites"] = sorted(fav)
        self.save()
        self.desktop.refill()
        self.big.update()

    def toggle_hidden(self, g):
        g.hidden = not g.hidden
        hid = set(self.cfg["hidden"])
        hid.add(g.id) if g.hidden else hid.discard(g.id)
        self.cfg["hidden"] = sorted(hid)
        self.save()
        self.desktop.refill()
        self.big.refill()

    def add_game(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Add a Game", str(Path.home()),
            "Games and programs (*.exe *.EXE *.bat *.BAT *.lnk *.sh *.x86_64 *.x86 *.AppImage *.appimage);;"
            "All files (*)")
        if not path:
            return
        p = Path(path)
        guess = p.stem
        # "Game.exe" / "launcher.sh" say less than the folder they're in.
        if guess.lower() in ("game", "launcher", "start", "run", "play", "main", "bin", "binaries", "win32",
                             "win64", "x64", "system"):
            guess = next((d.name for d in reversed(p.parents[:3]) if d.name.lower() not in
                          ("bin", "binaries", "win32", "win64", "x64", "system")), guess)
        name, ok = QInputDialog.getText(self, "Add a Game", "Name in your library:", text=guess)
        if not ok or not name.strip():
            return
        args, ok = QInputDialog.getText(self, "Add a Game", "Launch options (optional):")
        if not ok:
            return
        if not p.suffix.lower() in (".exe", ".bat", ".lnk", ".msi") and not os.access(p, os.X_OK):
            try:
                p.chmod(p.stat().st_mode | 0o111)
            except OSError:
                pass
        entry = library.custom_entry(p, name.strip(), args.strip())
        self.cfg["custom"].append(entry)
        self.save()
        self.reload()
        g = self.by_id.get(entry["id"])
        for i in range(self.desktop.tree.topLevelItemCount()):
            it = self.desktop.tree.topLevelItem(i)
            if g and it.data(0, Qt.UserRole) == g.id:
                self.desktop.tree.setCurrentItem(it)

    def rename(self, g):
        name, ok = QInputDialog.getText(self, "Rename", "Name in your library:", text=g.name)
        if ok and name.strip():
            for c in self.cfg["custom"]:
                if c["id"] == g.id:
                    c["name"] = name.strip()
            self.save()
            self.reload()

    def remove_custom(self, g):
        if QMessageBox.question(self, APP, f"Remove {g.name} from your library?\n\nThe game's files are kept.") \
                != QMessageBox.Yes:
            return
        self.cfg["custom"] = [c for c in self.cfg["custom"] if c["id"] != g.id]
        self.save()
        self.reload()

    # --- modes
    def toggle_big(self, keep_selection=True):
        if self.stack.currentWidget() is self.big:
            self.stack.setCurrentWidget(self.desktop)
            self.titlebar.show()
            self.layout().setContentsMargins(self.EDGE - 3, 2, self.EDGE - 3, self.EDGE - 3)
            self.setObjectName("root")
            self.showNormal()
            g = self.big.current()
            for i in range(self.desktop.tree.topLevelItemCount()):
                it = self.desktop.tree.topLevelItem(i)
                if g and it.data(0, Qt.UserRole) == g.id:
                    self.desktop.tree.setCurrentItem(it)
            self.desktop.tree.setFocus()
        else:
            self.big.refill()
            g = self.desktop.current() if keep_selection else None
            if g in self.big.games:
                self.big.index = self.big.games.index(g)
            self.titlebar.hide()
            self.layout().setContentsMargins(0, 0, 0, 0)
            self.big.go("home")
            self.stack.setCurrentWidget(self.big)
            self.showFullScreen()
            self.big.setFocus()
            self.big.play_intro()

    def on_pad(self, name):
        if not self.isActiveWindow() or QApplication.activeModalWidget():
            return
        view = self.stack.currentWidget()
        view.pad(name)

    # --- frameless resizing from the window edges
    def _edges(self, pos):
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edges()
        e = Qt.Edges()
        m = self.EDGE
        if pos.x() < m:
            e |= Qt.LeftEdge
        if pos.x() > self.width() - m:
            e |= Qt.RightEdge
        if pos.y() < m:
            e |= Qt.TopEdge
        if pos.y() > self.height() - m:
            e |= Qt.BottomEdge
        return e

    def mouseMoveEvent(self, e):
        edges = self._edges(e.position().toPoint())
        shapes = {Qt.LeftEdge: Qt.SizeHorCursor, Qt.RightEdge: Qt.SizeHorCursor, Qt.TopEdge: Qt.SizeVerCursor,
                  Qt.BottomEdge: Qt.SizeVerCursor, Qt.LeftEdge | Qt.TopEdge: Qt.SizeFDiagCursor,
                  Qt.RightEdge | Qt.BottomEdge: Qt.SizeFDiagCursor, Qt.RightEdge | Qt.TopEdge: Qt.SizeBDiagCursor,
                  Qt.LeftEdge | Qt.BottomEdge: Qt.SizeBDiagCursor}
        self.setCursor(shapes.get(edges, Qt.ArrowCursor))

    def mousePressEvent(self, e):
        edges = self._edges(e.position().toPoint())
        if edges and e.button() == Qt.LeftButton and self.windowHandle():
            self.windowHandle().startSystemResize(edges)


def main():
    ap = argparse.ArgumentParser(prog="halcyon-games", description="Halcyon game library")
    ap.add_argument("--big", action="store_true", help="start in full-screen gamepad mode")
    ap.add_argument("--screenshot", metavar="PNG", help=argparse.SUPPRESS)
    ap.add_argument("--patches-since", type=float, metavar="TIME",
                    help="offer patches for Windows games installed after TIME (used by the disc installer)")
    args = ap.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("halcyon-games")
    app.setDesktopFileName("halcyon-games")
    app.setStyle("Fusion")
    app.setStyleSheet(style.QSS)
    w = Launcher(big=args.big)
    if args.screenshot:                       # development: render offscreen and save
        w.resize(1280, 720) if args.big else None
        w.show()
        QTimer.singleShot(300, lambda: (w.grab().save(args.screenshot), app.quit()))
        return app.exec()
    w.show()
    if args.patches_since:
        QTimer.singleShot(500, lambda: w.patches_since(args.patches_since))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
