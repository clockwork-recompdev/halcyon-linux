"""Halcyon on-screen keyboard: a gamepad-driven keyboard docked at the bottom
of the screen that types into whatever window has focus.

    halcyon-osk [show|hide|toggle]   talk to the running keyboard (starts it if needed)
    halcyon-osk --daemon             start hidden; Guide + X on a gamepad shows it

Keys are sent through a virtual uinput keyboard, so they reach any app (Wayland
or X11). The window itself is an X11 override-redirect window that never takes
focus, so it can sit above full-screen games without stealing their input."""
import os
import sys

if os.environ.get("DISPLAY"):
    os.environ["QT_QPA_PLATFORM"] = "xcb"     # needed for a never-focused, always-on-top window

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QWidget

try:
    import evdev
    from evdev import ecodes as E
    from .gamepad import Gamepads
except ImportError:
    evdev = None
    Gamepads = None

NAME = f"halcyon-osk-{os.getuid()}"
DEBUG = bool(os.environ.get("HALCYON_OSK_DEBUG"))


def log(*a):
    if DEBUG:
        print("osk:", *a, file=sys.stderr, flush=True)
ACCENT = QColor("#1e9be0")

# (label, shifted label, key, width in key units); every row is 15 units wide.
_ = None
ROWS = [
    [("`", "~", "GRAVE", 1)] + [(c, s, c, 1) for c, s in zip("1234567890", "!@#$%^&*()")] +
    [("-", "_", "MINUS", 1), ("=", "+", "EQUAL", 1), ("⌫", _, "BACKSPACE", 2)],
    [("Tab", _, "TAB", 1.5)] + [(c, c.upper(), c.upper(), 1) for c in "qwertyuiop"] +
    [("[", "{", "LEFTBRACE", 1), ("]", "}", "RIGHTBRACE", 1), ("\\", "|", "BACKSLASH", 1.5)],
    [("Caps", _, "@caps", 1.75)] + [(c, c.upper(), c.upper(), 1) for c in "asdfghjkl"] +
    [(";", ":", "SEMICOLON", 1), ("'", '"', "APOSTROPHE", 1), ("Enter", _, "ENTER", 2.25)],
    [("Shift", _, "@shift", 2.25)] + [(c, c.upper(), c.upper(), 1) for c in "zxcvbnm"] +
    [(",", "<", "COMMA", 1), (".", ">", "DOT", 1), ("/", "?", "SLASH", 1), ("Shift", _, "@shift", 2.75)],
    [("Esc", _, "ESC", 1.25), ("Ctrl", _, "@ctrl", 1.25), ("Alt", _, "@alt", 1.25), ("Space", _, "SPACE", 6.25),
     ("←", _, "LEFT", 1), ("↑", _, "UP", 1), ("↓", _, "DOWN", 1), ("→", _, "RIGHT", 1), ("Hide", _, "@hide", 1)],
]
HINTS = [("A", "#5fb336", "Type"), ("X", "#3a8ee6", "Delete"), ("Y", "#e6b422", "Space"),
         ("LB", "#6b7280", "Shift"), ("RB", "#6b7280", "Enter"), ("B", "#d9452b", "Close")]


def keycode(name):
    return getattr(E, "KEY_" + name)


class Keyboard(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.X11BypassWindowManagerHint)
        self.setWindowTitle("On-Screen Keyboard")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.row, self.col = 2, 1                   # start on "a"
        self.shift = self.caps = self.ctrl = self.alt = False
        self.flash = None
        self.error = ""
        self.ui = None
        self._flash_timer = QTimer(self, singleShot=True, interval=120, timeout=self._unflash)
        try:
            codes = [keycode(k) for row in ROWS for _l, _s, k, _w in row if not k.startswith("@")]
            codes += [E.KEY_LEFTSHIFT, E.KEY_LEFTCTRL, E.KEY_LEFTALT]
            self.ui = evdev.UInput({E.EV_KEY: codes}, name="Halcyon On-Screen Keyboard")
        except Exception as e:                      # no python-evdev, or no access to /dev/uinput
            self.error = f"Can't type: no access to /dev/uinput ({e.__class__.__name__})"
        self.pads = Gamepads(self, repeat=("up", "down", "left", "right", "x")) if Gamepads else None
        if self.pads:
            self.pads.pressed.connect(self.on_pad)

    # --------------------------------------------------------- show/hide ---
    def place(self):
        scr = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        g = scr.geometry()
        w = min(int(g.width() * 0.66), 1180)
        self.unit = (w - 32 - 14 * 6) / 15
        self.keyh = int(self.unit * 0.86)
        h = 16 + 5 * self.keyh + 4 * 6 + 46
        self.setGeometry(g.x() + (g.width() - w) // 2, g.bottom() - h - int(g.height() * 0.03), w, h)

    def show_kb(self):
        self.place()
        self.show()
        self.raise_()
        if self.pads:
            self.pads.grab(True)                   # the game shouldn't also see our button presses
        log("shown", self.geometry(), "pads:", [d.name for d, *_ in (self.pads.devs.values() if self.pads else [])])

    def hide_kb(self):
        self.hide()
        if self.pads:
            self.pads.grab(False)
        log("hidden")

    def toggle(self):
        self.hide_kb() if self.isVisible() else self.show_kb()

    def command(self, cmd):
        {"show": self.show_kb, "hide": self.hide_kb, "toggle": self.toggle,
         "quit": QApplication.quit}.get(cmd, lambda: None)()

    # ------------------------------------------------------------ typing ---
    def tap(self, code, mods=()):
        if not self.ui:
            return
        for m in mods:
            self.ui.write(E.EV_KEY, m, 1)
            self.ui.syn()
        self.ui.write(E.EV_KEY, code, 1)
        self.ui.syn()
        self.ui.write(E.EV_KEY, code, 0)
        self.ui.syn()
        for m in reversed(mods):
            self.ui.write(E.EV_KEY, m, 0)
            self.ui.syn()

    def press(self, r, c):
        label, shifted, key, _w = ROWS[r][c]
        self.flash = (r, c)
        self._flash_timer.start()
        if key == "@shift":
            self.shift = not self.shift
        elif key == "@caps":
            self.caps = not self.caps
        elif key == "@ctrl":
            self.ctrl = not self.ctrl
        elif key == "@alt":
            self.alt = not self.alt
        elif key == "@hide":
            self.hide_kb()
            return
        else:
            letter = len(key) == 1 and key.isalpha()
            mods = []
            if shifted and (self.shift != (self.caps and letter)):
                mods.append(E.KEY_LEFTSHIFT)
            if self.ctrl:
                mods.append(E.KEY_LEFTCTRL)
            if self.alt:
                mods.append(E.KEY_LEFTALT)
            self.tap(keycode(key), mods)
            self.shift = self.ctrl = self.alt = False   # one-shot, like a phone keyboard
        self.update()

    def _unflash(self):
        self.flash = None
        self.update()

    # ------------------------------------------------------------- input ---
    def centers(self, r):
        x = 16
        out = []
        for _l, _s, _k, w in ROWS[r]:
            kw = w * self.unit + (w - 1) * 6
            out.append(x + kw / 2)
            x += kw + 6
        return out

    def move(self, d):
        if d in ("left", "right"):
            self.col = (self.col + (1 if d == "right" else -1)) % len(ROWS[self.row])
        else:
            cx = self.centers(self.row)[self.col]
            self.row = (self.row + (1 if d == "down" else -1)) % len(ROWS)
            cs = self.centers(self.row)
            self.col = min(range(len(cs)), key=lambda i: abs(cs[i] - cx))
        self.update()

    def find(self, key):
        return next((r, c) for r, row in enumerate(ROWS) for c, k in enumerate(row) if k[2] == key)

    def on_pad(self, name):
        held = self.pads.held
        log("pad", name, "held", sorted(held), "key", ROWS[self.row][self.col][0])
        if name == "x" and "guide" in held:        # Guide + X: show / hide from anywhere
            self.toggle()
            return
        if not self.isVisible():
            return
        if name in ("up", "down", "left", "right"):
            self.move(name)
        elif name == "a":
            self.press(self.row, self.col)
        elif name == "b":
            self.hide_kb()
        elif name == "x":
            self.press(*self.find("BACKSPACE"))
        elif name == "y":
            self.press(*self.find("SPACE"))
        elif name in ("lb", "lt"):
            self.press(*self.find("@shift"))
        elif name == "rb":
            self.press(*self.find("ENTER"))
        elif name == "rt":
            self.press(*self.find("@caps"))
        elif name == "start":
            self.press(*self.find("ENTER"))

    def key_at(self, pos):
        for r in range(len(ROWS)):
            for c, rect in enumerate(self.rects(r)):
                if rect.contains(pos):
                    return r, c
        return None

    def mousePressEvent(self, e):
        hit = self.key_at(e.position())
        if hit:
            self.row, self.col = hit
            self.press(*hit)

    # ----------------------------------------------------------- drawing ---
    def rects(self, r):
        y = 16 + r * (self.keyh + 6)
        x = 16
        out = []
        for _l, _s, _k, w in ROWS[r]:
            kw = w * self.unit + (w - 1) * 6
            out.append(QRectF(x, y, kw, self.keyh))
            x += kw + 6
        return out

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(16, 20, 28, 238))
        p.drawRoundedRect(body, 16, 16)

        f = QFont("Source Sans 3, DejaVu Sans")
        f.setPixelSize(int(self.keyh * 0.42))
        small = QFont(f)
        small.setPixelSize(int(self.keyh * 0.30))
        upper = self.shift
        for r, row in enumerate(ROWS):
            for c, rect in enumerate(self.rects(r)):
                label, shifted, key, _w = row[c]
                special = key.startswith("@") or len(label) > 1 and label not in ("⌫",)
                on = (key == "@shift" and self.shift) or (key == "@caps" and self.caps) or \
                     (key == "@ctrl" and self.ctrl) or (key == "@alt" and self.alt)
                sel = (r, c) == (self.row, self.col)
                fill = QColor(46, 54, 70) if not special else QColor(34, 40, 53)
                if (r, c) == self.flash:
                    fill = QColor(ACCENT).lighter(130)
                elif sel:
                    fill = QColor(ACCENT)
                p.setPen(QPen(QColor(ACCENT), 2) if on and not sel else Qt.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(rect, 7, 7)
                letter = len(key) == 1 and key.isalpha()
                text = shifted if shifted and (upper != (self.caps and letter)) else label
                p.setFont(small if len(text) > 1 and text not in ("⌫",) else f)
                p.setPen(QColor(255, 255, 255) if sel or on else QColor(225, 230, 240))
                p.drawText(rect, Qt.AlignCenter, text)

        # Hint bar
        y = 16 + 5 * (self.keyh + 6) + 6
        hf = QFont("Source Sans 3, DejaVu Sans")
        hf.setPixelSize(15)
        fm = QFontMetrics(hf)
        if self.error or (self.pads and not self.pads.connected):
            p.setFont(hf)
            p.setPen(QColor(240, 180, 48) if self.error else QColor(170, 178, 190))
            p.drawText(QRectF(20, y, self.width() - 40, 30), Qt.AlignVCenter | Qt.AlignLeft,
                       self.error or "Tip: Guide + X on a gamepad opens this keyboard anywhere")
            if self.error:
                return
        if not (self.pads and self.pads.connected):
            return
        total = sum(fm.horizontalAdvance(l) + 28 + 18 for _g, _c, l in HINTS)
        x = (self.width() - total) / 2
        for glyph, col, label in HINTS:
            gr = QRectF(x, y + 4, 22 if len(glyph) == 1 else 28, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col))
            if len(glyph) == 1:
                p.drawEllipse(gr)
            else:
                p.drawRoundedRect(gr, 6, 6)
            gf = QFont(hf)
            gf.setBold(True)
            gf.setPixelSize(12)
            p.setFont(gf)
            p.setPen(QColor("white"))
            p.drawText(gr, Qt.AlignCenter, glyph)
            x = gr.right() + 6
            p.setFont(hf)
            p.setPen(QColor(215, 220, 230))
            p.drawText(QRectF(x, y + 4, 200, 22), Qt.AlignVCenter | Qt.AlignLeft, label)
            x += fm.horizontalAdvance(label) + 18


def send(cmd):
    """Pass a command to a running keyboard. True if one was running."""
    s = QLocalSocket()
    s.connectToServer(NAME)
    if not s.waitForConnected(300):
        return False
    s.write(cmd.encode() + b"\n")
    s.flush()
    s.waitForBytesWritten(300)
    s.disconnectFromServer()
    return True


def main():
    args = sys.argv[1:]
    daemon = "--daemon" in args
    cmd = next((a for a in args if not a.startswith("-")), "status" if daemon else "toggle")
    if send(cmd if not daemon else "status"):
        return 0
    if cmd in ("hide", "quit"):
        return 0
    app = QApplication(sys.argv[:1])
    app.setApplicationName("halcyon-osk")
    app.setQuitOnLastWindowClosed(False)
    kb = Keyboard()
    server = QLocalServer()
    QLocalServer.removeServer(NAME)              # stale socket from a crash
    server.listen(NAME)

    def incoming():
        c = server.nextPendingConnection()
        def read():
            while c.canReadLine():
                kb.command(bytes(c.readLine()).decode().strip())
        c.readyRead.connect(read)
        c.disconnected.connect(c.deleteLater)
    server.newConnection.connect(incoming)
    if cmd in ("show", "toggle"):
        kb.show_kb()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
