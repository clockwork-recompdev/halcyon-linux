"""Gamepad input straight from evdev, so it works whether or not our window
has focus (the on-screen keyboard never does).

Buttons are named by their Xbox positions: a b x y lb rb lt rt select start
guide ls rs, plus up/down/left/right from the d-pad or left stick."""
import time

import evdev
from evdev import ecodes as E
from PySide6.QtCore import QObject, QSocketNotifier, QTimer, Signal

KEYS = {
    E.BTN_SOUTH: "a", E.BTN_EAST: "b", E.BTN_NORTH: "x", E.BTN_WEST: "y",
    E.BTN_TL: "lb", E.BTN_TR: "rb", E.BTN_TL2: "lt", E.BTN_TR2: "rt",
    E.BTN_SELECT: "select", E.BTN_START: "start", E.BTN_MODE: "guide",
    E.BTN_THUMBL: "ls", E.BTN_THUMBR: "rs",
    E.BTN_DPAD_UP: "up", E.BTN_DPAD_DOWN: "down", E.BTN_DPAD_LEFT: "left", E.BTN_DPAD_RIGHT: "right",
}
# xpad (Xbox and most PC pads) reports X/Y as BTN_NORTH/BTN_WEST; Sony's
# driver follows the kernel's positional naming, which is the other way round.
SONY = 0x054C
DIRS = ("up", "down", "left", "right")
OWN_DEVICES = ("Halcyon On-Screen Keyboard",)


class Gamepads(QObject):
    pressed = Signal(str)    # repeats while a direction (or anything in `repeat`) is held
    released = Signal(str)

    def __init__(self, parent=None, repeat=DIRS):
        super().__init__(parent)
        self.repeat = set(repeat)
        self.held = set()
        self.devs = {}           # path -> [InputDevice, QSocketNotifier, axis state]
        self.skip = set()        # paths that aren't gamepads (or can't be opened)
        self.grabbed = False
        self._last = {}          # name -> (time, device): drop doubles from Steam's virtual pad
        self._rep = QTimer(self, timeout=self._repeat)
        self._rep_name = None
        self._scan_timer = QTimer(self, interval=2000, timeout=self.scan)
        self._scan_timer.start()
        self.scan()

    # ---------------------------------------------------------- devices ---
    def scan(self):
        paths = set(evdev.list_devices())
        self.skip &= paths
        for p in list(self.devs):
            if p not in paths:
                self._drop(p)
        for p in paths - set(self.devs) - self.skip:
            try:
                d = evdev.InputDevice(p)
            except OSError:
                self.skip.add(p)
                continue
            keys = d.capabilities().get(E.EV_KEY, [])
            if d.name in OWN_DEVICES or not (E.BTN_SOUTH in keys or E.BTN_GAMEPAD in keys):
                d.close()
                self.skip.add(p)
                continue
            axes = {}
            for code in (E.ABS_X, E.ABS_Y, E.ABS_Z, E.ABS_RZ):
                try:
                    a = d.absinfo(code)
                    axes[code] = (a.min, a.max)
                except OSError:
                    pass
            n = QSocketNotifier(d.fd, QSocketNotifier.Read, self)
            n.activated.connect(lambda *_a, p=p: self._read(p))
            self.devs[p] = [d, n, axes, {}]
            if self.grabbed:
                self._grab(d, True)

    def _drop(self, p):
        d, n, *_ = self.devs.pop(p)
        n.setEnabled(False)
        n.deleteLater()
        try:
            d.close()
        except OSError:
            pass

    def _grab(self, d, on):
        try:
            d.grab() if on else d.ungrab()
        except OSError:
            pass

    def grab(self, on):
        """Take the pads exclusively (games stop seeing them) while on."""
        self.grabbed = on
        for d, *_ in self.devs.values():
            self._grab(d, on)

    @property
    def connected(self):
        return bool(self.devs)

    # ----------------------------------------------------------- events ---
    def _read(self, p):
        entry = self.devs.get(p)
        if not entry:
            return
        d, _n, axes, state = entry
        try:
            events = list(d.read())
        except BlockingIOError:
            return
        except OSError:                        # unplugged
            self._drop(p)
            return
        sony = d.info.vendor == SONY
        for ev in events:
            if ev.type == E.EV_KEY and ev.value in (0, 1):
                name = KEYS.get(ev.code)
                if sony and name in ("x", "y"):
                    name = "y" if name == "x" else "x"
                if name:
                    self._set(p, name, ev.value == 1)
            elif ev.type == E.EV_ABS:
                if ev.code == E.ABS_HAT0X:
                    self._set(p, "left", ev.value < 0)
                    self._set(p, "right", ev.value > 0)
                elif ev.code == E.ABS_HAT0Y:
                    self._set(p, "up", ev.value < 0)
                    self._set(p, "down", ev.value > 0)
                elif ev.code in axes:
                    lo, hi = axes[ev.code]
                    if ev.code in (E.ABS_X, E.ABS_Y):
                        v = (2 * (ev.value - lo) / ((hi - lo) or 1)) - 1      # -1 .. 1
                        neg, pos = ("left", "right") if ev.code == E.ABS_X else ("up", "down")
                        # Hysteresis so a stick resting near the edge doesn't chatter.
                        for name, val in ((neg, -v), (pos, v)):
                            key = ("stick", name)
                            if val > 0.6 and not state.get(key):
                                state[key] = True
                                self._set(p, name, True)
                            elif val < 0.35 and state.get(key):
                                state[key] = False
                                self._set(p, name, False)
                    else:                       # analog triggers
                        name = "lt" if ev.code == E.ABS_Z else "rt"
                        down = (ev.value - lo) / ((hi - lo) or 1) > 0.5
                        if down != bool(state.get(name)):
                            state[name] = down
                            self._set(p, name, down)

    def _set(self, dev, name, down):
        if down == (name in self.held):
            return
        if down:
            now = time.monotonic()
            t, other = self._last.get(name, (0, dev))
            self._last[name] = (now, dev)
            if other != dev and now - t < 0.04:
                return
            self.held.add(name)
            self.pressed.emit(name)
            if name in self.repeat:
                self._rep_name = name
                self._rep.start(380)
        else:
            self.held.discard(name)
            if self._rep_name == name:
                self._rep.stop()
                self._rep_name = None
            self.released.emit(name)

    def _repeat(self):
        if self._rep_name in self.held:
            self._rep.start(70 if self._rep_name in DIRS else 90)
            self.pressed.emit(self._rep_name)
        else:
            self._rep.stop()
