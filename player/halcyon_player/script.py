"""JScript engine for skins: the WMP skin object model on top of QJSEngine.

Skins are written against WMP's COM object model, which looked names up
case-insensitively. Here every scriptable thing (elements, view, player,
theme...) is a Python Api object; JavaScript sees each one through a Proxy
that forwards property reads/writes and method calls to `bridge`, so names
work in any case. Anything a skin uses that isn't implemented is logged to
~/.cache/halcyon-player/unsupported.log instead of breaking the skin.
"""
import json
import os
import re
import subprocess
import time

from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtQml import QJSEngine

from .skin import (ButtonElement, ButtonElementInGroup, ContainerElement,
                   CustomSliderElement, SliderElement, TextElement, ViewElement,
                   as_int, parse_color, truthy)

LOG = os.path.expanduser("~/.cache/halcyon-player/unsupported.log")
PREFS = os.path.expanduser("~/.config/halcyon-player/prefs")

PRELUDE = r"""
var __objs = {};
function __wrap(id) {
    if (__objs[id]) return __objs[id];
    var p = new Proxy(function () {}, {
        get: function (t, k) {
            if (typeof k !== 'string') return undefined;
            if (k === '__id') return id;
            if (k === 'toString' || k === 'valueOf') return function () { return bridge.str(id); };
            return __unwrap(bridge.get(id, k), id, k);
        },
        set: function (t, k, v) { bridge.set(id, k, (v && v.__id) ? {__ref: v.__id} : v); return true; },
        has: function (t, k) { return typeof k === 'string' && bridge.has(id, k); },
        apply: function (t, self, args) { return __unwrap(bridge.call(id, '', __args(args)), id, ''); }
    });
    __objs[id] = p;
    return p;
}
function __args(a) {
    var out = [];
    for (var i = 0; i < a.length; i++) out.push((a[i] && a[i].__id) ? {__ref: a[i].__id} : a[i]);
    return out;
}
function __unwrap(r, id, k) {
    if (r && typeof r === 'object') {
        if (r.__obj) return __wrap(r.__obj);
        if (r.__fn) return function () { return __unwrap(bridge.call(id, k, __args(arguments)), id, k); };
    }
    return r;
}
// What WMP's own helper scripts (res://wmploc.dll/RT_TEXT/...) gave skins:
// play/open state constants and a sprintf for strings.
var psUndefined = 0, psStopped = 1, psPaused = 2, psPlaying = 3, psScanForward = 4,
    psScanReverse = 5, psBuffering = 6, psWaiting = 7, psMediaEnded = 8,
    psTransitioning = 9, psReady = 10, psReconnecting = 11;
var osUndefined = 0, osPlaylistChanging = 1, osPlaylistLocating = 2, osPlaylistConnecting = 3,
    osPlaylistLoading = 4, osPlaylistOpening = 5, osPlaylistOpenNoMedia = 6,
    osPlaylistChanged = 7, osMediaChanging = 8, osMediaLocating = 9, osMediaConnecting = 10,
    osMediaLoading = 11, osMediaOpening = 12, osMediaOpen = 13, osBeginCodecAcquisition = 14,
    osEndCodecAcquisition = 15, osBeginLicenseAcquisition = 16, osEndLicenseAcquisition = 17,
    osBeginIndividualization = 18, osEndIndividualization = 19, osMediaWaiting = 20,
    osOpeningUnknownURL = 21;
String.prototype.sprintf = function (fmt) {
    var args = arguments, n = 1;
    return String(fmt).replace(/%(\d+)|%[sdif]|%%/g, function (m, idx) {
        if (m === '%%') return '%';
        return String(idx ? args[parseInt(idx, 10)] : args[n++]);
    });
};
// WMP's "event" object: the last key/mouse event's details.
var event = {keyCode: 0, x: 0, y: 0, offsetX: 0, offsetY: 0, button: 0,
             shiftKey: false, ctrlKey: false, altKey: false, returnValue: true, cancelBubble: false};
function __run(ctxId, viewId, code, newState) {
    var NewState = newState;
    // "view" means the event's own window, also inside the skin's global
    // functions (Halo 2's introStart() sets view.timerInterval = 0 to stop its
    // intro; with the wrong view that hit another window and the intro looped).
    var saved = view;
    view = __wrap(viewId);
    try {
        with (__wrap(ctxId)) { return eval(code); }
    } finally {
        view = saved;
    }
}
"""


_INDEXED_PUT = re.compile(r"(\b[A-Za-z_$][\w$.]*)\.(\w+)\(([^()]*)\)\s*=(?!=)\s*([^;\n]+)")


# Strings and comments are matched first so their contents are left alone.
_OCTAL = re.compile(r"""("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|//[^\n]*|/\*.*?\*/)|(?<![\w.$])0(\d+)(?![\w.])""", re.S)


def _octal(m):
    if m.group(1) is not None:
        return m.group(1)
    digits = m.group(2)
    # JScript: 0-prefixed with only 0-7 is octal (010 == 8); with 8/9 it's decimal.
    return str(int(digits, 8) if set(digits) <= set("01234567") else int(digits))


def jscript_compat(code):
    """Rewrite JScript-only syntax that modern JavaScript rejects:
    - legacy octal literals (00, 010), which Qt's engine refuses;
    - assigning to a COM indexed property, e.g. eq.gainLevels(3) = 5,
      which becomes a two-argument call."""
    code = _OCTAL.sub(_octal, code)
    return _INDEXED_PUT.sub(lambda m: f"{m.group(1)}.{m.group(2)}({m.group(3)}, {m.group(4)})", code)


def strip_prefix(code):
    c = (code or "").strip()
    if c.lower().startswith("jscript:"):
        c = c[8:]
    return jscript_compat(c)


def log_unsupported(what):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {what}\n")
    except OSError:
        pass


def fmt_time(sec):
    sec = int(max(0, sec or 0))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ===================================================================== Api ===
class Api:
    """Base for scriptable objects. Subclasses list `methods` and implement
    get_<name>/set_<name>/call_<name> in lower case."""

    methods = ()

    def __init__(self, host):
        self.host = host
        self.extra = {}
        self.id = host.register(self)

    def label(self):
        return type(self).__name__

    def has(self, name):
        n = name.lower()
        return n in self.methods or hasattr(self, "get_" + n) or hasattr(self, "call_" + n) or n in self.extra

    def get(self, name):
        n = name.lower()
        if n in self.methods or (hasattr(self, "call_" + n) and not hasattr(self, "get_" + n)):
            return {"__fn": True}
        g = getattr(self, "get_" + n, None)
        if g is not None:
            return g()
        if n in self.extra:
            return self.extra[n]
        log_unsupported(f"get {self.label()}.{name}")
        return None

    def set(self, name, value):
        n = name.lower()
        s = getattr(self, "set_" + n, None)
        if s is not None:
            s(value)
        else:
            self.extra[n] = value

    def call(self, name, args):
        f = getattr(self, "call_" + name.lower(), None)
        if f is None:
            log_unsupported(f"call {self.label()}.{name}()")
            return None
        return f(*args)


class Store(Api):
    """Free-form object: whatever the skin sets, it can read back."""

    def __init__(self, host, name, defaults=None, methods=()):
        super().__init__(host)
        self.name = name
        self.extra.update({k.lower(): v for k, v in (defaults or {}).items()})
        self.methods = tuple(m.lower() for m in methods)

    def label(self):
        return self.name

    def call(self, name, args):
        if name.lower() in self.methods:
            return None
        return super().call(name, args)


# ---------------------------------------------------------------- elements ---
class ElementApi(Api):
    methods = ("moveto", "alphablendto", "resizeto", "slideto")

    def __init__(self, host, el, window):
        self.el, self.window = el, window
        super().__init__(host)

    def label(self):
        return f"{self.el.node.tag}#{self.el.id}"

    def has(self, name):
        return super().has(name) or name.lower() in self.el.node.attrs

    def get(self, name):
        n = name.lower()
        if n not in self.methods and not hasattr(self, "get_" + n) and n not in self.extra \
                and n in self.el.node.attrs:
            return self.el.node.attrs[n]
        return super().get(name)

    def changed(self, prop=None):
        self.window.refresh_soon()
        if prop:
            self.host.fire(self.el, prop + "_onchange")

    # geometry / look
    def get_left(self): return self.el.left
    def set_left(self, v): self.el.left = as_int(v); self.changed("left")
    def get_top(self): return self.el.top
    def set_top(self, v): self.el.top = as_int(v); self.changed("top")
    def get_width(self): return self.el.size().width()
    def set_width(self, v): self.el.resize(w=as_int(v)); self.changed("width")
    def get_height(self): return self.el.size().height()
    def set_height(self, v): self.el.resize(h=as_int(v)); self.changed("height")
    def get_visible(self): return self.el.visible
    def set_visible(self, v): self.el.visible = truthy(v); self.changed("visible")
    def get_enabled(self): return self.el.enabled
    def set_enabled(self, v): self.el.enabled = truthy(v); self.changed("enabled")
    def get_alphablend(self): return self.el.alpha
    def set_alphablend(self, v): self.el.alpha = as_int(v, 255); self.changed("alphaBlend")
    def get_zindex(self): return self.el.z
    def set_zindex(self, v): self.el.z = as_int(v); self.changed("zIndex")
    def get_id(self): return self.el.id
    def get_tooltip(self): return self.el.tooltip
    def set_tooltip(self, v): self.el.tooltip = str(v)
    get_uptooltip, set_uptooltip = get_tooltip, set_tooltip

    def _img(self, key):
        if isinstance(self.el, ButtonElement):
            return self.el.images.get(key)
        if isinstance(self.el, ButtonElementInGroup):
            return self.el.own.get(key)
        return self.el.node.get(key + "image")

    def _set_img(self, key, v):
        if isinstance(self.el, ButtonElement):
            self.el.images[key] = v
        elif isinstance(self.el, ButtonElementInGroup):
            self.el.own[key] = v
        elif key == "" and hasattr(self.el, "image_name"):
            self.el.image_name = v
        elif key == "" and isinstance(self.el, CustomSliderElement):
            self.el.strip = v
        self.changed()

    def get_image(self): return self._img("") or getattr(self.el, "image_name", None)
    def set_image(self, v): self._set_img("", v)
    def get_hoverimage(self): return self._img("hover")
    def set_hoverimage(self, v): self._set_img("hover", v)
    def get_downimage(self): return self._img("down")
    def set_downimage(self, v): self._set_img("down", v)
    def get_disabledimage(self): return self._img("disabled")
    def set_disabledimage(self, v): self._set_img("disabled", v)

    def get_backgroundimage(self): return getattr(self.el, "background", None)
    def set_backgroundimage(self, v):
        if isinstance(self.el, ContainerElement):
            self.el.background = v
            self.el._movie = self.el.skin.movie(v) if v else None
            if self.el._movie:
                self.el._movie.frameChanged.connect(self.window.refresh_soon)
                self.el._movie.start()
        self.changed("backgroundImage")

    def get_down(self): return self.el.down
    def set_down(self, v): self.el.down = truthy(v, False); self.changed("down")

    # TEXT / SLIDER values
    def get_value(self):
        if isinstance(self.el, TextElement):
            return self.el.text
        if isinstance(self.el, SliderElement):
            return self.el.value
        return self.extra.get("value")

    def set_value(self, v):
        if isinstance(self.el, TextElement):
            self.el.text = "" if v is None else str(v)
        elif isinstance(self.el, SliderElement):
            try:
                self.el.value = float(v)
            except (TypeError, ValueError):
                return
        else:
            self.extra["value"] = v
        self.changed("value")

    def get_textwidth(self):
        if isinstance(self.el, TextElement):
            from PySide6.QtGui import QFontMetrics
            return QFontMetrics(self.el.font).horizontalAdvance(self.el.text)
        return 0

    def get_min(self): return getattr(self.el, "minimum", 0)
    def set_min(self, v): self.el.minimum = float(v); self.changed()
    def get_max(self): return getattr(self.el, "maximum", 100)
    def set_max(self, v): self.el.maximum = float(v); self.changed()

    def get_foregroundcolor(self):
        c = getattr(self.el, "color", None)
        return c.name() if hasattr(c, "name") else c

    def set_foregroundcolor(self, v):
        c = parse_color(str(v))
        if c is not None:
            self.el.color = c
            self.changed()

    # animations
    def call_moveto(self, x, y, ms=0):
        self.host.animate(self, {"left": as_int(x), "top": as_int(y)}, as_int(ms))

    def call_slideto(self, x, y, ms=0):
        self.call_moveto(x, y, ms)

    def call_resizeto(self, w, h, ms=0):
        self.host.animate(self, {"width": as_int(w), "height": as_int(h)}, as_int(ms))

    def call_alphablendto(self, a, ms=0):
        self.host.animate(self, {"alphablend": as_int(a, 255)}, as_int(ms))


WMP_EQ_BANDS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
WMP_EQ_PRESETS = [
    ("Default", [0] * 10),
    ("Rock", [5, 3, -2, -4, -2, 2, 5, 7, 7, 7]),
    ("Pop", [-2, 2, 4, 5, 4, 0, -2, -2, -2, -2]),
    ("Dance", [8, 6, 2, 0, 0, -4, -5, -5, 0, 0]),
    ("Jazz", [3, 2, 1, 2, -2, -2, 0, 1, 3, 4]),
    ("Classical", [0, 0, 0, 0, 0, 0, -6, -6, -6, -8]),
    ("Rap", [6, 5, 1, -2, -2, 1, 3, 2, 3, 4]),
    ("Metal", [6, 4, 0, -4, -2, 2, 6, 6, 6, 5]),
    ("Acoustic", [4, 3, 2, 0, 1, 2, 3, 3, 2, 1]),
    ("Speech", [-4, -2, 0, 4, 6, 6, 4, 2, 0, -4]),
    ("Bass Boost", [9, 7, 5, 2, 0, 0, 0, 0, 0, 0]),
]


class EqualizerApi(ElementApi):
    """EQUALIZERSETTINGS: a real 10-band equalizer (WMP's bands, +/-14 dB)
    applied through mpv's audio filters."""

    methods = ElementApi.methods + ("nextpreset", "previouspreset", "reset", "gainlevels",
                                    "getpresettitle")

    def __init__(self, host, el, window):
        super().__init__(host, el, window)
        self.gains = list(host.eq_state["gains"])
        self.preset = host.eq_state["preset"]
        self._pending = False

    def _apply(self):
        self.host.eq_state.update(gains=list(self.gains), preset=self.preset)
        if self._pending:
            return
        self._pending = True
        def go():
            self._pending = False
            g = [max(-14.0, min(14.0, float(x))) for x in self.gains]
            chain = ",".join(f"equalizer=f={f}:t=o:w=1:g={v:.1f}" for f, v in zip(WMP_EQ_BANDS, g) if abs(v) > 0.05)
            self.host.player.set_eq(chain if self.host.eq_state.get("enabled", True) else "")
            self.window.refresh_soon()
        QTimer.singleShot(80, go)   # sliders fire many changes; apply once

    def get(self, name):
        n = name.lower()
        if n.startswith("gainlevel") and n[9:].isdigit():
            i = int(n[9:]) - 1
            return self.gains[i] if 0 <= i < 10 else 0
        return super().get(name)

    def set(self, name, value):
        n = name.lower()
        if n.startswith("gainlevel") and n[9:].isdigit():
            i = int(n[9:]) - 1
            if 0 <= i < 10:
                self.gains[i] = float(value or 0)
                self.preset = -1
                self._apply()
            return
        super().set(name, value)

    def call_gainlevels(self, i, value=None):
        i = as_int(i)
        if value is None:
            return self.gains[i] if 0 <= i < 10 else 0
        self.set(f"gainLevel{i + 1}", value)

    def _use(self, idx):
        self.preset = idx % len(WMP_EQ_PRESETS)
        self.gains = list(map(float, WMP_EQ_PRESETS[self.preset][1]))
        self._apply()

    def call_nextpreset(self): self._use(max(self.preset, 0) + 1)
    def call_previouspreset(self): self._use(max(self.preset, 0) - 1)
    def call_reset(self): self._use(0)
    def call_getpresettitle(self, i): return WMP_EQ_PRESETS[as_int(i) % len(WMP_EQ_PRESETS)][0]
    def get_currentpreset(self): return max(self.preset, 0)
    def set_currentpreset(self, v): self._use(as_int(v))
    def get_currentpresettitle(self): return "Custom" if self.preset < 0 else WMP_EQ_PRESETS[self.preset][0]
    def get_presetcount(self): return len(WMP_EQ_PRESETS)
    def get_bypass(self): return not self.host.eq_state.get("enabled", True)
    def set_bypass(self, v): self.host.eq_state["enabled"] = not truthy(v, False); self._apply()
    def get_enabled(self): return self.host.eq_state.get("enabled", True)
    def set_enabled(self, v): self.host.eq_state["enabled"] = truthy(v); self._apply()
    def get_enhancedaudio(self): return False
    def set_enhancedaudio(self, v): pass
    def get_splinetension(self): return 0
    def set_splinetension(self, v): pass
    def get_enablesplinetension(self): return False
    def set_enablesplinetension(self, v): pass
    def get_truebass(self): return 0
    def set_truebass(self, v): pass
    def get_wowlevel(self): return 0
    def set_wowlevel(self, v): pass
    def get_speakersize(self): return 0
    def set_speakersize(self, v): pass
    def get_normalization(self): return False
    def set_normalization(self, v): pass


class EffectsApi(ElementApi):
    """EFFECTS (visualizations): accepted and remembered; drawing comes later."""

    methods = ElementApi.methods + ("next", "previous", "nextpreset", "previouspreset",
                                    "nexteffect", "previouseffect", "settings", "fullscreen",
                                    "geteffecttitle", "geteffecttype", "getpresettitle")

    def _styles(self):
        from .vis import STYLES
        return STYLES

    def _step(self, d):
        self.el.vis_style = (getattr(self.el, "vis_style", 0) + d) % len(self._styles())
        self.changed()

    def get_currenteffecttype(self): return self._styles()[getattr(self.el, "vis_style", 0) % len(self._styles())]
    def set_currenteffecttype(self, v):
        if str(v) in self._styles():
            self.el.vis_style = self._styles().index(str(v))
    def get_currenteffecttitle(self): return self.get_currenteffecttype()
    def get_currentpreset(self): return self.extra.get("preset", 0)
    def set_currentpreset(self, v): self.extra["preset"] = as_int(v)
    def get_currentpresettitle(self): return "Default"
    def get_effectcount(self): return len(self._styles())
    def get_presetcount(self): return 1
    def get_currenteffect(self): return 0
    def set_currenteffect(self, v): pass
    def get_allowall(self): return True
    def set_allowall(self, v): pass
    def call_next(self): self._step(1)
    def call_previous(self): self._step(-1)
    def call_nextpreset(self): self._step(1)
    def call_previouspreset(self): self._step(-1)
    def call_nexteffect(self): self._step(1)
    def call_previouseffect(self): self._step(-1)
    def call_settings(self): pass
    def call_fullscreen(self): pass
    def call_geteffecttitle(self, i=0): return self._styles()[as_int(i) % len(self._styles())]
    def call_geteffecttype(self, i=0): return self.call_geteffecttitle(i)
    def call_getpresettitle(self, *_): return "Default"


class VideoSettingsApi(ElementApi):
    """VIDEOSETTINGS: brightness/contrast/hue/saturation map to mpv (-100..100)."""

    def _prop(self, n):
        try:
            return int(self.host.player.mpv[n] or 0)
        except Exception:
            return 0

    def _put(self, n, v):
        try:
            self.host.player.mpv[n] = max(-100, min(100, as_int(v)))
        except Exception:
            pass

    def get_brightness(self): return self._prop("brightness")
    def set_brightness(self, v): self._put("brightness", v)
    def get_contrast(self): return self._prop("contrast")
    def set_contrast(self, v): self._put("contrast", v)
    def get_hue(self): return self._prop("hue")
    def set_hue(self, v): self._put("hue", v)
    def get_saturation(self): return self._prop("saturation")
    def set_saturation(self, v): self._put("saturation", v)


class ListElementApi(ElementApi):
    """PLAYLIST / LISTBOX / CURRENTPLAYLIST controls: there's no list view yet,
    so their setup calls (setColumnResizeMode, sort, ...) quietly succeed."""

    VERBS = ("set", "get", "select", "sort", "delete", "move", "insert", "add", "remove", "clear",
             "ensure", "scroll", "begin", "end", "refresh", "show", "hide")

    def get(self, name):
        n = name.lower()
        if n.startswith(self.VERBS) and not hasattr(self, "get_" + n) and n not in self.el.node.attrs:
            return {"__fn": True}
        return super().get(name)

    def call(self, name, args):
        f = getattr(self, "call_" + name.lower(), None)
        return f(*args) if f else None

    def _box(self):
        return self.el.node.tag == "LISTBOX"

    def get_itemcount(self):
        return len(self.el.items) if self._box() else self.host.player_api.playlist.get_count()
    def get_selectedcount(self): return 1 if self.el.selected >= 0 else 0
    def get_columncount(self): return 1
    def get_selectedindex(self): return self.el.selected
    def set_selectedindex(self, v): self.el.selected = as_int(v, -1); self.changed("selectedIndex")
    def get_selecteditem(self): return self.el.selected
    def set_selecteditem(self, v): self.set_selectedindex(v)

    # LISTBOX contents, as skins script them
    def call_additem(self, text, *_):
        self.el.items.append("" if text is None else str(text)); self.changed()
        return len(self.el.items) - 1
    call_appenditem = call_additem

    def call_insertitem(self, i, text, *_):
        self.el.items.insert(as_int(i), str(text)); self.changed()
    def call_deleteitem(self, i):
        i = as_int(i)
        if 0 <= i < len(self.el.items):
            del self.el.items[i]; self.changed()
    def call_deleteall(self): self.el.items.clear(); self.changed()
    def call_getitem(self, i):
        i = as_int(i)
        return self.el.items[i] if 0 <= i < len(self.el.items) else ""
    def call_finditem(self, start, text):
        try:
            return self.el.items.index(str(text), as_int(start))
        except ValueError:
            return -1


class ViewApi(ElementApi):
    methods = ElementApi.methods + ("close", "minimize", "maximize", "restore", "returntomediacenter",
                                    "size", "setmousecapture", "releasemousecapture")

    def get_timerinterval(self): return self.window.timer_ms
    def set_timerinterval(self, v): self.window.set_timer(as_int(v))
    def get_title(self): return self.window.windowTitle()
    def set_title(self, v): self.window.setWindowTitle(str(v))
    def get_focusobjectid(self): return self.extra.get("focusobjectid", "")
    def set_focusobjectid(self, v): self.extra["focusobjectid"] = v; self.host.fire(self.el, "focusObjectID_onchange")
    def get_minwidth(self): return as_int(self.el.node.get("minwidth"), 0)
    def get_minheight(self): return as_int(self.el.node.get("minheight"), 0)

    def set_width(self, v):
        super().set_width(v)
        self.window.resize(self.el.size())

    def set_height(self, v):
        super().set_height(v)
        self.window.resize(self.el.size())

    def call_close(self): self.host.close_view(self.window)
    def call_minimize(self): self.window.showMinimized()
    def call_maximize(self): pass
    def call_restore(self): self.window.showNormal()
    def call_returntomediacenter(self): self.host.close_view(self.window)
    def call_size(self, *_): pass
    def call_setmousecapture(self, *_): pass
    def call_releasemousecapture(self, *_): pass


# ------------------------------------------------------------------ player ---
WMP_PLAYSTATE = {"undefined": 0, "stopped": 1, "paused": 2, "playing": 3, "ended": 8, "ready": 10}


class MediaApi(Api):
    methods = ("getiteminfo", "setiteminfo", "getattributename", "ismemberof", "isreadonlyitem")

    def __init__(self, host, entry=None):
        self.entry = entry          # None = the current media
        super().__init__(host)

    def label(self):
        return "currentMedia"

    def _mpv(self):
        return self.host.player.mpv

    def _meta(self):
        try:
            return {k.lower(): v for k, v in (self._mpv().metadata or {}).items()}
        except Exception:
            return {}

    def get_name(self):
        if self.entry is not None:
            return self.entry.get("title") or os.path.basename(self.entry.get("filename", ""))
        return self.host.player.title

    def get_sourceurl(self):
        return (self.entry or {}).get("filename") or self._mpv().path or ""

    def get_duration(self):
        return float(self._mpv().duration or 0) if self.entry is None else 0.0

    def get_durationstring(self):
        return fmt_time(self.get_duration())

    def get_imagesourcewidth(self):
        return int(self._mpv().width or 0)

    def get_imagesourceheight(self):
        return int(self._mpv().height or 0)

    def get_attributecount(self):
        return len(self._meta())

    def call_getiteminfo(self, attr):
        a = str(attr).lower()
        m = self._meta()
        aliases = {"title": ("title",), "author": ("artist", "album_artist"), "artist": ("artist",),
                   "wm/albumtitle": ("album",), "album": ("album",), "wm/genre": ("genre",),
                   "wm/year": ("date", "year"), "wm/tracknumber": ("track",), "name": ("title",)}
        for key in aliases.get(a, (a,)):
            if key in m:
                return m[key]
        if a in ("title", "name"):
            return self.get_name()
        if a == "duration":
            return self.get_duration()
        if a == "sourceurl":
            return self.get_sourceurl()
        return ""

    def call_setiteminfo(self, *_): return None
    def call_getattributename(self, i): return list(self._meta())[as_int(i)] if self._meta() else ""
    def call_ismemberof(self, *_): return False
    def call_isreadonlyitem(self, *_): return True


class PlaylistApi(Api):
    methods = ("item", "appenditem", "insertitem", "removeitem", "clear", "getiteminfo", "moveitem")

    def label(self):
        return "playlist"

    def _items(self):
        try:
            return self.host.player.mpv.playlist or []
        except Exception:
            return []

    def get_count(self): return len(self._items())
    def get_name(self): return self.extra.get("name", "Now Playing")
    def set_name(self, v): self.extra["name"] = v
    def call_item(self, i):
        items = self._items()
        i = as_int(i)
        return {"__obj": MediaApi(self.host, items[i]).id} if 0 <= i < len(items) else None
    def call_appenditem(self, media):
        url = media.get_sourceurl() if isinstance(media, MediaApi) else str(media)
        self.host.player.open([url], append=True)
    def call_insertitem(self, i, media): self.call_appenditem(media)
    def call_removeitem(self, *_): return None
    def call_clear(self): self.host.player.mpv.command("playlist-clear")
    def call_getiteminfo(self, *_): return ""
    def call_moveitem(self, *_): return None


class EmptyCollection(Api):
    """mediaCollection / playlistCollection / cdromCollection: Halcyon has no
    WMP library, so these answer politely with nothing."""

    methods = ("getall", "getbyname", "getbyattribute", "getbyauthor", "getbygenre",
               "getbyalbum", "getmediaatom", "getall", "item", "getattributestringcollection",
               "newplaylist", "add", "remove", "getbydrivespecifier")

    def __init__(self, host, name):
        self.name = name
        super().__init__(host)

    def label(self):
        return self.name

    def get_count(self): return 0

    def call(self, name, args):
        n = name.lower()
        if n == "getmediaatom":
            return 0
        if n in self.methods:
            return {"__obj": PlaylistApi(self.host).id}
        return super().call(name, args)


class ControlsApi(Api):
    methods = ("play", "pause", "stop", "next", "previous", "fastforward", "fastreverse",
               "isavailable", "playitem", "step")

    def label(self): return "controls"
    @property
    def p(self): return self.host.player

    def call_play(self): self.p.play()
    def call_pause(self): self.p.pause()
    def call_stop(self): self.p.stop()
    def call_next(self): self.p.next()
    def call_previous(self): self.p.previous()
    def call_fastforward(self): self.p.mpv.speed = 2.0
    def call_fastreverse(self): self.p.mpv.seek(-10)
    def call_step(self, *_): self.p.mpv.command("frame-step")
    def call_playitem(self, media):
        if isinstance(media, MediaApi) and media.entry is not None:
            self.p.open([media.get_sourceurl()])

    def call_isavailable(self, what):
        st = self.host.play_state()
        w = str(what).lower()
        loaded = st in (2, 3)
        return {"play": st != 3, "pause": st == 3, "stop": loaded,
                "next": self.p.mpv.playlist_count > 1, "previous": self.p.mpv.playlist_count > 1,
                "fastforward": st == 3, "fastreverse": st == 3, "currentposition": loaded,
                "step": st == 2}.get(w, loaded)

    def get_currentposition(self): return float(self.p.mpv.time_pos or 0)
    def set_currentposition(self, v):
        try:
            self.p.mpv.seek(float(v), "absolute")
        except Exception:
            pass
    def get_currentpositionstring(self): return fmt_time(self.p.mpv.time_pos)
    def get_currentitem(self): return {"__obj": MediaApi(self.host).id}


class SettingsApi(Api):
    methods = ("getmode", "setmode", "isavailable", "requestmediaaccessrights")

    def label(self): return "settings"
    @property
    def p(self): return self.host.player

    def get_volume(self): return int(self.p.mpv.volume or 0)
    def set_volume(self, v): self.p.mpv.volume = max(0, min(100, as_int(v)))
    def get_mute(self): return bool(self.p.mpv.mute)
    def set_mute(self, v): self.p.mpv.mute = truthy(v, False)
    def get_balance(self): return self.extra.get("balance", 0)
    def set_balance(self, v): self.extra["balance"] = as_int(v)
    def get_rate(self): return float(self.p.mpv.speed or 1)
    def set_rate(self, v): self.p.mpv.speed = float(v)
    def get_autostart(self): return True
    def set_autostart(self, v): pass
    def call_getmode(self, m): return self.p.get_mode(str(m))
    def call_setmode(self, m, on):
        self.p.set_mode(str(m), truthy(on, False))
        self.host.fire_player("modechange", m)
    def call_isavailable(self, *_): return True
    def call_requestmediaaccessrights(self, *_): return True


class PlayerApi(Api):
    methods = ("launchurl", "newplaylist", "newmedia", "close", "openplayer")

    def __init__(self, host):
        super().__init__(host)
        self.controls = ControlsApi(host)
        self.settings = SettingsApi(host)
        self.playlist = PlaylistApi(host)
        self.media_collection = EmptyCollection(host, "mediaCollection")
        self.playlist_collection = EmptyCollection(host, "playlistCollection")
        self.cdrom_collection = EmptyCollection(host, "cdromCollection")
        self.network = Store(host, "network", {"bandWidth": 0, "bitRate": 0, "bufferingProgress": 100,
                                              "downloadProgress": 100, "frameRate": 0, "lostPackets": 0})
        self.error = Store(host, "error", {"errorCount": 0}, methods=("clearErrorQueue", "webHelp"))
        self.closed_caption = Store(host, "closedCaption", {"SAMIFileName": "", "captioningID": ""})
        self.dvd = Store(host, "dvd", {"domain": ""}, methods=("topMenu", "titleMenu", "back", "resume"))

    def label(self): return "player"
    def get_controls(self): return {"__obj": self.controls.id}
    def get_settings(self): return {"__obj": self.settings.id}
    def get_currentmedia(self): return {"__obj": MediaApi(self.host).id}
    def get_currentplaylist(self): return {"__obj": self.playlist.id}
    def get_mediacollection(self): return {"__obj": self.media_collection.id}
    def get_playlistcollection(self): return {"__obj": self.playlist_collection.id}
    def get_cdromcollection(self): return {"__obj": self.cdrom_collection.id}
    def get_network(self): return {"__obj": self.network.id}
    def get_error(self): return {"__obj": self.error.id}
    def get_closedcaption(self): return {"__obj": self.closed_caption.id}
    def get_dvd(self): return {"__obj": self.dvd.id}
    def get_playstate(self): return self.host.play_state()
    def get_openstate(self): return 13 if self.host.player.mpv.path else 0
    def get_status(self):
        return {1: "Stopped", 2: "Paused", 3: "Playing", 10: "Ready"}.get(self.host.play_state(), "Ready")
    def get_url(self): return self.host.player.mpv.path or ""
    def set_url(self, v):
        if v:
            self.host.player.open([str(v)])
    def get_versioninfo(self): return "12.0.7601.17514"   # new enough for every skin's version check
    def get_fullscreen(self): return False
    def set_fullscreen(self, v): pass
    def get_enabled(self): return True
    def get_uimode(self): return "custom"
    def get_isonline(self): return True
    def call_launchurl(self, url):
        subprocess.Popen(["xdg-open", str(url)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def call_newplaylist(self, *_): return {"__obj": PlaylistApi(self.host).id}
    def call_newmedia(self, url):
        return {"__obj": MediaApi(self.host, {"filename": str(url)}).id}
    def call_close(self): self.host.app_quit()
    def call_openplayer(self, *_): return None


# ------------------------------------------------------------------- theme ---
class ThemeApi(Api):
    methods = ("savepreference", "loadpreference", "openview", "closeview", "openviewrelative",
               "playsound", "loadstring", "opendialog", "showerrordialog", "logstring", "messagebox")

    def __init__(self, host):
        super().__init__(host)
        self.prefs_file = os.path.join(PREFS, f"{host.skin.name}.json")
        try:
            with open(self.prefs_file) as f:
                self.prefs = json.load(f)
        except (OSError, ValueError):
            self.prefs = {}

    def label(self): return "theme"
    def get_title(self): return self.host.skin.theme.get("title") or self.host.skin.name
    def get_author(self): return self.host.skin.theme.get("author") or ""
    def get_currentviewid(self): return self.host.current_view_id()
    def set_currentviewid(self, v): self.host.open_view(str(v), replace=True)

    def call_savepreference(self, key, value):
        self.prefs[str(key).lower()] = "" if value is None else str(value)
        os.makedirs(PREFS, exist_ok=True)
        with open(self.prefs_file, "w") as f:
            json.dump(self.prefs, f)

    def call_loadpreference(self, key):
        return self.prefs.get(str(key).lower(), "--")   # WMP's "not set"

    def call_openview(self, vid): self.host.open_view(str(vid))
    def call_openviewrelative(self, vid, x=0, y=0): self.host.open_view(str(vid), offset=(as_int(x), as_int(y)))
    def call_closeview(self, vid): self.host.close_view_id(str(vid))
    def call_playsound(self, *_): return None
    def call_loadstring(self, *_): return ""
    def call_showerrordialog(self, *_): return None
    def call_logstring(self, s): print("skin:", s)
    def call_messagebox(self, text, *_):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, self.get_title(), str(text))

    def call_opendialog(self, kind="FILE_OPEN", filt="FILES_ALL"):
        from PySide6.QtWidgets import QFileDialog
        if "FOLDER" in str(kind).upper():
            return QFileDialog.getExistingDirectory(None, "Choose a folder") or ""
        path, _ = QFileDialog.getOpenFileName(None, "Open", os.path.expanduser("~/Music"))
        return path or ""


# ======================================================== bridge and host ===
class Bridge(QObject):
    def __init__(self, host):
        super().__init__()
        self.host = host

    def _obj(self, oid):
        return self.host.objects.get(oid)

    def _in(self, v):
        if isinstance(v, dict) and "__ref" in v:
            return self.host.objects.get(v["__ref"])
        if isinstance(v, list):
            return [self._in(x) for x in v]
        return v

    @Slot(str, str, result="QVariant")
    def get(self, oid, name):
        o = self._obj(oid)
        return None if o is None else o.get(name)

    @Slot(str, str, "QVariant")
    def set(self, oid, name, value):
        o = self._obj(oid)
        if o is not None:
            o.set(name, self._in(value))

    @Slot(str, str, result=bool)
    def has(self, oid, name):
        o = self._obj(oid)
        return bool(o and o.has(name))

    @Slot(str, str, "QVariantList", result="QVariant")
    def call(self, oid, name, args):
        o = self._obj(oid)
        if o is None:
            return None
        try:
            return o.call(name, [self._in(a) for a in (args or [])])
        except TypeError as e:
            log_unsupported(f"bad call {o.label()}.{name}{tuple(args or [])}: {e}")
            return None

    @Slot(str, result=str)
    def str(self, oid):
        o = self._obj(oid)
        return f"[object {o.label() if o else '?'}]"


class ScriptHost:
    """One per open skin: the JS engine, every scriptable object, events,
    bindings and animations. The app supplies window create/close hooks."""

    def __init__(self, skin, player, open_window, close_window, quit_app):
        self.skin, self.player = skin, player
        self._open_window, self._close_window, self.app_quit = open_window, close_window, quit_app
        self.objects = {}
        self._next = 0
        self.windows = {}          # view id (lower) -> SkinWindow
        self.engine = QJSEngine()
        self.engine.installExtensions(QJSEngine.ConsoleExtension)
        self.bridge = Bridge(self)
        self.engine.globalObject().setProperty("bridge", self.engine.newQObject(self.bridge))
        self.eval(PRELUDE, "prelude")
        self.player_api = PlayerApi(self)
        self.theme_api = ThemeApi(self)
        self.mediacenter = Store(self, "mediaCenter", {"videoZoom": 100, "videoStretchToFit": True,
                                                      "effectType": "", "effectPreset": 0})
        for name, api in (("player", self.player_api), ("theme", self.theme_api),
                          ("mediaCenter", self.mediacenter), ("mediacenter", self.mediacenter)):
            self.eval(f"var {name} = __wrap('{api.id}');", "globals")
        self.bindings = []       # (window, api, prop, expr, kind)
        self.anims = []
        self.anim_timer = QTimer(interval=16, timeout=self._step_anims)
        self.last = {"playstate": None, "media": None, "mode": None, "video": False, "status": None,
                     "playlist": None}
        self.video = None
        self.loaded_scripts = set()
        self.eq_state = {"gains": [0.0] * 10, "preset": 0, "enabled": True}

    # ---- registry
    def register(self, api):
        self._next += 1
        oid = f"o{self._next}"
        self.objects[oid] = api
        return oid

    def eval(self, code, where="skin"):
        r = self.engine.evaluate(code, where)
        if r.isError():
            line = r.property("lineNumber").toInt()
            src = code.splitlines()[line - 1].strip()[:120] if 0 < line <= len(code.splitlines()) else ""
            log_unsupported(f"error in {where}:{line}: {r.toString()}  | {src}")
        return r

    # ---- views
    def attach(self, window):
        """Make a freshly created window's elements scriptable."""
        view = window.view
        vid = (view.id or "view").lower()
        self.windows[vid] = window
        window.api = ViewApi(self, view, window)
        self.eval(f"var view = __wrap('{window.api.id}');", "view")
        if view.id:
            self.eval(f"var {view.id} = view;", "view")
        kinds = {"EQUALIZERSETTINGS": EqualizerApi, "EFFECTS": EffectsApi, "VIDEOSETTINGS": VideoSettingsApi,
                 "PLAYLIST": ListElementApi, "LISTBOX": ListElementApi, "CURRENTPLAYLIST": ListElementApi}
        for el in view.walk():
            if el is view:
                continue
            api = kinds.get(el.node.tag, ElementApi)(self, el, window)
            el.api = api
            if el.id and el.id.isidentifier():
                self.eval(f"var {el.id} = __wrap('{api.id}');", "elements")
        # Script files for this view ("a.js;res://..."; res:// lives inside WMP itself).
        for name in (view.node.get("scriptfile") or "").split(";"):
            name = name.strip()
            if not name or name.lower().startswith("res:") or name.lower() in self.loaded_scripts:
                continue
            text = self.skin.read_text(name)
            if text is None:
                log_unsupported(f"missing script file {name}")
                continue
            self.loaded_scripts.add(name.lower())
            self.eval(jscript_compat(text), name)
        # jscript:/wmpprop: attribute bindings
        for el in view.walk():
            for key, raw in el.node.attrs.items():
                low = str(raw).strip().lower()
                if low.startswith(("wmpprop:", "jscript:", "wmpenabled:", "wmpdisabled:")) and not key.startswith("on") \
                        and not key.endswith("_onchange") and key not in ("scriptfile",):
                    self.bindings.append((window, el, key, raw.strip()))
        self.update_bindings(window)
        window.view_api = window.api
        # timerInterval written in the skin file starts the view's timer
        # (Halo 2 and Half-Life 2 run their opening animations from it).
        ms = as_int(view.node.get("timerinterval"), 0)
        if ms > 0:
            window.set_timer(ms)
        self.fire(view, "onload")

    def current_view_id(self):
        for vid, w in self.windows.items():
            if w.isVisible():
                return w.view.id
        return ""

    def open_view(self, vid, offset=None, replace=False):
        key = vid.lower()
        if key in self.windows:
            w = self.windows[key]
            w.show()
            w.raise_()
            return
        node = next((v for v in self.skin.views if (v.get("id") or "").lower() == key), None)
        if node is None:
            log_unsupported(f"openView: no view {vid}")
            return
        self._open_window(node, offset, replace)

    def close_view_id(self, vid):
        w = self.windows.get(vid.lower())
        if w is not None:
            self.close_view(w)

    def close_view(self, window):
        self.fire(window.view, "onclose")
        self.windows = {k: w for k, w in self.windows.items() if w is not window}
        self._close_window(window)

    # ---- events
    def fire(self, el, event, new_state=None):
        code = el.node.get(event.lower())
        if not code:
            return
        api = getattr(el, "api", None) or getattr(el, "_view_api", None)
        if api is None:
            win = next((w for w in self.windows.values() if w.view is el), None)
            api = win.api if win else None
        if api is None:
            return
        self.run(code, api, f"{el.node.tag}#{el.id}.{event}", new_state)

    def run(self, code, ctx_api, where="event", new_state=None):
        code = strip_prefix(code)
        if not code:
            return
        win = getattr(ctx_api, "window", None)
        view_id = win.api.id if win is not None and getattr(win, "api", None) else ctx_api.id
        js = f"__run('{ctx_api.id}', '{view_id}', {json.dumps(code)}, {json.dumps(new_state)})"
        self.eval(js, where)

    def fire_player(self, event, new_state=None):
        """PLAYER element handlers, e.g. playStateChange / PlayState_onchange."""
        for window in list(self.windows.values()):
            for node in window.view.node.find_all("PLAYER"):
                for key, code in node.attrs.items():
                    if key.replace("_on", "").replace("change", "") == event.replace("_on", "").replace("change", ""):
                        self.run(code, window.api, f"PLAYER.{key}", new_state)

    def set_event(self, **kw):
        """Update WMP's global `event` before firing a key/mouse handler."""
        sets = "".join(f"event.{k} = {json.dumps(v)};" for k, v in kw.items())
        self.eval(sets + "event.returnValue = true;", "event")

    def play_state(self):
        m = self.player.mpv
        try:
            if m.idle_active or not m.path:
                return 1 if m.playlist_count else 10
            return 2 if m.pause else 3
        except Exception:
            return 0

    # ---- bindings and player events (called ~4x a second)
    def tick(self):
        st = self.play_state()
        if st != self.last["playstate"]:
            self.last["playstate"] = st
            self.fire_player("playstatechange", st)
            self.fire_player("openstatechange", 13 if st in (2, 3) else 10)
        media = self.player.mpv.path
        if media != self.last["media"]:
            self.last["media"] = media
            self.fire_player("currentmediachange")
            self.fire_player("mediachange")
            self.fire_player("currentitemchange")
        # status_onChange and currentPlaylist_onChange: skins like Energy Blue
        # write their "now playing" text only from these.
        status = {1: "Stopped", 2: "Paused", 3: "Playing", 10: "Ready"}.get(st, "Ready")
        if status != self.last["status"] or media != self.last.get("status_media"):
            self.last["status"], self.last["status_media"] = status, media
            self.fire_player("statuschange")
        try:
            count = int(self.player.mpv.playlist_count or 0)
        except Exception:
            count = 0
        if count != self.last["playlist"]:
            self.last["playlist"] = count
            self.fire_player("currentplaylistchange")
        has_video = bool(self.video and self.video.has_video())
        if has_video != self.last["video"]:
            self.last["video"] = has_video
            ev = "onvideostart" if has_video else "onvideoend"
            for window in list(self.windows.values()):
                for el in window.view.walk():
                    if el.node.get(ev):
                        self.fire(el, ev)
        for window in list(self.windows.values()):
            self.update_bindings(window)

    def binding_value(self, raw):
        low = raw.lower()
        if low.startswith(("wmpenabled:", "wmpdisabled:")):
            target = raw.split(":", 1)[1].strip()
            method = target.rsplit(".", 1)[-1]
            r = self.eval(f"player.controls.isAvailable({json.dumps(method)})", "binding")
            on = bool(r.toVariant())
            return on if low.startswith("wmpenabled:") else not on
        expr = raw.split(":", 1)[1].strip()
        r = self.engine.evaluate(expr, "binding")
        return None if r.isError() else r.toVariant()

    def update_bindings(self, window):
        dirty = False
        for (w, el, key, raw) in self.bindings:
            if w is not window or getattr(el, "api", None) is None and el is not window.view:
                continue
            if getattr(el, "_dragging", False):
                continue
            v = self.binding_value(raw)
            if v is None:
                continue
            api = el.api if getattr(el, "api", None) else window.api
            if api.get(key) != v:
                api.set(key, v)
                dirty = True
        if dirty:
            window.refresh_soon()

    def write_back(self, el, key, value):
        """User moved a bound slider: push the value into the bound property."""
        for (w, e, k, raw) in self.bindings:
            if e is el and k == key and raw.lower().startswith("wmpprop:"):
                self.eval(f"{raw.split(':', 1)[1].strip()} = {json.dumps(value)}", "binding")

    # ---- animations
    def animate(self, api, targets, ms):
        if ms <= 0:
            for k, v in targets.items():
                api.set(k, v)
            return
        start = {k: float(api.get(k) or 0) for k in targets}
        self.anims = [a for a in self.anims if not (a[0] is api and set(a[2]) & set(targets))]
        self.anims.append((api, start, targets, time.monotonic(), ms / 1000))
        self.anim_timer.start()

    def _step_anims(self):
        now = time.monotonic()
        keep = []
        for api, start, targets, t0, dur in self.anims:
            f = min(1.0, (now - t0) / dur)
            for k, end in targets.items():
                api.set(k, round(start[k] + (end - start[k]) * f))
            if f < 1.0:
                keep.append((api, start, targets, t0, dur))
        self.anims = keep
        if not keep:
            self.anim_timer.stop()
