"""Halcyon Sticky Notes, after Windows 7's: pastel notes on the desktop with
+ and × in their top strip, a choice of six colors, and simple formatting
(Ctrl+B / I / U, Ctrl+T strikethrough, Ctrl+Shift+L bullets, Ctrl+Shift+>
and < for size). Everything saves as you type and comes back at logon.

    halcyon-notes            a new note (or bring the notes forward)
    halcyon-notes --restore  just reopen saved notes (logon)"""
import json
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")      # notes reopen where you left them (Wayland can't)

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import (QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QLinearGradient,  # noqa: E402
                           QPainter, QPainterPath, QPen, QTextCharFormat, QTextCursor, QTextListFormat)
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QMenu, QMessageBox, QSizeGrip, QTextEdit,  # noqa: E402
                               QVBoxLayout, QWidget)

STORE = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "sticky-notes.json"
SOCKET = "halcyon-sticky-notes-" + str(os.getuid())
# name: (body top, body bottom, strip, edge)
COLORS = {
    "Yellow": ("#fdfdca", "#f8f29f", "#f5ec85", "#d8cc63"),
    "Blue": ("#ddf3fe", "#bfe6fb", "#a9dbf6", "#7fb9d8"),
    "Green": ("#d7fbd2", "#bdf2b5", "#a6e89c", "#7cc271"),
    "Pink": ("#fde0f2", "#f9c6e4", "#f4b0d8", "#d38bb6"),
    "Purple": ("#ebe3fd", "#d9cbfb", "#c9b6f6", "#a08ad6"),
    "White": ("#ffffff", "#f4f4f4", "#e9e9e9", "#c4c4c4"),
}
SHADOW = 6
STRIP = 22


def note_font():
    fams = QFontDatabase.families()
    for name in ("Segoe Print", "Comic Neue", "Comic Shanns", "Comic Sans MS", "Noto Sans"):
        if name in fams:
            return QFont(name, 11 if name != "Segoe Print" else 10)
    return QFont(QApplication.font().family(), 11)


class Notes:
    def __init__(self):
        self.windows = []
        self.data = self.load()
        self.ask_delete = self.data.get("ask_delete", True)
        self.save_timer = QTimer(singleShot=True, interval=700, timeout=self.save)

    def load(self):
        try:
            return json.loads(STORE.read_text())
        except (OSError, ValueError):
            return {"notes": []}

    def save(self):
        notes = [w.state() for w in self.windows]
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"notes": notes, "ask_delete": self.ask_delete}, indent=1, ensure_ascii=False))
        tmp.replace(STORE)

    def changed(self):
        self.save_timer.start()

    def restore(self):
        for n in self.data.get("notes", []):
            self.open(n)
        return bool(self.windows)

    def open(self, state=None, near=None):
        w = Note(self, state or {})
        if not state:
            if near is not None:
                w.move(near.pos() + QPoint(28, 28))
            else:
                scr = QApplication.primaryScreen().availableGeometry()
                k = len(self.windows) % 8
                w.move(scr.right() - w.width() - 40 - k * 24, scr.top() + 40 + k * 24)
        self.windows.append(w)
        w.show()
        w.raise_()
        w.activateWindow()
        if not state:
            w.edit.setFocus()
        self.changed()
        return w

    def close_note(self, w):
        self.windows.remove(w)
        w.deleteLater()
        self.save()
        if not self.windows:
            QApplication.quit()

    def raise_all(self):
        for w in self.windows:
            w.showNormal()
            w.raise_()
        if self.windows:
            self.windows[-1].activateWindow()


class Strip(QWidget):
    """The darker band across the top: + on the left, × on the right; drag to move."""
    def __init__(self, note):
        super().__init__(note)
        self.note = note
        self.setFixedHeight(STRIP)
        self.setMouseTracking(True)
        self.hover = None
        self.drag = None

    def zones(self):
        return QRect(2, 1, 22, STRIP - 2), QRect(self.width() - 24, 1, 22, STRIP - 2)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        plus, close = self.zones()
        for key, r in (("plus", plus), ("close", close)):
            if self.hover == key:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 120))
                p.drawRoundedRect(QRectF(r).adjusted(2, 2, -2, -2), 3, 3)
        ink = QColor(0, 0, 0, 150)
        p.setPen(QPen(ink, 1.8, Qt.SolidLine, Qt.RoundCap))
        c = plus.center()
        p.drawLine(c.x() - 5, c.y(), c.x() + 5, c.y())
        p.drawLine(c.x(), c.y() - 5, c.x(), c.y() + 5)
        c = close.center()
        p.drawLine(c.x() - 4, c.y() - 4, c.x() + 4, c.y() + 4)
        p.drawLine(c.x() + 4, c.y() - 4, c.x() - 4, c.y() + 4)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self.drag is not None:
            self.note.move(e.globalPosition().toPoint() - self.drag)
            return
        plus, close = self.zones()
        h = "plus" if plus.contains(pos) else "close" if close.contains(pos) else None
        if h != self.hover:
            self.hover = h
            self.setToolTip({"plus": "New Note (Ctrl+N)", "close": "Delete Note (Ctrl+D)"}.get(h, ""))
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        plus, close = self.zones()
        pos = e.position().toPoint()
        if plus.contains(pos):
            self.note.notes.open(near=self.note)
        elif close.contains(pos):
            self.note.delete()
        else:
            h = self.note.windowHandle()
            if h is not None and h.startSystemMove():
                return
            self.drag = e.globalPosition().toPoint() - self.note.pos()

    def mouseReleaseEvent(self, e):
        if self.drag is not None:
            self.drag = None
            self.note.notes.changed()


class Note(QWidget):
    def __init__(self, notes, state):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.notes = notes
        self.id = state.get("id") or uuid.uuid4().hex[:10]
        self.color = state.get("color", "Yellow")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Sticky Notes")
        self.setWindowIcon(QIcon.fromTheme("knotes", QIcon.fromTheme("note")))
        self.setMinimumSize(160, 120)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SHADOW, SHADOW // 2, SHADOW, SHADOW + 2)
        lay.setSpacing(0)
        self.strip = Strip(self)
        lay.addWidget(self.strip)
        self.edit = QTextEdit()
        self.edit.setFrameShape(QTextEdit.NoFrame)
        self.edit.setFont(note_font())
        self.edit.document().setDefaultFont(note_font())
        self.edit.setStyleSheet("QTextEdit { background: transparent; color: #1a1a1a; padding: 2px 6px; "
                                "selection-background-color: rgba(0, 90, 200, 90); selection-color: black; }")
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.edit.customContextMenuRequested.connect(self.menu)
        if state.get("html"):
            self.edit.setHtml(state["html"])
        self.edit.textChanged.connect(self.notes.changed)
        lay.addWidget(self.edit, 1)
        self.grip = QSizeGrip(self)
        self.grip.setStyleSheet("background: transparent;")
        self.grip.setFixedSize(14, 14)
        self.resize(state.get("w", 240), state.get("h", 230))
        if "x" in state:
            self.move(state["x"], state["y"])
        for key, fn in (("Ctrl+N", lambda: self.notes.open(near=self)), ("Ctrl+D", self.delete),
                        ("Ctrl+B", lambda: self.fmt(bold=True)), ("Ctrl+I", lambda: self.fmt(italic=True)),
                        ("Ctrl+U", lambda: self.fmt(underline=True)), ("Ctrl+T", lambda: self.fmt(strike=True)),
                        ("Ctrl+Shift+L", self.bullets), ("Ctrl+Shift+>", lambda: self.fmt(size=+1)),
                        ("Ctrl+Shift+<", lambda: self.fmt(size=-1)), ("Ctrl+Shift+.", lambda: self.fmt(size=+1)),
                        ("Ctrl+Shift+,", lambda: self.fmt(size=-1))):
            a = QAction(self, shortcut=QKeySequence(key))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            a.triggered.connect(fn)
            self.addAction(a)

    def state(self):
        g = self.geometry()
        return {"id": self.id, "color": self.color, "html": self.edit.toHtml(), "x": g.x(), "y": g.y(),
                "w": g.width(), "h": g.height()}

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        top, bottom, strip, edge = COLORS.get(self.color, COLORS["Yellow"])
        body = QRectF(self.rect()).adjusted(SHADOW, SHADOW // 2, -SHADOW, -SHADOW - 2)
        for i in range(SHADOW, 0, -1):                     # soft shadow, lower right
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 10 + (SHADOW - i) * 3))
            p.drawRoundedRect(body.adjusted(-i + 2, -i + 3, i, i + 2), i, i)
        g = QLinearGradient(0, body.top(), 0, body.bottom())
        g.setColorAt(0, QColor(top))
        g.setColorAt(1, QColor(bottom))
        p.setPen(QPen(QColor(edge), 1))
        p.setBrush(g)
        p.drawRect(body)
        s = QRectF(body.left() + 0.5, body.top() + 0.5, body.width() - 1, STRIP)
        sg = QLinearGradient(0, s.top(), 0, s.bottom())
        sg.setColorAt(0, QColor(strip).lighter(104))
        sg.setColorAt(1, QColor(strip))
        p.setPen(Qt.NoPen)
        p.setBrush(sg)
        p.drawRect(s)
        # the folded-paper sheen down the right edge
        sheen = QLinearGradient(body.right() - 30, 0, body.right(), 0)
        sheen.setColorAt(0, QColor(255, 255, 255, 0))
        sheen.setColorAt(1, QColor(255, 255, 255, 60))
        p.fillRect(QRectF(body.right() - 30, s.bottom(), 29, body.height() - STRIP - 1), sheen)

    def resizeEvent(self, e):
        self.grip.move(self.width() - SHADOW - self.grip.width(), self.height() - SHADOW - 2 - self.grip.height())
        self.notes.changed()

    def moveEvent(self, e):
        self.notes.changed()

    def fmt(self, bold=False, italic=False, underline=False, strike=False, size=0):
        cur = self.edit.currentCharFormat()
        f = QTextCharFormat()
        if bold:
            f.setFontWeight(QFont.Normal if cur.fontWeight() > QFont.Normal else QFont.Bold)
        if italic:
            f.setFontItalic(not cur.fontItalic())
        if underline:
            f.setFontUnderline(not cur.fontUnderline())
        if strike:
            f.setFontStrikeOut(not cur.fontStrikeOut())
        if size:
            pt = cur.fontPointSize() or self.edit.font().pointSizeF()
            f.setFontPointSize(max(6, min(48, pt + size * 2)))
        self.edit.mergeCurrentCharFormat(f)

    def bullets(self):
        c = self.edit.textCursor()
        lst = c.currentList()
        if lst:
            fmt = c.blockFormat()
            fmt.setIndent(0)
            lst.remove(c.block())
            c.setBlockFormat(fmt)
        else:
            c.createList(QTextListFormat.ListDisc)

    def menu(self, pos):
        m = self.edit.createStandardContextMenu()
        m.addSeparator()
        for name in COLORS:
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(name == self.color)
            a.triggered.connect(lambda _=False, n=name: self.set_color(n))
        m.exec(self.edit.mapToGlobal(pos))

    def set_color(self, name):
        self.color = name
        self.update()
        self.notes.changed()

    def delete(self):
        if self.edit.toPlainText().strip() and self.notes.ask_delete:
            box = QMessageBox(QMessageBox.Warning, "Sticky Notes", "Are you sure you want to delete this note?",
                              QMessageBox.Yes | QMessageBox.No, self)
            dont = QCheckBox("Don't display this message again")
            box.setCheckBox(dont)
            if box.exec() != QMessageBox.Yes:
                return
            if dont.isChecked():
                self.notes.ask_delete = False
        self.close()
        self.notes.close_note(self)


def main():
    restore_only = "--restore" in sys.argv
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if sock.waitForConnected(300):                     # already running: ask it for a note
        if not restore_only:
            sock.write(b"new\n")
            sock.flush()
            sock.waitForBytesWritten(500)
        return 0
    app = QApplication(sys.argv[:1])
    app.setApplicationName("halcyon-notes")
    app.setDesktopFileName("halcyon-notes")
    app.setQuitOnLastWindowClosed(False)
    notes = Notes()
    QLocalServer.removeServer(SOCKET)
    server = QLocalServer()
    server.listen(SOCKET)

    def incoming():
        c = server.nextPendingConnection()
        c.readyRead.connect(lambda c=c: (bytes(c.readAll()).strip() == b"new" and
                                         (notes.raise_all(), notes.open())))
    server.newConnection.connect(incoming)
    had = notes.restore()
    if restore_only and not had:
        return 0
    if not restore_only:
        if had:
            notes.raise_all()
        else:
            notes.open()
    app.aboutToQuit.connect(notes.save)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
