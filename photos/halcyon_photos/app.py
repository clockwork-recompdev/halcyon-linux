"""Halcyon Photo Viewer: open a picture the way Windows Vista's Photo Gallery
Viewer did - the picture fills the window, a glass control bar along the
bottom (zoom, previous, a big round slide show button, next, rotate,
delete) and File / Fix / Print / E-mail / Background / Info along the top.
Pictures in the same folder are a click (or an arrow key) away."""
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QFontDatabase, QIcon, QImage, QImageReader, QKeySequence,
                           QLinearGradient, QMovie, QPainter, QPainterPath, QPalette, QPen, QPixmap,
                           QPolygonF, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QSlider, QToolButton, QVBoxLayout, QWidget)

APP = "Halcyon Photo Viewer"
EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".avif", ".ico", ".jfif",
        ".svg", ".tga", ".jxl"}
TEXT = "#1e395b"
SLIDE_SECONDS = 4


def natural(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def siblings(path):
    try:
        return sorted((p for p in path.parent.iterdir() if p.suffix.lower() in EXTS and p.is_file()), key=natural)
    except OSError:
        return [path]


def human(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


# -------------------------------------------------------------------- EXIF ---
EXIF_TAGS = {0x010F: "make", 0x0110: "model", 0x0112: "orientation", 0x9003: "taken", 0x829A: "exposure",
             0x829D: "fnumber", 0x8827: "iso", 0x920A: "focal", 0x8769: "exif_ifd"}


def exif(path):
    """The few EXIF fields the Info pane shows, read straight from a JPEG."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return {}
            while True:
                marker, = struct.unpack(">H", f.read(2))
                size, = struct.unpack(">H", f.read(2))
                if marker == 0xFFE1:
                    data = f.read(size - 2)
                    if data[:6] == b"Exif\0\0":
                        return _tiff(data[6:])
                    return {}
                if marker in (0xFFDA, 0xFFD9) or size < 2:
                    return {}
                f.seek(size - 2, 1)
    except (OSError, struct.error):
        return {}


def _tiff(t):
    end = "<" if t[:2] == b"II" else ">"
    out = {}

    def val(typ, count, raw):
        if typ == 2:
            off = struct.unpack(end + "I", raw)[0] if count > 4 else None
            s = t[off:off + count] if off is not None else raw[:count]
            return s.split(b"\0")[0].decode(errors="replace").strip()
        if typ == 3:
            return struct.unpack(end + "H", raw[:2])[0]
        if typ == 4:
            return struct.unpack(end + "I", raw)[0]
        if typ in (5, 10):
            off = struct.unpack(end + "I", raw)[0]
            a, b = struct.unpack(end + ("II" if typ == 5 else "ii"), t[off:off + 8])
            return (a, b)
        return None

    def ifd(off):
        n, = struct.unpack(end + "H", t[off:off + 2])
        for i in range(n):
            e = t[off + 2 + 12 * i: off + 14 + 12 * i]
            tag, typ, count = struct.unpack(end + "HHI", e[:8])
            if tag in EXIF_TAGS:
                out[EXIF_TAGS[tag]] = val(typ, count, e[8:12])
    try:
        ifd(struct.unpack(end + "I", t[4:8])[0])
        if "exif_ifd" in out:
            ifd(out.pop("exif_ifd"))
    except (struct.error, IndexError):
        pass
    return out


def describe_exif(x):
    rows = []
    cam = " ".join(v for v in (x.get("make"), x.get("model")) if v)
    if x.get("model", "").startswith(x.get("make", "") or "\0"):
        cam = x["model"]
    if cam:
        rows.append(("Camera", cam))
    bits = []
    if isinstance(x.get("exposure"), tuple) and x["exposure"][1]:
        a, b = x["exposure"]
        bits.append(f"1/{round(b / a)} sec" if a and a < b else f"{a / b:g} sec")
    if isinstance(x.get("fnumber"), tuple) and x["fnumber"][1]:
        bits.append(f"f/{x['fnumber'][0] / x['fnumber'][1]:g}")
    if x.get("iso"):
        bits.append(f"ISO {x['iso']}")
    if isinstance(x.get("focal"), tuple) and x["focal"][1]:
        bits.append(f"{x['focal'][0] / x['focal'][1]:g} mm")
    if bits:
        rows.append(("Exposure", ", ".join(bits)))
    return rows


def transform_file(p, t, jpegtran, image=None):
    """Rotate or flip a picture file itself, as Vista did; JPEGs go through jpegtran so the
    picture isn't recompressed. Returns an error message, or None."""
    p = Path(p)
    if not os.access(p, os.W_OK):
        return "This picture is read-only, so it can't be changed."
    if p.suffix.lower() in (".jpg", ".jpeg", ".jfif") and shutil.which("jpegtran") and \
            exif(p).get("orientation", 1) in (1, None):
        tmp = p.with_name(f".{p.name}.halcyon-tmp")
        # Lossless when the size allows; otherwise re-encode below rather than trim the edges.
        r = subprocess.run(["jpegtran", "-copy", "all", "-optimize", "-perfect", *jpegtran, "-outfile", str(tmp),
                            str(p)], capture_output=True)
        if r.returncode == 0:
            os.replace(tmp, p)
            return None
        tmp.unlink(missing_ok=True)
    if image is None:
        image, _, err = load(p)
        if image is None:
            return err
    img = image.transformed(t, Qt.SmoothTransformation)
    if not img.save(str(p), "JPG" if p.suffix.lower() == ".jfif" else None, 95):
        return "Halcyon Photo Viewer can't save changes to this kind of picture."
    return None


def show_in_folder(path):
    if shutil.which("dolphin"):
        subprocess.Popen(["dolphin", "--select", str(path)], start_new_session=True)
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)], start_new_session=True)


# ------------------------------------------------------------------ drawing ---
def glyph(kind, size=22, color="#2d4f7c"):
    """The control-bar symbols, drawn here."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s, c = size, size / 2
    col = QColor(color)
    pen = QPen(col, s * 0.09, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    if kind in ("prev", "next"):
        d = -1 if kind == "prev" else 1
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        for k in (0, 1):
            x0 = c - d * s * 0.28 + d * k * s * 0.3
            p.drawPolygon(QPolygonF([QPointF(x0, c - s * 0.25), QPointF(x0 + d * s * 0.3, c),
                                     QPointF(x0, c + s * 0.25)]))
    elif kind in ("rotl", "rotr"):
        # A 3/4 circle open at the top with the arrowhead on one end: ↺ counterclockwise, ↻ clockwise.
        cw = kind == "rotr"
        r = QRectF(s * 0.2, s * 0.24, s * 0.6, s * 0.6)
        p.setBrush(Qt.NoBrush)
        p.drawArc(r, (60 if cw else 120) * 16, (270 if cw else -270) * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        a = math.radians(60 if cw else 120)
        tip = QPointF(r.center().x() + math.cos(a) * r.width() / 2, r.center().y() - math.sin(a) * r.height() / 2)
        d = -1 if cw else 1
        p.drawPolygon(QPolygonF([tip + QPointF(-d * s * 0.02, -s * 0.15), tip + QPointF(-d * s * 0.02, s * 0.15),
                                 tip + QPointF(-d * s * 0.2, -s * 0.01)]))
    elif kind == "delete":
        p.setPen(QPen(QColor("#c8331f"), s * 0.13, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
        p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))
    elif kind == "zoom":
        p.setBrush(QColor(255, 255, 255, 120))
        p.drawEllipse(QRectF(s * 0.18, s * 0.16, s * 0.48, s * 0.48))
        p.setPen(QPen(col, s * 0.13, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(s * 0.6, s * 0.58), QPointF(s * 0.82, s * 0.8))
    elif kind == "fit":
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(s * 0.2, s * 0.26, s * 0.6, s * 0.48))
        p.drawLine(QPointF(s * 0.32, s * 0.5), QPointF(s * 0.68, s * 0.5))
        for x0, d in ((s * 0.32, 1), (s * 0.68, -1)):
            p.drawLine(QPointF(x0, s * 0.5), QPointF(x0 + d * s * 0.08, s * 0.42))
            p.drawLine(QPointF(x0, s * 0.5), QPointF(x0 + d * s * 0.08, s * 0.58))
    elif kind == "actual":
        f = QFont()
        f.setPixelSize(int(s * 0.42))
        f.setBold(True)
        p.setFont(f)
        p.drawRect(QRectF(s * 0.14, s * 0.24, s * 0.72, s * 0.52))
        p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "1:1")
    p.end()
    return QIcon(pm)


class PlayOrb(QToolButton):
    """The big round blue slide show button in the middle of the bar."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(46, 46)
        self.setToolTip("Play slide show (F11)")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(2, 2, self.width() - 4, self.height() - 4)
        hover, down = self.underMouse(), self.isDown()
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        g.setColorAt(0, QColor("#1d4f99" if down else "#6fb4f2" if hover else "#5aa2ea"))
        g.setColorAt(0.5, QColor("#2a6cc6" if not hover else "#3580dc"))
        g.setColorAt(1, QColor("#4ea6ff" if not down else "#2c6cc0"))
        p.setPen(QPen(QColor("#163f7a"), 1.4))
        p.setBrush(g)
        p.drawEllipse(r)
        hl = QLinearGradient(0, r.top(), 0, r.center().y())
        hl.setColorAt(0, QColor(255, 255, 255, 210))
        hl.setColorAt(1, QColor(255, 255, 255, 25))
        p.setPen(Qt.NoPen)
        p.setBrush(hl)
        p.drawEllipse(QRectF(r.left() + 5, r.top() + 2, r.width() - 10, r.height() * 0.5))
        if hover and not down:
            glow = QRadialGradient(r.center().x(), r.bottom() - 3, r.width() * 0.55)
            glow.setColorAt(0, QColor(150, 235, 255, 170))
            glow.setColorAt(1, QColor(150, 235, 255, 0))
            p.setBrush(glow)
            p.drawEllipse(r)
        c = r.center()
        tri = QPolygonF([QPointF(c.x() - 6, c.y() - 9), QPointF(c.x() + 10, c.y()), QPointF(c.x() - 6, c.y() + 9)])
        p.setPen(QPen(QColor(10, 40, 90, 120), 1))
        p.setBrush(QColor("white"))
        p.drawPolygon(tri)


class Bar(QWidget):
    """Glass strip (top or bottom)."""
    def __init__(self, height, bottom):
        super().__init__()
        self.bottom = bottom
        self.setFixedHeight(height)

    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        if self.bottom:
            g.setColorAt(0, QColor("#eef4fb"))
            g.setColorAt(0.45, QColor("#dbe7f5"))
            g.setColorAt(0.46, QColor("#c9dbf0"))
            g.setColorAt(1, QColor("#d9e6f5"))
        else:
            g.setColorAt(0, QColor("#f6f9fd"))
            g.setColorAt(1, QColor("#dde8f5"))
        p.fillRect(self.rect(), g)
        p.setPen(QColor("#9fb6d3"))
        y = 0 if self.bottom else self.height() - 1
        p.drawLine(0, y, self.width(), y)
        p.setPen(QColor(255, 255, 255, 200))
        p.drawLine(0, y + (1 if self.bottom else -1), self.width(), y + (1 if self.bottom else -1))


# ------------------------------------------------------------------ canvas ---
class Canvas(QWidget):
    """Shows the picture: fit to the window, or zoomed and dragged around."""
    zoomed = Signal(float)

    def __init__(self, background="#ffffff"):
        super().__init__()
        self.bg = QColor(background)
        self.image = None           # QImage
        self.movie = None
        self.message = ""
        self.zoom = None            # None = fit
        self.offset = QPointF(0, 0)
        self.drag = None
        self.fade_from = None
        self.fade = 1.0
        self._fade = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=700)
        self._fade.valueChanged.connect(self._fade_step)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def _fade_step(self, v):
        self.fade = v
        self.update()

    def set_image(self, img, movie=None, crossfade=False):
        if crossfade and self.image is not None:
            self.fade_from = self.grab_frame()
            self.fade = 0.0
            self._fade.start()
        else:
            self.fade_from = None
        if self.movie:
            self.movie.stop()
            self.movie.frameChanged.disconnect()
        self.movie = movie
        self.image = img
        self.message = ""
        self.zoom = None
        self.offset = QPointF(0, 0)
        if movie:
            movie.frameChanged.connect(lambda _: (setattr(self, "image", movie.currentImage()), self.update()))
            movie.start()
        self.zoomed.emit(self.scale())
        self.update()

    def grab_frame(self):
        pm = QPixmap(self.size())
        pm.fill(self.bg)
        p = QPainter(pm)
        self.draw(p)
        p.end()
        return pm

    def set_message(self, text):
        self.image, self.movie, self.message = None, None, text
        self.update()

    def fit_scale(self):
        if not self.image or self.image.isNull():
            return 1.0
        w, h = self.image.width(), self.image.height()
        m = 0.96 if self.bg.lightness() > 100 else 1.0
        return min(1.0, self.width() * m / w, self.height() * m / h)

    def scale(self):
        return self.zoom if self.zoom is not None else self.fit_scale()

    def set_zoom(self, z, anchor=None):
        if not self.image:
            return
        old = self.scale()
        z = max(min(self.fit_scale(), 0.05), min(16.0, z))
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        # keep the point under the anchor still
        center = QPointF(self.width() / 2, self.height() / 2) + self.offset
        rel = (anchor - center) / old
        self.zoom = z
        self.offset = anchor - rel * z - QPointF(self.width() / 2, self.height() / 2)
        if abs(z - self.fit_scale()) < 1e-3:
            self.zoom, self.offset = None, QPointF(0, 0)
        self.clamp()
        self.zoomed.emit(self.scale())
        self.update()

    def clamp(self):
        if not self.image:
            return
        s = self.scale()
        w, h = self.image.width() * s, self.image.height() * s
        mx, my = max(0, (w - self.width()) / 2), max(0, (h - self.height()) / 2)
        self.offset = QPointF(max(-mx, min(mx, self.offset.x())), max(-my, min(my, self.offset.y())))

    def target(self):
        s = self.scale()
        w, h = self.image.width() * s, self.image.height() * s
        x = (self.width() - w) / 2 + self.offset.x()
        y = (self.height() - h) / 2 + self.offset.y()
        return QRectF(x, y, w, h)

    def draw(self, p):
        if self.image and not self.image.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform, self.scale() < 2)
            r = self.target()
            if self.bg.lightness() > 100 and not self.image.hasAlphaChannel():
                p.setPen(QPen(QColor(0, 0, 0, 40), 1))
                p.drawRect(r.adjusted(-1, -1, 0, 0))
            p.drawImage(r, self.image)
        elif self.message:
            p.setPen(QColor("#6d7f95") if self.bg.lightness() > 100 else QColor("#aab"))
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.message)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg)
        if self.fade_from is not None and self.fade < 1:
            p.setOpacity(1 - self.fade)
            p.drawPixmap(0, 0, self.fade_from)
            p.setOpacity(self.fade)
        self.draw(p)

    def resizeEvent(self, e):
        self.clamp()
        self.zoomed.emit(self.scale())

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120
        if steps:
            self.set_zoom(self.scale() * (1.2 ** steps), e.position())

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.zoom is not None:
            self.drag = (e.position(), self.offset)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self.drag:
            start, off = self.drag
            self.offset = off + (e.position() - start)
            self.clamp()
            self.update()
        elif self.zoom is not None and self.image:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, e):
        self.drag = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, e):
        self.window().slideshow()


def load(path):
    """(QImage, QMovie or None, error text)."""
    path = str(path)
    r = QImageReader(path)
    r.setAutoTransform(True)                      # phone photos: honour the rotation
    if r.supportsAnimation() and r.imageCount() != 1:
        m = QMovie(path)
        if m.isValid():
            m.jumpToFrame(0)
            return m.currentImage(), m, None
    img = r.read()
    if img.isNull():
        return None, None, r.errorString()
    return img, None, None


# ------------------------------------------------------------- slide show ---
class SlideShow(QWidget):
    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.setWindowTitle("Slide Show")
        self.setStyleSheet("background: black;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.canvas = Canvas("#000000")
        self.canvas.mouseDoubleClickEvent = lambda e: self.close()
        lay.addWidget(self.canvas)
        self.timer = QTimer(self, interval=SLIDE_SECONDS * 1000, timeout=lambda: self.step(1))
        self.paused = False
        self.index = viewer.index
        self.show_current(fade=False)
        self.timer.start()
        self.setCursor(Qt.BlankCursor)

    def show_current(self, fade=True):
        files = self.viewer.files
        if not files:
            return self.close()
        img, movie, err = load(files[self.index])
        if img is None:
            return self.canvas.set_message(f"{files[self.index].name}\n{err}")
        self.canvas.set_image(img, movie, crossfade=fade)

    def step(self, d):
        if not self.viewer.files:
            return
        self.index = (self.index + d) % len(self.viewer.files)
        self.show_current()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Escape, Qt.Key_F11):
            self.close()
        elif k in (Qt.Key_Right, Qt.Key_Down, Qt.Key_PageDown):
            self.step(1)
            self.timer.start()
        elif k in (Qt.Key_Left, Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Backspace):
            self.step(-1)
            self.timer.start()
        elif k == Qt.Key_Space:
            self.paused = not self.paused
            self.timer.stop() if self.paused else self.timer.start()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.close()

    def closeEvent(self, e):
        self.timer.stop()
        self.viewer.show_index(self.index)
        e.accept()


# ------------------------------------------------------------------ viewer ---
QSS = f"""
QMainWindow {{ background: white; }}
QToolButton {{ border: 1px solid transparent; border-radius: 3px; padding: 3px 7px; color: {TEXT}; }}
QToolButton:hover {{ border-color: #a8c8ec; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 #fdfeff, stop:0.5 #ebf4fd, stop:0.51 #d9eafb, stop:1 #e8f2fc); }}
QToolButton:pressed, QToolButton:checked {{ background: #c8dff7; border-color: #7da2ce; }}
QToolButton:disabled {{ color: #9aa9bb; }}
QToolButton::menu-indicator {{ image: none; }}
QMenu {{ background: #f7f9fc; border: 1px solid #97a8c0; padding: 2px; }}
QMenu::item {{ padding: 4px 24px 4px 26px; }}
QMenu::item:selected {{ background: #d9e9fb; border: 1px solid #a8c8ec; border-radius: 2px; color: black; }}
QMenu::item:disabled {{ color: #9aa9bb; }}
#info {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f6f9fd, stop:1 #eaf1f9);
    border-left: 1px solid #c5d6e8; }}
#info QLabel {{ color: #1e1e1e; }}
QSlider::groove:vertical {{ background: #c9d8ea; width: 4px; border-radius: 2px; }}
QSlider::handle:vertical {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffffff, stop:1 #cfe0f3);
    border: 1px solid #6f97c6; height: 10px; margin: 0 -6px; border-radius: 3px; }}
"""


class Viewer(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon.fromTheme("image-x-generic", QIcon.fromTheme("graphics-viewer-document")))
        self.resize(1000, 720)
        self.setAcceptDrops(True)
        self.files, self.index = [], -1

        w = QWidget()
        self.setCentralWidget(w)
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        top = Bar(34, bottom=False)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(6, 2, 6, 2)
        tl.setSpacing(2)
        file_m = QMenu(self)
        file_m.addAction("Open…", self.open_dialog, QKeySequence.Open)
        file_m.addAction("Make a Copy…", self.make_copy, QKeySequence("Ctrl+Shift+S"))
        file_m.addAction("Open File Location", self.show_in_folder)
        file_m.addSeparator()
        file_m.addAction("Copy", self.copy, QKeySequence.Copy)
        file_m.addAction("Delete", self.delete)
        file_m.addSeparator()
        file_m.addAction("Exit", self.close)
        self.file_btn = self.top_button(tl, "File", menu=file_m)
        fix_m = QMenu(self)
        fix_m.addAction("Rotate Counterclockwise", lambda: self.rotate(-90), QKeySequence("Ctrl+,"))
        fix_m.addAction("Rotate Clockwise", lambda: self.rotate(90), QKeySequence("Ctrl+."))
        fix_m.addAction("Flip Horizontal", lambda: self.flip(True))
        fix_m.addAction("Flip Vertical", lambda: self.flip(False))
        self.top_button(tl, "Fix", menu=fix_m)
        self.top_button(tl, "Print", self.print_photo)
        self.mail_btn = self.top_button(tl, "E-mail", self.email)
        self.top_button(tl, "Set as Background", self.set_background)
        open_m = QMenu(self, aboutToShow=self.fill_open_with)
        self.open_menu = open_m
        self.top_button(tl, "Open", menu=open_m)
        tl.addStretch(1)
        self.info_btn = self.top_button(tl, "Info", self.toggle_info)
        self.info_btn.setCheckable(True)
        col.addWidget(top)

        mid = QHBoxLayout()
        mid.setSpacing(0)
        self.canvas = Canvas()
        self.canvas.zoomed.connect(self.zoom_changed)
        mid.addWidget(self.canvas, 1)
        self.info = QFrame(objectName="info")
        self.info.setFixedWidth(240)
        il = QVBoxLayout(self.info)
        il.setContentsMargins(14, 14, 14, 14)
        self.info_text = QLabel(wordWrap=True, textFormat=Qt.RichText, alignment=Qt.AlignTop)
        self.info_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        il.addWidget(self.info_text)
        il.addStretch(1)
        self.info.hide()
        mid.addWidget(self.info)
        col.addLayout(mid, 1)

        bottom = Bar(58, bottom=True)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(12, 4, 12, 4)
        bl.setSpacing(4)
        self.zoom_btn = self.icon_button(bl, "zoom", "Change the display size", self.toggle_zoom_slider)
        self.fit_btn = self.icon_button(bl, "actual", "Actual size", self.fit_toggle)
        self.zoom_label = QLabel()
        self.zoom_label.setStyleSheet(f"color: {TEXT};")
        self.zoom_label.setFixedWidth(44)
        bl.addWidget(self.zoom_label)
        bl.addStretch(1)
        self.prev_btn = self.icon_button(bl, "prev", "Previous (Left arrow)", lambda: self.step(-1))
        self.play = PlayOrb()
        self.play.clicked.connect(self.slideshow)
        bl.addSpacing(6)
        bl.addWidget(self.play)
        bl.addSpacing(6)
        self.next_btn = self.icon_button(bl, "next", "Next (Right arrow)", lambda: self.step(1))
        bl.addSpacing(16)
        self.icon_button(bl, "rotl", "Rotate counterclockwise (Ctrl+,)", lambda: self.rotate(-90))
        self.icon_button(bl, "rotr", "Rotate clockwise (Ctrl+.)", lambda: self.rotate(90))
        bl.addSpacing(16)
        self.icon_button(bl, "delete", "Delete (Del)", self.delete)
        bl.addStretch(1)
        self.position = QLabel()
        self.position.setStyleSheet(f"color: {TEXT};")
        self.position.setFixedWidth(90)
        self.position.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bl.addWidget(self.position)
        col.addWidget(bottom)

        self.slider = QSlider(Qt.Vertical, self)
        self.slider.setRange(0, 100)
        self.slider.setFixedSize(26, 150)
        self.slider.setStyleSheet("background: #f2f6fb; border: 1px solid #9fb6d3; border-radius: 3px;")
        self.slider.valueChanged.connect(self.slider_moved)
        self.slider.hide()

        for key, fn in (("Right", lambda: self.step(1)), ("Left", lambda: self.step(-1)),
                        ("PgDown", lambda: self.step(1)), ("PgUp", lambda: self.step(-1)),
                        ("Home", lambda: self.show_index(0)), ("End", lambda: self.show_index(len(self.files) - 1)),
                        ("F11", self.slideshow), ("Delete", self.delete), ("Ctrl+=", lambda: self.zoom_by(1.25)),
                        ("Ctrl++", lambda: self.zoom_by(1.25)), ("Ctrl+-", lambda: self.zoom_by(0.8)),
                        ("Ctrl+0", lambda: self.canvas.set_zoom(self.canvas.fit_scale())),
                        ("Ctrl+Alt+0", lambda: self.canvas.set_zoom(1.0)), ("Alt+Return", self.toggle_info),
                        ("Escape", self.escape)):
            a = QAction(self, shortcut=QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
        if path:
            self.open(Path(path))
        else:
            self.canvas.set_message("Open a picture: File → Open, or drag one here.")
        self.update_buttons()

    def top_button(self, lay, text, slot=None, menu=None):
        b = QToolButton(text=text + (" ▾" if menu else ""), autoRaise=True)
        if menu:
            b.setMenu(menu)
            b.setPopupMode(QToolButton.InstantPopup)
        if slot:
            b.clicked.connect(slot)
        lay.addWidget(b)
        return b

    def icon_button(self, lay, kind, tip, slot):
        b = QToolButton(icon=glyph(kind), toolTip=tip, autoRaise=True)
        b.setIconSize(QSize(22, 22))
        b.setFixedSize(34, 34)
        b.clicked.connect(slot)
        lay.addWidget(b)
        return b

    # --- files
    @property
    def path(self):
        return self.files[self.index] if 0 <= self.index < len(self.files) else None

    def open(self, path):
        path = path.resolve()
        self.files = siblings(path)
        if path not in self.files:
            self.files.insert(0, path)
        self.show_index(self.files.index(path))

    def show_index(self, i):
        if not self.files:
            self.index = -1
            self.canvas.set_message("There are no more pictures in this folder.")
            self.setWindowTitle(APP)
            self.update_buttons()
            return
        self.index = max(0, min(i, len(self.files) - 1))
        p = self.path
        img, movie, err = load(p)
        if img is None:
            self.canvas.set_message(f"Halcyon Photo Viewer can't open this picture.\n\n{p.name}\n{err}")
        else:
            self.canvas.set_image(img, movie)
        self.setWindowTitle(f"{p.name} - {APP}")
        self.update_buttons()
        self.update_info()

    def step(self, d):
        if self.files:
            self.show_index((self.index + d) % len(self.files))

    def update_buttons(self):
        n = len(self.files)
        for b in (self.prev_btn, self.next_btn):
            b.setEnabled(n > 1)
        self.play.setEnabled(n > 0)
        self.position.setText(f"{self.index + 1} of {n}" if n else "")

    def update_info(self):
        p = self.path
        if not p or not self.info.isVisible():
            return
        rows = [("Name", p.name)]
        img = self.canvas.image
        try:
            st = p.stat()
        except OSError:
            st = None
        x = exif(p) if p.suffix.lower() in (".jpg", ".jpeg", ".jfif") else {}
        if x.get("taken"):
            try:
                t = time.strptime(x["taken"], "%Y:%m:%d %H:%M:%S")
                rows.append(("Date taken", time.strftime("%-m/%-d/%Y %-I:%M %p", t)))
            except ValueError:
                pass
        if st:
            rows.append(("Modified", time.strftime("%-m/%-d/%Y %-I:%M %p", time.localtime(st.st_mtime))))
        if img is not None:
            rows.append(("Dimensions", f"{img.width()} × {img.height()}"))
        if st:
            rows.append(("Size", human(st.st_size)))
        rows.append(("Type", p.suffix.lstrip(".").upper() + " image"))
        rows += describe_exif(x)
        rows.append(("Folder", str(p.parent)))
        out = []
        for k, v in rows:
            out.append(f"<div style='color:#6d7f95; margin-top:8px'>{k}</div>"
                       f"<div style='color:#1e1e1e'>{v.replace('&', '&amp;').replace('<', '&lt;')}</div>")
        self.info_text.setText(f"<div style='color:{TEXT}; font-size:12pt'>Info</div>" + "".join(out))

    def toggle_info(self):
        self.info.setVisible(not self.info.isVisible())
        self.info_btn.setChecked(self.info.isVisible())
        self.update_info()

    def open_dialog(self):
        start = str(self.path.parent) if self.path else str(Path.home() / "Pictures")
        f, _ = QFileDialog.getOpenFileName(self, "Open", start,
                                           "Pictures (" + " ".join("*" + e for e in sorted(EXTS)) + ");;All files (*)")
        if f:
            self.open(Path(f))

    def make_copy(self):
        if not self.path:
            return
        f, _ = QFileDialog.getSaveFileName(self, "Make a Copy", str(self.path.with_name(self.path.stem + " - Copy" +
                                                                                       self.path.suffix)))
        if f:
            shutil.copy2(self.path, f)

    def copy(self):
        if self.canvas.image:
            QApplication.clipboard().setImage(self.canvas.image)

    def show_in_folder(self):
        if self.path:
            show_in_folder(self.path)

    def fill_open_with(self):
        m = self.open_menu
        m.clear()
        for name, cmd in (("GIMP", "gimp"), ("Krita", "krita"), ("Pinta", "pinta"), ("KolourPaint", "kolourpaint"),
                          ("Gwenview", "gwenview"), ("Inkscape", "inkscape")):
            if shutil.which(cmd) and self.path:
                m.addAction(name, lambda c=cmd: subprocess.Popen([c, str(self.path)], start_new_session=True))
        if m.isEmpty():
            a = m.addAction("No picture editors installed")
            a.setEnabled(False)
        m.addSeparator()
        m.addAction("Choose Program…", lambda: self.path and subprocess.Popen(
            ["kioclient", "openWith", str(self.path)] if shutil.which("kioclient") else ["xdg-open", str(self.path)],
            start_new_session=True))

    # --- fixing
    def rotate(self, deg):
        self.transform_file(QTransform().rotate(deg), jpegtran=["-rotate", str(deg % 360)])

    def flip(self, horizontal):
        self.transform_file(QTransform().scale(-1 if horizontal else 1, 1 if horizontal else -1),
                            jpegtran=["-flip", "horizontal" if horizontal else "vertical"])

    def transform_file(self, t, jpegtran):
        p = self.path
        if not p or self.canvas.movie:
            return
        err = transform_file(p, t, jpegtran, self.canvas.image)
        if err:
            QMessageBox.warning(self, APP, err)
            return
        self.show_index(self.index)

    def delete(self):
        p = self.path
        if not p:
            return
        if QMessageBox.question(self, "Delete File", f"Are you sure you want to move this file to the Trash?\n\n{p.name}"
                                ) != QMessageBox.Yes:
            return
        from PySide6.QtCore import QFile
        if not QFile.moveToTrash(str(p)):
            QMessageBox.warning(self, APP, "The file couldn't be moved to the Trash.")
            return
        del self.files[self.index]
        self.show_index(min(self.index, len(self.files) - 1))

    # --- sharing
    def email(self):
        if not self.path:
            return
        if shutil.which("halcyon-mail"):
            subprocess.Popen(["halcyon-mail", "--attach", str(self.path)], start_new_session=True)
        else:
            subprocess.Popen(["xdg-email", "--attach", str(self.path)], start_new_session=True)

    def set_background(self):
        if not self.path:
            return
        if shutil.which("plasma-apply-wallpaperimage"):
            r = subprocess.run(["plasma-apply-wallpaperimage", str(self.path)], capture_output=True, text=True)
            if r.returncode != 0:
                QMessageBox.warning(self, APP, r.stderr.strip() or "Couldn't set the desktop background.")
        else:
            QMessageBox.information(self, APP, "Setting the background needs the Plasma desktop.")

    def print_photo(self):
        if not self.canvas.image:
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.HighResolution)
        if QPrintDialog(printer, self).exec():
            p = QPainter(printer)
            page = printer.pageRect(QPrinter.DevicePixel)
            img = self.canvas.image
            size = img.size().scaled(page.size().toSize(), Qt.KeepAspectRatio)
            p.drawImage(QRectF((page.width() - size.width()) / 2, (page.height() - size.height()) / 2,
                               size.width(), size.height()), img)
            p.end()

    # --- zoom and slide show
    def zoom_by(self, f):
        self.canvas.set_zoom(self.canvas.scale() * f)

    def fit_toggle(self):
        if self.canvas.zoom is None and abs(self.canvas.fit_scale() - 1.0) > 1e-3:
            self.canvas.set_zoom(1.0)
        else:
            self.canvas.set_zoom(self.canvas.fit_scale())

    def zoom_changed(self, s):
        self.zoom_label.setText(f"{round(s * 100)}%" if self.canvas.image else "")
        fit = self.canvas.zoom is None
        self.fit_btn.setIcon(glyph("actual" if fit else "fit"))
        self.fit_btn.setToolTip("Actual size (Ctrl+Alt+0)" if fit else "Fit to window (Ctrl+0)")
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(s))
        self.slider.blockSignals(False)

    def _to_slider(self, s):
        lo = math.log(max(0.05, min(self.canvas.fit_scale(), 1.0)))
        return int(100 * (math.log(s) - lo) / (math.log(16) - lo)) if s > 0 else 0

    def slider_moved(self, v):
        lo = math.log(max(0.05, min(self.canvas.fit_scale(), 1.0)))
        self.canvas.set_zoom(math.exp(lo + (math.log(16) - lo) * v / 100))

    def toggle_zoom_slider(self):
        if self.slider.isVisible():
            return self.slider.hide()
        pos = self.zoom_btn.mapTo(self, self.zoom_btn.rect().topLeft())
        self.slider.move(pos.x() + 4, pos.y() - self.slider.height() - 4)
        self.slider.show()
        self.slider.raise_()

    def escape(self):
        if self.slider.isVisible():
            self.slider.hide()
        elif self.canvas.zoom is not None:
            self.canvas.set_zoom(self.canvas.fit_scale())

    def slideshow(self):
        if not self.files:
            return
        self.show_window = SlideShow(self)
        self.show_window.showFullScreen()

    # --- drag and drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.open(Path(urls[0]))


def light_palette():
    pal = QPalette()
    for role, color in ((QPalette.Window, "#f3f7fc"), (QPalette.WindowText, "#000000"),
                        (QPalette.Base, "#ffffff"), (QPalette.Text, "#000000"),
                        (QPalette.Button, "#eef3f9"), (QPalette.ButtonText, "#000000"),
                        (QPalette.Highlight, "#3399ff"), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.ToolTipBase, "#ffffe1"), (QPalette.ToolTipText, "#000000")):
        pal.setColor(role, QColor(color))
    return pal


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("halcyon-photos")
    app.setDesktopFileName("halcyon-photos")
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
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if (not args and "--slideshow" not in sys.argv) or "--gallery" in sys.argv or "--import" in sys.argv:
        from . import gallery                 # no picture given: the Photo Gallery library, as in Vista
        app.setStyleSheet(gallery.QSS)
        w = gallery.Gallery()
        w.show()
        if "--import" in sys.argv:
            QTimer.singleShot(400, w.import_dialog)
        return app.exec()
    target = None
    if args:
        a = args[0]
        if a.startswith("file://"):
            from urllib.parse import unquote, urlparse
            a = unquote(urlparse(a).path)
        target = Path(a)
        if target.is_dir():
            first = siblings(target / "_")
            target = first[0] if first else None
    w = Viewer(target)
    w.show()
    if "--slideshow" in sys.argv and w.files:
        QTimer.singleShot(200, w.slideshow)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
