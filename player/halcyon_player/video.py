"""Video for skins: mpv renders frames in software into a QImage, which skin
VIDEO areas (or a fallback window) paint.

Software rendering avoids embedding mpv's own window, which Wayland doesn't
allow, and lets the frame be clipped and shaped like any other skin part.
python-mpv doesn't expose libmpv's software-render parameters, so they're
passed by number (libmpv/render.h: SW_SIZE=17, SW_FORMAT=18, SW_STRIDE=19,
SW_POINTER=20).
"""
import ctypes

import mpv
from PySide6.QtCore import QObject, QRect, QSize, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

SW_SIZE, SW_FORMAT, SW_STRIDE, SW_POINTER = 17, 18, 19, 20


def _param(type_id, ptr):
    p = mpv.MpvRenderParam.__new__(mpv.MpvRenderParam)
    p.type_id = type_id
    p.data = ctypes.cast(ptr, ctypes.c_void_p)
    return p


class VideoRenderer(QObject):
    """Owns mpv's render context. `frame` is the latest video frame (or None)."""

    updated = Signal()
    _wake = Signal()

    def __init__(self, player):
        super().__init__()
        self.player = player
        self.frame = None
        self.target = QSize(640, 360)
        self.ctx = mpv.MpvRenderContext(player.mpv, "sw")
        self._wake.connect(self._render, Qt.QueuedConnection)
        self.ctx.update_cb = self._wake.emit          # called on mpv's thread

    def set_target(self, size):
        """Render at roughly the size it will be shown (keeps CPU use low)."""
        if size.isValid() and size != self.target:
            self.target = QSize(max(16, size.width()), max(16, size.height()))
            self._wake.emit()

    def has_video(self):
        try:
            return bool(self.player.mpv.video_params and (self.player.mpv.width or 0) > 0)
        except Exception:
            return False

    def _render(self):
        if not (self.ctx.update() & 1):              # MPV_RENDER_UPDATE_FRAME
            return
        if not self.has_video():
            if self.frame is not None:
                self.frame = None
                self.updated.emit()
            return
        # Fit the video's aspect ratio inside the target box.
        vw, vh = self.player.mpv.dwidth or self.player.mpv.width, self.player.mpv.dheight or self.player.mpv.height
        tw, th = self.target.width(), self.target.height()
        scale = min(tw / vw, th / vh) if vw and vh else 1.0
        w, h = max(2, int(vw * scale)) & ~1, max(2, int(vh * scale)) & ~1
        # 3-byte RGB: mpv's 4-byte formats leave the spare byte 0, which Qt would
        # read as alpha (fully transparent video).
        img = QImage(w, h, QImage.Format_RGB888)
        buf = (ctypes.c_ubyte * img.sizeInBytes()).from_buffer(img.bits())
        size = (ctypes.c_int * 2)(w, h)
        fmt = ctypes.c_char_p(b"rgb24")
        stride = ctypes.c_size_t(img.bytesPerLine())
        params = (mpv.MpvRenderParam * 5)(
            _param(SW_SIZE, ctypes.pointer(size)),
            _param(SW_FORMAT, fmt),
            _param(SW_STRIDE, ctypes.pointer(stride)),
            _param(SW_POINTER, ctypes.addressof(buf)),
            _param(0, None))
        mpv._mpv_render_context_render(self.ctx._handle, params)
        del buf
        self.frame = img
        self.updated.emit()

    def paint(self, p: QPainter, rect: QRect):
        """Letterboxed frame inside rect (black bars)."""
        p.fillRect(rect, Qt.black)
        if self.frame is None:
            return
        f = self.frame.size().scaled(rect.size(), Qt.KeepAspectRatio)
        x = rect.x() + (rect.width() - f.width()) // 2
        y = rect.y() + (rect.height() - f.height()) // 2
        p.drawImage(QRect(x, y, f.width(), f.height()), self.frame)

    def close(self):
        try:
            self.ctx.free()
        except Exception:
            pass


class VideoWindow(QWidget):
    """Plain video window for skins without a visible VIDEO area."""

    def __init__(self, renderer):
        super().__init__()
        self.renderer = renderer
        self.setWindowTitle("Halcyon Media Player — Video")
        self.resize(640, 360)
        renderer.updated.connect(self.update)

    def resizeEvent(self, e):
        self.renderer.set_target(self.size())

    def paintEvent(self, _):
        p = QPainter(self)
        self.renderer.paint(p, self.rect())

    def mouseDoubleClickEvent(self, _):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()
