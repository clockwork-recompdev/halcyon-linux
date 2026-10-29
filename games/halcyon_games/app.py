"""Halcyon Games: one list for Steam, Windows (Wine) and Linux games, in a
classic game-client window, plus a full-screen menu for playing with a gamepad."""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QProcess, QProcessEnvironment, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QFontMetrics, QIcon, QKeySequence, QLinearGradient,
                           QPainter, QPen, QPixmap, QRadialGradient, QShortcut)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QSizeGrip, QStackedWidget, QTabBar,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import library, style

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
class BigView(QWidget):
    """Full-screen menu in the style of an early-2000s game's main menu:
    the selected game's art fills the screen, names are listed on the left."""

    def __init__(self, launcher):
        super().__init__()
        self.l = launcher
        self.setFocusPolicy(Qt.StrongFocus)
        self.games = []
        self.index = 0
        self.search = QLineEdit(self, objectName="bigsearch", placeholderText="Search")
        self.search.hide()
        self.search.setStyleSheet(
            "QLineEdit { background: rgba(0,0,0,170); color: white; border: 1px solid rgba(255,255,255,90);"
            " padding: 6px 10px; font-size: 18pt; }")
        self.search.textChanged.connect(lambda _t: self.refill())
        self.search.returnPressed.connect(self.close_search_keep)
        self.search.installEventFilter(self)
        self.clock = QTimer(self, interval=20000, timeout=self.update)
        self.clock.start()

    # --- data
    def refill(self):
        keep = self.current()
        q = self.search.text().strip().casefold() if self.search.isVisible() or self.search.text() else ""
        games = [g for g in self.l.games if not g.hidden and (not q or q in g.name.casefold())]
        # Like a console: recently played first, favorites next, then A-Z.
        games.sort(key=lambda g: (-g.last_played, not g.favorite, g.name.casefold()))
        self.games = games
        self.index = next((i for i, g in enumerate(games) if keep and g.id == keep.id), 0)
        self.update()

    def current(self):
        return self.games[self.index] if 0 <= self.index < len(self.games) else None

    def move(self, d):
        if self.games:
            self.index = max(0, min(len(self.games) - 1, self.index + d))
            self.update()

    # --- search
    def open_search(self, text=""):
        self.search.show()
        self.search.setFocus()
        if text:
            self.search.setText(self.search.text() + text)
        self.layout_search()
        show_keyboard()

    def close_search_keep(self):
        self.search.hide()
        self.setFocus()
        self.update()

    def close_search(self):
        self.search.clear()
        self.close_search_keep()
        self.refill()

    def layout_search(self):
        w = int(self.width() * 0.32)
        self.search.setGeometry(int(self.width() * 0.06), int(self.height() * 0.33), w, 46)

    def resizeEvent(self, e):
        self.layout_search()

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == QEvent.KeyPress:
            if e.key() == Qt.Key_Escape:
                self.close_search()
                return True
            if e.key() in (Qt.Key_Up, Qt.Key_Down):
                self.move(-1 if e.key() == Qt.Key_Up else 1)
                return True
        return False

    # --- input
    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Up, Qt.Key_Down):
            self.move(-1 if k == Qt.Key_Up else 1)
        elif k in (Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Left, Qt.Key_Right):
            self.move(-6 if k in (Qt.Key_PageUp, Qt.Key_Left) else 6)
        elif k == Qt.Key_Home:
            self.move(-len(self.games))
        elif k == Qt.Key_End:
            self.move(len(self.games))
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.l.launch(self.current())
        elif k == Qt.Key_Escape:
            self.l.toggle_big()
        elif e.text() and e.text().isprintable() and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            self.search.show()
            self.search.setFocus()
            self.search.setText(self.search.text() + e.text())
            self.layout_search()
        else:
            super().keyPressEvent(e)

    def pad(self, name):
        if name in ("up", "down"):
            self.move(-1 if name == "up" else 1)
        elif name in ("left", "right", "lb", "rb"):
            self.move(-6 if name in ("left", "lb") else 6)
        elif name == "a":
            self.l.launch(self.current())
        elif name == "b":
            if self.search.isVisible() or self.search.text():
                self.close_search()
        elif name == "x":
            self.l.toggle_favorite(self.current())
        elif name == "y":
            self.open_search()
        elif name in ("start", "select"):
            self.l.toggle_big()

    def wheelEvent(self, e):
        self.move(-1 if e.angleDelta().y() > 0 else 1)

    def mousePressEvent(self, e):
        i = self.row_at(e.position().toPoint())
        if i is not None:
            if i == self.index and e.button() == Qt.LeftButton:
                self.l.launch(self.current())
            self.index = i
            self.update()

    # --- drawing
    def _rows(self):
        H = self.height()
        rh = max(28, H // 19)
        y0 = int(H * 0.42)
        vis = max(1, int((H * 0.86 - y0) / rh))
        start = max(0, min(self.index - vis // 3, len(self.games) - vis))
        return y0, rh, vis, start

    def row_at(self, pos):
        y0, rh, vis, start = self._rows()
        if pos.x() > self.width() * 0.6 or pos.y() < y0:
            return None
        i = start + (pos.y() - y0) // rh
        return i if i < len(self.games) and i < start + vis else None

    def _text(self, p, x, y, text, font, color, glow=False):
        p.setFont(font)
        p.setPen(QColor(0, 0, 0, 200))
        p.drawText(x + 2, y + 2, text)
        if glow:
            p.setPen(QColor(255, 236, 160, 60))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p.drawText(x + dx, y + dy, text)
        p.setPen(color)
        p.drawText(x, y, text)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        g = self.current()

        hero = pixmap(g.hero if g else None)
        if not hero.isNull():
            s = hero.scaled(W, H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((W - s.width()) // 2, (H - s.height()) // 2, s)
        else:
            rg = QRadialGradient(W * 0.7, H * 0.35, W * 0.8)
            rg.setColorAt(0, QColor("#3a4430"))
            rg.setColorAt(1, QColor("#0b0d09"))
            p.fillRect(self.rect(), rg)
        shade = QLinearGradient(0, 0, W, 0)
        shade.setColorAt(0, QColor(6, 8, 5, 235))
        shade.setColorAt(0.45, QColor(6, 8, 5, 170))
        shade.setColorAt(1, QColor(6, 8, 5, 40))
        p.fillRect(self.rect(), shade)
        bottom = QLinearGradient(0, H * 0.7, 0, H)
        bottom.setColorAt(0, QColor(0, 0, 0, 0))
        bottom.setColorAt(1, QColor(0, 0, 0, 200))
        p.fillRect(self.rect(), bottom)

        x0 = int(W * 0.06)
        family = "Trebuchet MS, Verdana, DejaVu Sans"
        # Title: the game's logo art, else its name.
        if g:
            logo = pixmap(g.logo)
            box = QRect(x0, int(H * 0.07), int(W * 0.38), int(H * 0.2))
            if not logo.isNull():
                s = logo.scaled(box.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                p.drawPixmap(box.x(), box.bottom() - s.height(), s)
            else:
                f = QFont(family)
                f.setPixelSize(max(24, H // 13))
                f.setBold(True)
                fm = QFontMetrics(f)
                self._text(p, x0, box.bottom() - fm.descent(), fm.elidedText(g.name, Qt.ElideRight, int(W * 0.8)),
                           f, QColor("#f2f0e6"))
            f = QFont(family)
            f.setPixelSize(max(13, H // 48))
            info = f"{g.source}   ·   Last played: {when(g.last_played)}" + ("   ·   ★ Favorite" if g.favorite else "")
            self._text(p, x0 + 2, int(H * 0.27) + f.pixelSize() * 2, info, f, QColor(220, 220, 205, 190))

        # Menu
        y0, rh, vis, start = self._rows()
        f = QFont(family)
        f.setPixelSize(max(16, int(rh * 0.62)))
        fm = QFontMetrics(f)
        if not self.games:
            self._text(p, x0, y0 + rh, "No games found" if self.search.text() else "No games installed yet",
                       f, QColor(220, 220, 205, 200))
        for row, i in enumerate(range(start, min(len(self.games), start + vis))):
            y = y0 + row * rh + fm.ascent()
            name = fm.elidedText(self.games[i].name, Qt.ElideRight, int(W * 0.5))
            if i == self.index:
                p.fillRect(x0 - 18, y0 + row * rh + rh // 5, 4, rh * 3 // 5, QColor("#C4B550"))
                self._text(p, x0, y, name, f, QColor("#ffffff"), glow=True)
            else:
                fade = 210 - min(120, abs(i - self.index) * 18)
                self._text(p, x0, y, name, f, QColor(200, 200, 186, fade))
        if start > 0:
            self._text(p, x0, y0 - rh // 4, "▲", f, QColor(200, 200, 186, 120))
        if start + vis < len(self.games):
            self._text(p, x0, y0 + vis * rh + fm.ascent(), "▼", f, QColor(200, 200, 186, 120))

        # Clock, top right
        f2 = QFont(family)
        f2.setPixelSize(max(14, H // 36))
        clock = time.strftime("%-I:%M %p")
        self._text(p, W - x0 - QFontMetrics(f2).horizontalAdvance(clock), int(H * 0.07) + f2.pixelSize(),
                   clock, f2, QColor(235, 235, 225, 210))

        # Button hints, bottom right
        hints = [("A", "#5fb336", "Play"), ("X", "#3a8ee6", "Favorite"), ("Y", "#e6b422", "Search"),
                 ("☰", "#8a8a80", "Desktop")]
        if self.search.text():
            hints.insert(1, ("B", "#d9452b", "Clear search"))
        f3 = QFont(family)
        f3.setPixelSize(max(13, H // 50))
        fm3 = QFontMetrics(f3)
        r = f3.pixelSize()
        x = W - x0
        y = H - int(H * 0.06)
        for glyph, col, label in reversed(hints):
            x -= fm3.horizontalAdvance(label)
            self._text(p, x, y + fm3.ascent() // 2 - 1, label, f3, QColor(235, 235, 225, 220))
            x -= r + 8
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col))
            p.drawEllipse(QPoint(x + r // 2, y - 1), r * 0.62, r * 0.62)
            p.setPen(QColor("white"))
            gf = QFont(family)
            gf.setPixelSize(int(r * 0.8))
            gf.setBold(True)
            p.setFont(gf)
            p.drawText(QRect(x + r // 2 - r, y - 1 - r, 2 * r, 2 * r), Qt.AlignCenter, glyph)
            x -= 26


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
        self.desktop.refill()
        self.big.refill()

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
        self.save()
        self.desktop.status.setText(f"Starting {g.name}…")
        self.desktop.details.set_game(self.desktop.current())
        self.big.update()

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
            self.stack.setCurrentWidget(self.big)
            self.showFullScreen()
            self.big.setFocus()

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
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
