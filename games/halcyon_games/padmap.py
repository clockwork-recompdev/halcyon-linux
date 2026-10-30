"""Controller mapper, a la Steam Input: turns a gamepad into keyboard and
mouse for games that don't support controllers.

    python -m halcyon_games.padmap --layout action|mouse [--pid PID]

Grabs the gamepads (so the game doesn't also see them), types through a
virtual uinput keyboard/mouse, and exits when PID ends (or on SIGTERM).
Guide + Back toggles the mapping off and on mid-game."""
import argparse
import os
import select
import signal
import time

import evdev
from evdev import ecodes as E

from .gamepad import KEYS, OWN_DEVICES, SONY

NAME = "Halcyon Controller Mapper"

# Button -> key (or mouse button) per layout. Sticks are handled separately.
LAYOUTS = {
    "action": {   # shooters, action, platformers: WASD + mouse look
        "a": E.KEY_SPACE, "b": E.KEY_LEFTCTRL, "x": E.KEY_R, "y": E.KEY_E,
        "lb": E.KEY_Q, "rb": E.KEY_F, "lt": E.BTN_RIGHT, "rt": E.BTN_LEFT,
        "ls": E.KEY_LEFTSHIFT, "rs": E.KEY_V, "start": E.KEY_ESC, "select": E.KEY_TAB,
        "up": E.KEY_1, "down": E.KEY_2, "left": E.KEY_3, "right": E.KEY_4,
    },
    "mouse": {    # strategy, ARPG, point-and-click: stick moves the pointer
        "a": E.BTN_LEFT, "b": E.BTN_RIGHT, "x": E.BTN_MIDDLE, "y": E.KEY_SPACE,
        "lb": E.KEY_LEFTCTRL, "rb": E.KEY_LEFTSHIFT, "lt": E.BTN_RIGHT, "rt": E.BTN_LEFT,
        "ls": E.KEY_LEFTALT, "rs": E.KEY_TAB, "start": E.KEY_ESC, "select": E.KEY_TAB,
        "up": E.KEY_UP, "down": E.KEY_DOWN, "left": E.KEY_LEFT, "right": E.KEY_RIGHT,
    },
}
# What each layout does with the sticks (for the UI's description, too).
STICKS = {
    "action": {"left": "W A S D", "right": "mouse look"},
    "mouse": {"left": "move the pointer", "right": "arrow keys (scroll the map)"},
}
DEADZONE = 0.18


def describe(layout):
    names = {E.BTN_LEFT: "left click", E.BTN_RIGHT: "right click", E.BTN_MIDDLE: "middle click"}
    rows = [("left stick", STICKS[layout]["left"]), ("right stick", STICKS[layout]["right"])]
    for btn, code in LAYOUTS[layout].items():
        label = names.get(code) or E.KEY[code].replace("KEY_", "").replace("LEFT", "").title()
        rows.append((btn.upper() if len(btn) <= 2 else btn, label))
    return rows


def gamepads():
    out = []
    for p in evdev.list_devices():
        try:
            d = evdev.InputDevice(p)
        except OSError:
            continue
        keys = d.capabilities().get(E.EV_KEY, [])
        if d.name in OWN_DEVICES or d.name == NAME or not (E.BTN_SOUTH in keys or E.BTN_GAMEPAD in keys):
            d.close()
            continue
        out.append(d)
    return out


class Mapper:
    def __init__(self, layout):
        self.layout = layout
        self.map = LAYOUTS[layout]
        keys = sorted(set(self.map.values()) | {E.KEY_W, E.KEY_A, E.KEY_S, E.KEY_D, E.KEY_UP, E.KEY_DOWN,
                                                E.KEY_LEFT, E.KEY_RIGHT, E.BTN_LEFT, E.BTN_RIGHT, E.BTN_MIDDLE})
        self.ui = evdev.UInput({E.EV_KEY: keys, E.EV_REL: [E.REL_X, E.REL_Y, E.REL_WHEEL]}, name=NAME)
        self.devs = []
        self.axes = {}           # (dev fd, code) -> normalised -1..1
        self.ranges = {}
        self.down = set()        # synthetic keys currently held
        self.enabled = True
        self.held = set()        # pad buttons held (for Guide+Back)
        self.frac = [0.0, 0.0]   # sub-pixel mouse movement

    def attach(self):
        for d in gamepads():
            if d.path in {x.path for x in self.devs}:
                d.close()
                continue
            try:
                d.grab()                     # the game shouldn't also see the raw pad
            except OSError:
                pass
            for code, info in d.capabilities(absinfo=True).get(E.EV_ABS, []):
                self.ranges[(d.fd, code)] = (info.min, info.max)
            self.devs.append(d)

    def name_of(self, dev, code):
        name = KEYS.get(code)
        if dev.info.vendor == SONY and name in ("x", "y"):
            name = "y" if name == "x" else "x"
        return name

    def axis(self, dev, code, value):
        lo, hi = self.ranges.get((dev.fd, code), (-32768, 32767))
        if code in (E.ABS_Z, E.ABS_RZ):                     # triggers: 0..1
            v = (value - lo) / max(1, hi - lo)
        elif code in (E.ABS_HAT0X, E.ABS_HAT0Y):
            v = float(value)
        else:
            v = (2 * (value - lo) / max(1, hi - lo)) - 1
        self.axes[(dev.fd, code)] = v
        return v

    def stick(self, code):
        vals = [v for (fd, c), v in self.axes.items() if c == code]
        v = max(vals, key=abs) if vals else 0.0
        return 0.0 if abs(v) < DEADZONE else v

    def set_key(self, code, on):
        if on and code not in self.down:
            self.down.add(code)
            self.ui.write(E.EV_KEY, code, 1)
        elif not on and code in self.down:
            self.down.discard(code)
            self.ui.write(E.EV_KEY, code, 0)

    def release_all(self):
        for code in list(self.down):
            self.set_key(code, False)
        self.ui.syn()

    def button(self, name, on):
        if on:
            self.held.add(name)
        else:
            self.held.discard(name)
        if on and name in ("guide", "select") and {"guide", "select"} <= self.held:
            self.enabled = not self.enabled
            self.release_all()
            for d in self.devs:                 # off: give the pad back to the game
                try:
                    d.grab() if self.enabled else d.ungrab()
                except OSError:
                    pass
            return
        if self.enabled and name in self.map:
            self.set_key(self.map[name], on)

    def event(self, dev, ev):
        if ev.type == E.EV_KEY:
            name = self.name_of(dev, ev.code)
            if name:
                self.button(name, ev.value != 0)
        elif ev.type == E.EV_ABS:
            v = self.axis(dev, ev.code, ev.value)
            if ev.code in (E.ABS_Z, E.ABS_RZ):
                self.button("lt" if ev.code == E.ABS_Z else "rt", v > 0.5)
            elif ev.code == E.ABS_HAT0X:
                self.button("left", v < 0)
                self.button("right", v > 0)
            elif ev.code == E.ABS_HAT0Y:
                self.button("up", v < 0)
                self.button("down", v > 0)

    def tick(self, dt):
        """Sticks: held keys and mouse movement, every few milliseconds."""
        if not self.enabled:
            return
        lx, ly = self.stick(E.ABS_X), self.stick(E.ABS_Y)
        rx, ry = self.stick(E.ABS_RX), self.stick(E.ABS_RY)
        if self.layout == "action":
            self.set_key(E.KEY_W, ly < -0.45)
            self.set_key(E.KEY_S, ly > 0.45)
            self.set_key(E.KEY_A, lx < -0.45)
            self.set_key(E.KEY_D, lx > 0.45)
            mx, my, speed = rx, ry, 1400
        else:
            self.set_key(E.KEY_UP, ry < -0.5)
            self.set_key(E.KEY_DOWN, ry > 0.5)
            self.set_key(E.KEY_LEFT, rx < -0.5)
            self.set_key(E.KEY_RIGHT, rx > 0.5)
            mx, my, speed = lx, ly, 1100
        # Curved response: fine control near the centre, fast at the edge.
        for i, v in enumerate((mx, my)):
            self.frac[i] += (v * abs(v)) * speed * dt
        dx, dy = int(self.frac[0]), int(self.frac[1])
        self.frac[0] -= dx
        self.frac[1] -= dy
        if dx:
            self.ui.write(E.EV_REL, E.REL_X, dx)
        if dy:
            self.ui.write(E.EV_REL, E.REL_Y, dy)
        self.ui.syn()

    def alive(self, pid, prefix, started):
        """Is the game still running? Windows games: any program left in their Wine
        prefix (the launcher's start.exe exits at once). Others: the launched PID."""
        if time.monotonic() - started < 20:        # games take a while to start
            return True
        if prefix:
            want = f"WINEPREFIX={prefix}".encode()
            for d in os.listdir("/proc"):
                if not d.isdigit():
                    continue
                try:
                    with open(f"/proc/{d}/environ", "rb") as f:
                        if want in f.read():
                            with open(f"/proc/{d}/cmdline", "rb") as c:
                                cmd = c.read().lower()
                            if b".exe" in cmd and b"wineserver" not in cmd and b"services.exe" not in cmd \
                                    and b"explorer.exe" not in cmd and b"winedevice" not in cmd \
                                    and b"plugplay" not in cmd and b"svchost" not in cmd and b"rpcss" not in cmd:
                                return True
                except OSError:
                    continue
            return False
        return bool(pid) and os.path.exists(f"/proc/{pid}")

    def run(self, pid=None, prefix=None):
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        signal.signal(signal.SIGINT, lambda *_: stop.append(1))
        self.attach()
        last = time.monotonic()
        last_check = last
        started = last
        while not stop:
            fds = {d.fd: d for d in self.devs}
            r, _, _ = select.select(list(fds), [], [], 0.008)
            for fd in r:
                d = fds[fd]
                try:
                    for ev in d.read():
                        self.event(d, ev)
                except OSError:                 # unplugged
                    self.devs.remove(d)
            self.ui.syn()
            now = time.monotonic()
            self.tick(now - last)
            last = now
            if now - last_check > 1.5:
                last_check = now
                self.attach()                   # pads plugged in mid-game
                if (pid or prefix) and not self.alive(pid, prefix, started):
                    break
        self.release_all()
        for d in self.devs:
            try:
                d.ungrab()
            except OSError:
                pass
        self.ui.close()


def main():
    ap = argparse.ArgumentParser(prog="halcyon-padmap")
    ap.add_argument("--layout", choices=sorted(LAYOUTS), default="action")
    ap.add_argument("--pid", type=int, help="stop when this process ends")
    ap.add_argument("--prefix", help="Windows games: stop when nothing runs in this Wine prefix")
    a = ap.parse_args()
    Mapper(a.layout).run(a.pid, a.prefix)


if __name__ == "__main__":
    main()
