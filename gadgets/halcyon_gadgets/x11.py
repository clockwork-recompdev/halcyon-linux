"""Window hints Qt has no API for: keep gadgets out of the taskbar and pager."""
import ctypes
import ctypes.util

_x = None
_dpy = None


class _ClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _ClientMessage), ("pad", ctypes.c_long * 24)]


def _init():
    global _x, _dpy
    if _x is None:
        name = ctypes.util.find_library("X11")
        _x = ctypes.CDLL(name) if name else False
        if _x:
            _x.XOpenDisplay.restype = ctypes.c_void_p
            _x.XOpenDisplay.argtypes = [ctypes.c_char_p]
            _x.XInternAtom.restype = ctypes.c_ulong
            _x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            _x.XDefaultRootWindow.restype = ctypes.c_ulong
            _x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            _x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
            _x.XFlush.argtypes = [ctypes.c_void_p]
            _dpy = _x.XOpenDisplay(None)
    return bool(_x and _dpy)


def skip_taskbar(wid):
    """Ask the window manager to leave a mapped window out of the taskbar and pager."""
    if not _init():
        return
    atom = lambda n: _x.XInternAtom(_dpy, n, 0)
    ev = _XEvent()
    m = ev.xclient
    m.type = 33                                  # ClientMessage
    m.send_event = 1
    m.window = int(wid)
    m.message_type = atom(b"_NET_WM_STATE")
    m.format = 32
    m.data[0] = 1                                # _NET_WM_STATE_ADD
    m.data[1] = atom(b"_NET_WM_STATE_SKIP_TASKBAR")
    m.data[2] = atom(b"_NET_WM_STATE_SKIP_PAGER")
    m.data[3] = 1                                # source: normal application
    mask = (1 << 19) | (1 << 20)                 # SubstructureRedirect | SubstructureNotify
    _x.XSendEvent(_dpy, _x.XDefaultRootWindow(_dpy), 0, mask, ctypes.byref(ev))
    _x.XFlush(_dpy)
