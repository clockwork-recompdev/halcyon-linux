"""Halcyon Photo Gallery, after Windows Photo Gallery in Vista: the library
of your pictures - a navigation tree (All Pictures, Recently Imported, Tags,
Date Taken, Ratings, Folders), thumbnails grouped by date, an Info pane with
stars, tags and a caption, the glass control bar, and Import from a camera
or memory card. Double-click opens a picture in the viewer.

Tags, ratings and captions live in Halcyon's own database (your files are
never rewritten)."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QImageReader, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QSlider, QSplitter, QStyle, QStyledItemDelegate,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import app as V

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "halcyon-photos"
DB = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "photo-gallery.json"
VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".3gp", ".m4v", ".mts", ".webm", ".mpg"}
MEDIA = V.EXTS | VIDEO
TEXT = V.TEXT
GRAY = "#6d7f95"
ROLE = Qt.UserRole + 1


def pictures_dir():
    try:
        d = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True).stdout.strip()
        if d and d != str(Path.home()):
            return Path(d)
    except OSError:
        pass
    return Path.home() / "Pictures"


def taken(path, mtime):
    if Path(path).suffix.lower() in (".jpg", ".jpeg", ".jfif"):
        t = V.exif(path).get("taken")
        if t:
            try:
                return time.mktime(time.strptime(t, "%Y:%m:%d %H:%M:%S"))
            except ValueError:
                pass
    m = re.search(r"(20\d\d|19\d\d)[-_]?(\d\d)[-_]?(\d\d)[-_ T]?(\d\d)?(\d\d)?", Path(path).stem)
    if m:                                   # IMG_20240612_153000.jpg and friends
        try:
            return time.mktime(time.strptime("".join(x or "00" for x in m.groups()), "%Y%m%d%H%M"))
        except ValueError:
            pass
    return mtime


# ------------------------------------------------------------------ library ---
class Library(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        try:
            self.db = json.loads(DB.read_text())
        except (OSError, ValueError):
            self.db = {}
        self.db.setdefault("folders", [str(pictures_dir())])
        self.db.setdefault("items", {})          # path -> {mtime, taken, video, imported?}
        self.db.setdefault("meta", {})           # path -> {rating, tags, caption}
        self.scanning = False

    def save(self):
        DB.parent.mkdir(parents=True, exist_ok=True)
        tmp = DB.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.db, ensure_ascii=False))
        tmp.replace(DB)

    def scan(self):
        if self.scanning:
            return
        self.scanning = True
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        old, found = self.db["items"], {}
        for folder in self.db["folders"]:
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in MEDIA or f.startswith("."):
                        continue
                    p = os.path.join(root, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    o = old.get(p)
                    if o and o.get("mtime") == st.st_mtime:
                        found[p] = o
                    else:
                        found[p] = {"mtime": st.st_mtime, "taken": taken(p, st.st_mtime), "video": ext in VIDEO,
                                    **({"imported": o["imported"]} if o and o.get("imported") else {})}
        self.db["items"] = found
        self.db["meta"] = {k: v for k, v in self.db["meta"].items() if k in found}
        self.save()
        self.scanning = False
        self.changed.emit()

    def meta(self, p):
        return self.db["meta"].get(p, {})

    def set_meta(self, paths, **kw):
        for p in paths:
            m = self.db["meta"].setdefault(p, {})
            for k, v in kw.items():
                if v in (None, "", [], 0):
                    m.pop(k, None)
                else:
                    m[k] = v
            if not m:
                self.db["meta"].pop(p, None)
        self.save()

    def tags(self):
        out = {}
        for m in self.db["meta"].values():
            for t in m.get("tags", []):
                out[t] = out.get(t, 0) + 1
        return out

    def items(self, view, arg=None, query=""):
        rows = []
        q = query.lower().strip()
        last_import = max((i.get("imported", 0) for i in self.db["items"].values()), default=0)
        for p, i in self.db["items"].items():
            m = self.meta(p)
            if view == "pictures" and i.get("video"):
                continue
            if view == "videos" and not i.get("video"):
                continue
            if view == "recent" and not (last_import and i.get("imported") == last_import):
                continue
            if view == "tag" and arg not in m.get("tags", []):
                continue
            if view == "untagged" and m.get("tags"):
                continue
            if view == "rating" and m.get("rating", 0) < arg:
                continue
            if view == "unrated" and m.get("rating"):
                continue
            if view == "year" and time.localtime(i["taken"]).tm_year != arg:
                continue
            if view == "month" and time.localtime(i["taken"])[:2] != arg:
                continue
            if view == "folder" and not p.startswith(arg.rstrip("/") + "/"):
                continue
            if q and q not in f"{os.path.basename(p)} {' '.join(m.get('tags', []))} {m.get('caption', '')}".lower():
                continue
            rows.append((p, i, m))
        rows.sort(key=lambda r: r[1]["taken"], reverse=True)
        return rows


# --------------------------------------------------------------- thumbnails ---
class _Emit(QObject):
    done = Signal(str, QImage)


class _Thumb(QRunnable):
    def __init__(self, path, video, emitter):
        super().__init__()
        self.path, self.video, self.emitter = path, video, emitter

    def run(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return
        key = hashlib.sha1(f"{self.path}:{st.st_mtime}".encode()).hexdigest()
        cached = CACHE / "thumbs" / f"{key}.jpg"
        img = QImage(str(cached)) if cached.exists() else QImage()
        if img.isNull():
            if self.video:
                tmp = cached.with_suffix(".png")
                cached.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "1", "-i", self.path, "-frames:v", "1",
                                "-vf", "scale=256:-1", str(tmp)], capture_output=True)
                img = QImage(str(tmp))
                tmp.unlink(missing_ok=True)
            else:
                r = QImageReader(self.path)
                r.setAutoTransform(True)
                s = r.size()
                if s.isValid():
                    r.setScaledSize(s.scaled(QSize(256, 256), Qt.KeepAspectRatio))
                img = r.read()
            if not img.isNull():
                cached.parent.mkdir(parents=True, exist_ok=True)
                img.save(str(cached), "JPG", 85)
        self.emitter.done.emit(self.path, img)


class Thumbs(QObject):
    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.images, self.pending = {}, set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.em = _Emit()
        self.em.done.connect(self._done)

    def get(self, path, video=False):
        if path in self.images:
            return self.images[path]
        if path not in self.pending:
            self.pending.add(path)
            self.pool.start(_Thumb(path, video, self.em))
        return None

    def _done(self, path, img):
        self.pending.discard(path)
        self.images[path] = QPixmap.fromImage(img) if not img.isNull() else None
        self.ready.emit(path)


class ThumbDelegate(QStyledItemDelegate):
    """A picture tile: the thumbnail on a soft frame, stars under it; group headers between days."""
    def __init__(self, gallery):
        super().__init__(gallery.grid)
        self.g = gallery

    def sizeHint(self, option, index):
        d = index.data(ROLE)
        s = self.g.thumb_size
        if d and d.get("header"):
            return QSize(self.g.grid.viewport().width() - 12, 30)
        return QSize(s + 18, s + 34)

    def paint(self, p, option, index):
        d = index.data(ROLE)
        if not d:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect
        if d.get("header"):
            f = QFont(option.font)
            f.setPointSizeF(f.pointSizeF() + 1.5)
            p.setFont(f)
            p.setPen(QColor(TEXT))
            p.drawText(r.adjusted(4, 4, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, d["title"])
            w = p.fontMetrics().horizontalAdvance(d["title"]) + 14
            p.setPen(QColor("#9fb6d3"))
            p.drawLine(r.left() + w, r.center().y() + 2, r.right() - 6, r.center().y() + 2)
            p.setPen(QColor(GRAY))
            p.setFont(option.font)
            p.restore()
            return
        sel = option.state & QStyle.State_Selected
        hover = option.state & QStyle.State_MouseOver
        box = QRectF(r).adjusted(3, 3, -3, -3)
        if sel or hover:
            p.setPen(QPen(QColor("#84acd8" if sel else "#c4d7ec"), 1))
            p.setBrush(QColor("#cfe3fa" if sel else "#eef5fd"))
            p.drawRoundedRect(box, 4, 4)
        s = self.g.thumb_size
        area = QRect(r.left() + 9, r.top() + 8, s, s)
        pm = self.g.thumbs.get(d["path"], d.get("video"))
        if pm:
            sz = pm.size().scaled(area.size(), Qt.KeepAspectRatio)
            tr = QRect(area.left() + (s - sz.width()) // 2, area.top() + (s - sz.height()) // 2, sz.width(), sz.height())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 40))
            p.drawRect(tr.translated(2, 2))
            p.drawPixmap(tr, pm)
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(tr)
            if d.get("video"):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 140))
                p.drawRect(QRect(tr.left(), tr.bottom() - 14, tr.width(), 14))
                p.setPen(QColor("white"))
                p.drawText(QRect(tr.left() + 4, tr.bottom() - 14, tr.width(), 14), Qt.AlignVCenter, "▶ video")
        else:
            p.setPen(QPen(QColor("#d5e0ec"), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(area.adjusted(10, 10, -10, -10))
        stars = d.get("rating", 0)
        if stars or sel or hover:
            p.setPen(Qt.NoPen)
            x0 = r.left() + (r.width() - 5 * 13) // 2
            for i in range(5):
                p.setBrush(QColor("#f2b01e") if i < stars else QColor(0, 0, 0, 25))
                star(p, x0 + i * 13 + 6, r.bottom() - 13, 5.5)
        p.restore()


def star(p, cx, cy, r):
    import math
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.45
        a = -math.pi / 2 + i * math.pi / 5
        pts.append(QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a)))
    p.drawPolygon(QPolygonF(pts))


class Stars(QWidget):
    rated = Signal(int)

    def __init__(self):
        super().__init__()
        self.value, self.hover = 0, 0
        self.setFixedSize(5 * 20 + 4, 20)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Rate this picture")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#b98a12"), 0.8))
        show = self.hover or self.value
        for i in range(5):
            p.setBrush(QColor("#f6c33a") if i < show else QColor("#e9eef4"))
            star(p, 10 + i * 20, 10, 8.5)

    def mouseMoveEvent(self, e):
        self.hover = min(5, int(e.position().x() // 20) + 1)
        self.update()

    def leaveEvent(self, e):
        self.hover = 0
        self.update()

    def mousePressEvent(self, e):
        v = min(5, int(e.position().x() // 20) + 1)
        self.value = 0 if v == self.value else v           # click the same star again to clear
        self.rated.emit(self.value)
        self.update()


# ------------------------------------------------------------------ import ---
def cameras():
    """Mounted cameras and memory cards: anything with a DCIM folder."""
    roots = [Path(f"/run/media/{os.environ.get('USER', '')}"), Path("/media") / os.environ.get("USER", ""), Path("/media"),
             Path("/mnt")]
    extra = os.environ.get("HALCYON_PHOTOS_CAMERA")
    out = [Path(extra)] if extra else []
    for r in roots:
        try:
            for d in r.iterdir():
                if (d / "DCIM").is_dir():
                    out.append(d)
        except OSError:
            continue
    return out


class ImportDialog(QDialog):
    """Import Pictures and Videos: copy everything new into Pictures/<date> <tag>."""
    def __init__(self, gallery, source):
        super().__init__(gallery)
        self.g, self.source = gallery, source
        self.setWindowTitle("Import Pictures and Videos")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        self.files = [p for p in (source / "DCIM").rglob("*") if p.suffix.lower() in MEDIA and p.is_file()]
        have = {(os.path.basename(p), i["mtime"]) for p, i in gallery.lib.db["items"].items()}
        self.new = [p for p in self.files if (p.name, p.stat().st_mtime) not in have]
        lay.addWidget(QLabel(f"<b>{len(self.new)}</b> new picture(s) and video(s) found on "
                             f"<b>{source.name}</b>" + (f" ({len(self.files) - len(self.new)} already imported)"
                                                         if len(self.files) > len(self.new) else "") + "."))
        lay.addWidget(QLabel("Tag these pictures (optional):"))
        self.tag = QLineEdit(placeholderText="e.g. Beach trip")
        lay.addWidget(self.tag)
        self.erase = QCheckBox("Erase after importing")
        lay.addWidget(self.erase)
        self.bar = QProgressBar()
        self.bar.hide()
        lay.addWidget(self.bar)
        self.bb = QDialogButtonBox()
        self.go_btn = self.bb.addButton("Import", QDialogButtonBox.AcceptRole)
        self.bb.addButton(QDialogButtonBox.Cancel)
        self.go_btn.setEnabled(bool(self.new))
        self.bb.accepted.connect(self.run)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)

    def run(self):
        tag = self.tag.text().strip()
        dest = Path(self.g.lib.db["folders"][0]) / (time.strftime("%Y-%m-%d") + (f" {tag}" if tag else ""))
        dest.mkdir(parents=True, exist_ok=True)
        self.bar.setRange(0, len(self.new))
        self.bar.show()
        self.go_btn.setEnabled(False)
        stamp = time.time()
        copied = []
        for i, src in enumerate(self.new):
            target = dest / src.name
            n = 1
            while target.exists():
                target = dest / f"{src.stem} ({n}){src.suffix}"
                n += 1
            shutil.copy2(src, target)
            copied.append(str(target))
            self.bar.setValue(i + 1)
            QApplication.processEvents()
        for p in copied:
            st = os.stat(p)
            self.g.lib.db["items"][p] = {"mtime": st.st_mtime, "taken": taken(p, st.st_mtime),
                                         "video": Path(p).suffix.lower() in VIDEO, "imported": stamp}
        if tag:
            self.g.lib.set_meta(copied, tags=[tag])
        self.g.lib.save()
        if self.erase.isChecked():
            for src in self.new:
                try:
                    src.unlink()
                except OSError:
                    pass
        self.accept()


# ------------------------------------------------------------------ window ---
QSS = V.QSS + f"""
QTreeWidget#nav {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f6f9fd, stop:1 #e9f0f8);
    border: none; border-right: 1px solid #c5d6e8; color: {TEXT}; outline: 0; }}
QTreeWidget#nav::item {{ padding: 3px 2px; border: 1px solid transparent; }}
QTreeWidget#nav::item:hover {{ background: #e5effa; border-color: #c8dcf2; border-radius: 2px; }}
QTreeWidget#nav::item:selected {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dcebfc,
    stop:1 #c1dbfc); border-color: #7da2ce; border-radius: 2px; color: black; }}
QListWidget#grid {{ background: white; border: none; outline: 0; }}
QLineEdit#find {{ border: 1px solid #a7bdd6; border-radius: 2px; padding: 3px 6px; background: white; color: black; }}
QLineEdit#tagbox, QLineEdit#caption {{ border: 1px solid #c5d6e8; padding: 2px 4px; background: white; color: black; }}
"""


class Gallery(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Halcyon Photo Gallery")
        self.setWindowIcon(QIcon.fromTheme("image-x-generic"))
        self.resize(1180, 760)
        self.lib = Library()
        self.lib.changed.connect(self.rebuild)
        self.thumbs = Thumbs()
        self.thumbs.ready.connect(lambda _: self.grid.viewport().update())
        self.thumb_size = 132
        self.view = ("all", None)
        self.rows = []

        w = QWidget()
        self.setCentralWidget(w)
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        top = V.Bar(34, bottom=False)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(6, 2, 6, 2)
        tl.setSpacing(2)
        file_m = QMenu(self)
        file_m.addAction("Import from Camera or Scanner…", self.import_dialog, QKeySequence("Ctrl+I"))
        file_m.addAction("Add Folder to Gallery…", self.add_folder)
        file_m.addSeparator()
        file_m.addAction("Open File Location", lambda: self.selected() and V.show_in_folder(self.selected()[0]))
        file_m.addAction("Exit", self.close)
        for text, slot, menu in (("File", None, file_m), ("Fix", lambda: self.open_viewer(), None),
                                 ("Info", self.toggle_info, None), ("Print", self.print_selected, None),
                                 ("E-mail", self.email, None), ("Slide Show", self.slideshow, None)):
            b = QToolButton(text=text + (" ▾" if menu else ""), autoRaise=True)
            if menu:
                b.setMenu(menu)
                b.setPopupMode(QToolButton.InstantPopup)
            if slot:
                b.clicked.connect(slot)
            tl.addWidget(b)
            if text == "Info":
                self.info_btn = b
                b.setCheckable(True)
                b.setChecked(True)
        tl.addStretch(1)
        self.find = QLineEdit(objectName="find", placeholderText="Search")
        self.find.setFixedWidth(220)
        self.find.textChanged.connect(lambda _: self.fill())
        tl.addWidget(self.find)
        col.addWidget(top)

        split = QSplitter(Qt.Horizontal, handleWidth=1, childrenCollapsible=False)
        self.nav = QTreeWidget(objectName="nav", headerHidden=True, indentation=14)
        self.nav.setIconSize(QSize(18, 18))
        self.nav.itemClicked.connect(self.nav_clicked)
        self.nav.setContextMenuPolicy(Qt.CustomContextMenu)
        self.nav.customContextMenuRequested.connect(self.nav_menu)
        split.addWidget(self.nav)
        self.grid = QListWidget(objectName="grid", viewMode=QListWidget.IconMode, resizeMode=QListWidget.Adjust,
                                movement=QListWidget.Static, selectionMode=QAbstractItemView.ExtendedSelection,
                                spacing=2, wrapping=True)
        self.grid.setMouseTracking(True)
        self.grid.setItemDelegate(ThumbDelegate(self))
        self.grid.itemDoubleClicked.connect(lambda it: self.open_viewer(it))
        self.grid.itemSelectionChanged.connect(self.update_info)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.grid_menu)
        split.addWidget(self.grid)
        self.info = QFrame(objectName="info")
        self.info.setFixedWidth(250)
        il = QVBoxLayout(self.info)
        il.setContentsMargins(12, 12, 12, 12)
        il.setSpacing(6)
        self.info_title = QLabel(f"<span style='color:{TEXT}; font-size:12pt'>Info</span>")
        il.addWidget(self.info_title)
        self.info_preview = QLabel(alignment=Qt.AlignCenter)
        il.addWidget(self.info_preview)
        self.info_text = QLabel(wordWrap=True, textFormat=Qt.RichText)
        il.addWidget(self.info_text)
        self.stars = Stars()
        self.stars.rated.connect(self.rate)
        il.addWidget(self.stars)
        il.addWidget(QLabel(f"<span style='color:{GRAY}'>Tags</span>"))
        self.tag_list = QLabel(wordWrap=True, textFormat=Qt.RichText)
        self.tag_list.linkActivated.connect(self.remove_tag)
        il.addWidget(self.tag_list)
        self.tag_box = QLineEdit(objectName="tagbox", placeholderText="Add Tags")
        self.tag_box.returnPressed.connect(self.add_tag)
        il.addWidget(self.tag_box)
        il.addWidget(QLabel(f"<span style='color:{GRAY}'>Caption</span>"))
        self.caption = QLineEdit(objectName="caption", placeholderText="Add Caption")
        self.caption.editingFinished.connect(self.set_caption)
        il.addWidget(self.caption)
        il.addStretch(1)
        split.addWidget(self.info)
        split.setSizes([210, 720, 250])
        split.setStretchFactor(1, 1)
        col.addWidget(split, 1)

        bottom = V.Bar(52, bottom=True)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(12, 4, 12, 4)
        zl = QLabel()
        zl.setPixmap(V.glyph("zoom").pixmap(20))
        bl.addWidget(zl)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(64, 256)
        self.size_slider.setValue(self.thumb_size)
        self.size_slider.setFixedWidth(120)
        self.size_slider.valueChanged.connect(self.resize_thumbs)
        bl.addWidget(self.size_slider)
        bl.addStretch(1)
        self.play = V.PlayOrb()
        self.play.setFixedSize(42, 42)
        self.play.clicked.connect(self.slideshow)
        for kind, tip, fn in (("rotl", "Rotate counterclockwise", lambda: self.rotate(-90)),):
            b = QToolButton(icon=V.glyph(kind), toolTip=tip, autoRaise=True)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addSpacing(10)
        bl.addWidget(self.play)
        bl.addSpacing(10)
        for kind, tip, fn in (("rotr", "Rotate clockwise", lambda: self.rotate(90)),
                              ("delete", "Delete", self.delete)):
            b = QToolButton(icon=V.glyph(kind), toolTip=tip, autoRaise=True)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addStretch(1)
        self.count = QLabel()
        self.count.setStyleSheet(f"color: {TEXT};")
        bl.addWidget(self.count)
        col.addWidget(bottom)

        for key, fn in (("Return", lambda: self.open_viewer()), ("Delete", self.delete), ("F11", self.slideshow),
                        ("Ctrl+E", self.find.setFocus), ("Ctrl+T", self.tag_box.setFocus), ("Ctrl+.", lambda: self.rotate(90)),
                        ("Ctrl+,", lambda: self.rotate(-90)), ("Ctrl+A", self.grid.selectAll),
                        *[(f"Ctrl+{n}", (lambda n=n: self.rate(n))) for n in range(0, 6)]):
            a = QAction(self, shortcut=QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
        self.rebuild()
        self.lib.scan()
        QTimer.singleShot(1500, self.offer_import)

    # --- navigation tree
    def rebuild(self):
        cur = self.view
        self.nav.clear()

        def node(parent, text, icon, view, arg=None):
            it = QTreeWidgetItem([text])
            it.setIcon(0, QIcon.fromTheme(icon))
            it.setData(0, ROLE, (view, arg))
            (parent.addChild(it) if parent else self.nav.addTopLevelItem(it))
            if (view, arg) == cur:
                self.nav.setCurrentItem(it)
            return it
        items = self.lib.db["items"]
        allp = node(None, "All Pictures and Videos", "folder-image", "all")
        node(allp, "Pictures", "image-x-generic", "pictures")
        node(allp, "Videos", "video-x-generic", "videos")
        allp.setExpanded(True)
        if any(i.get("imported") for i in items.values()):
            node(None, "Recently Imported", "document-import", "recent")
        tags = node(None, "Tags", "tag", "tags")
        node(tags, "Create a New Tag", "list-add", "newtag")
        node(tags, "Not Tagged", "tag", "untagged")
        for t, n in sorted(self.lib.tags().items(), key=lambda kv: kv[0].lower()):
            node(tags, f"{t}  ({n})", "tag", "tag", t)
        tags.setExpanded(True)
        dates = node(None, "Date Taken", "view-calendar", "dates")
        years = {}
        for i in items.values():
            t = time.localtime(i["taken"])
            years.setdefault(t.tm_year, set()).add(t.tm_mon)
        for y in sorted(years, reverse=True):
            yi = node(dates, str(y), "view-calendar-month", "year", y)
            for m in sorted(years[y], reverse=True):
                node(yi, time.strftime("%B", (y, m, 1, 0, 0, 0, 0, 1, -1)), "view-calendar-day", "month", (y, m))
        dates.setExpanded(True)
        ratings = node(None, "Ratings", "rating", "ratings")
        for n in range(5, 0, -1):
            node(ratings, ("★" * n) + ("☆" * (5 - n)) + (" and up" if n < 5 else ""), "rating", "rating", n)
        node(ratings, "Not Rated", "rating-unrated", "unrated")
        folders = node(None, "Folders", "folder", "folders")
        for f in self.lib.db["folders"]:
            fi = node(folders, Path(f).name or f, "folder-pictures", "folder", f)
            subs = sorted({str(Path(p).parent) for p in items if str(Path(p).parent).startswith(f) and
                           str(Path(p).parent) != f})
            for s in subs:
                if Path(s).parent == Path(f):
                    node(fi, Path(s).name, "folder", "folder", s)
            fi.setExpanded(True)
        folders.setExpanded(True)
        if self.nav.currentItem() is None:
            self.nav.setCurrentItem(allp)
            self.view = ("all", None)
        self.fill()

    def nav_clicked(self, it, *_):
        view, arg = it.data(0, ROLE)
        if view == "newtag":
            return self.tag_box.setFocus()
        if view in ("tags", "dates", "ratings", "folders"):
            it.setExpanded(not it.isExpanded())
            return
        self.view = (view, arg)
        self.fill()

    def nav_menu(self, pos):
        it = self.nav.itemAt(pos)
        if not it:
            return
        view, arg = it.data(0, ROLE)
        m = QMenu(self)
        if view == "tag":
            m.addAction("Rename tag…", lambda: self.rename_tag(arg))
            m.addAction("Delete tag", lambda: self.delete_tag(arg))
        elif view == "folders" or view == "folder":
            m.addAction("Add Folder to Gallery…", self.add_folder)
            if view == "folder" and arg in self.lib.db["folders"] and len(self.lib.db["folders"]) > 1:
                m.addAction("Remove from Gallery", lambda: (self.lib.db["folders"].remove(arg), self.lib.scan()))
        else:
            return
        m.exec(self.nav.viewport().mapToGlobal(pos))

    # --- the grid
    def fill(self):
        view, arg = self.view
        self.rows = self.lib.items(view, arg, self.find.text())
        keep = set(self.selected())
        self.grid.clear()
        group = None
        for p, i, m in self.rows:
            label = time.strftime("%B %-d, %Y", time.localtime(i["taken"]))
            if label != group:
                group = label
                h = QListWidgetItem()
                h.setData(ROLE, {"header": True, "title": label})
                h.setFlags(Qt.NoItemFlags)
                h.setSizeHint(QSize(self.grid.viewport().width() - 12, 30))
                self.grid.addItem(h)
            it = QListWidgetItem()
            it.setData(ROLE, {"path": p, "video": i.get("video"), "rating": m.get("rating", 0)})
            it.setToolTip(os.path.basename(p))
            self.grid.addItem(it)
            if p in keep:
                it.setSelected(True)
        n = len(self.rows)
        self.count.setText(f"{n} item{'s' if n != 1 else ''}" if n else
                           ("Looking for pictures…" if self.lib.scanning else "No pictures here"))
        self.update_info()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self.refit_headers)

    def refit_headers(self):
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if (it.data(ROLE) or {}).get("header"):
                it.setSizeHint(QSize(self.grid.viewport().width() - 12, 30))

    def resize_thumbs(self, v):
        self.thumb_size = v
        self.grid.doItemsLayout()
        self.grid.viewport().update()

    def selected(self):
        return [it.data(ROLE)["path"] for it in self.grid.selectedItems() if it.data(ROLE) and not it.data(ROLE).get("header")]

    def visible_paths(self):
        return [p for p, i, m in self.rows if not i.get("video")]

    def grid_menu(self, pos):
        if not self.selected():
            return
        m = QMenu(self)
        m.addAction("Preview", lambda: self.open_viewer())
        m.addAction("Play Slide Show", self.slideshow)
        rate = m.addMenu("Rate")
        for n in range(5, -1, -1):
            rate.addAction("★" * n + "☆" * (5 - n) if n else "Clear rating", lambda n=n: self.rate(n))
        m.addAction("Add Tags…", self.tag_box.setFocus)
        m.addSeparator()
        m.addAction("Rotate Clockwise", lambda: self.rotate(90))
        m.addAction("Rotate Counterclockwise", lambda: self.rotate(-90))
        m.addAction("Set as Desktop Background", lambda: subprocess.Popen(
            ["plasma-apply-wallpaperimage", self.selected()[0]], start_new_session=True))
        m.addAction("E-mail", self.email)
        m.addSeparator()
        m.addAction("Delete", self.delete)
        m.exec(self.grid.viewport().mapToGlobal(pos))

    # --- info pane
    def toggle_info(self):
        self.info.setVisible(not self.info.isVisible())
        self.info_btn.setChecked(self.info.isVisible())

    def update_info(self):
        sel = self.selected()
        for w_ in (self.stars, self.tag_box, self.caption):
            w_.setEnabled(bool(sel))
        if not sel:
            self.info_preview.clear()
            self.info_text.setText(f"<span style='color:{GRAY}'>{len(self.rows)} items</span>")
            self.tag_list.setText("")
            self.caption.setText("")
            self.stars.value = 0
            self.stars.update()
            return
        metas = [self.lib.meta(p) for p in sel]
        if len(sel) == 1:
            p = sel[0]
            i = self.lib.db["items"].get(p, {})
            pm = self.thumbs.get(p, i.get("video"))
            self.info_preview.setPixmap(pm.scaled(220, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation) if pm else QPixmap())
            try:
                size = V.human(os.stat(p).st_size)
            except OSError:
                size = ""
            cam = V.describe_exif(V.exif(p)) if p.lower().endswith((".jpg", ".jpeg")) else []
            rows = [os.path.basename(p), time.strftime("%-m/%-d/%Y %-I:%M %p", time.localtime(i.get("taken", 0))), size]
            rows += [v for _, v in cam]
            self.info_text.setText("<br>".join(f"<span style='color:#1e1e1e'>{x}</span>" if k == 0 else
                                               f"<span style='color:{GRAY}'>{x}</span>" for k, x in enumerate(rows) if x))
        else:
            self.info_preview.clear()
            self.info_text.setText(f"<b>{len(sel)} items selected</b>")
        ratings = {m.get("rating", 0) for m in metas}
        self.stars.value = ratings.pop() if len(ratings) == 1 else 0
        self.stars.update()
        common = set(metas[0].get("tags", []))
        for m in metas[1:]:
            common &= set(m.get("tags", []))
        self.tag_list.setText(" ".join(f"<span style='background:#e3eefa'>&nbsp;{t}&nbsp;<a href='{t}' "
                                       f"style='color:#c6282d;text-decoration:none'>×</a></span>"
                                       for t in sorted(common)) or f"<span style='color:{GRAY}'>(none)</span>")
        caps = {m.get("caption", "") for m in metas}
        self.caption.setText(caps.pop() if len(caps) == 1 else "")

    def rate(self, n):
        sel = self.selected()
        if not sel:
            return
        self.lib.set_meta(sel, rating=n)
        for it in self.grid.selectedItems():
            d = it.data(ROLE)
            d["rating"] = n
            it.setData(ROLE, d)
        self.update_info()
        self.grid.viewport().update()

    def add_tag(self):
        tags = [t.strip() for t in self.tag_box.text().split(";") if t.strip()]
        sel = self.selected()
        if not tags or not sel:
            return
        for p in sel:
            cur = self.lib.meta(p).get("tags", [])
            self.lib.set_meta([p], tags=sorted(set(cur) | set(tags), key=str.lower))
        self.tag_box.clear()
        self.rebuild()

    def remove_tag(self, tag):
        for p in self.selected():
            self.lib.set_meta([p], tags=[t for t in self.lib.meta(p).get("tags", []) if t != tag])
        self.rebuild()

    def rename_tag(self, tag):
        from PySide6.QtWidgets import QInputDialog
        new, ok = QInputDialog.getText(self, "Rename Tag", "Tag:", text=tag)
        if ok and new.strip():
            for p, m in list(self.lib.db["meta"].items()):
                if tag in m.get("tags", []):
                    self.lib.set_meta([p], tags=sorted({new.strip() if t == tag else t for t in m["tags"]}))
            self.view = ("tag", new.strip())
            self.rebuild()

    def delete_tag(self, tag):
        for p, m in list(self.lib.db["meta"].items()):
            if tag in m.get("tags", []):
                self.lib.set_meta([p], tags=[t for t in m["tags"] if t != tag])
        self.view = ("all", None)
        self.rebuild()

    def set_caption(self):
        sel = self.selected()
        if sel:
            self.lib.set_meta(sel, caption=self.caption.text().strip())

    # --- actions
    def open_viewer(self, it=None):
        paths = self.visible_paths()
        sel = [it.data(ROLE)["path"]] if it is not None and it.data(ROLE) else self.selected()
        if not sel:
            return
        if self.lib.db["items"].get(sel[0], {}).get("video"):
            subprocess.Popen(["xdg-open", sel[0]], start_new_session=True)
            return
        v = V.Viewer(Path(sel[0]))
        v.files = [Path(p) for p in paths] or v.files
        v.show_index(v.files.index(Path(sel[0])) if Path(sel[0]) in v.files else 0)
        v.resize(1000, 720)
        v.show()
        self._viewer = v

    def slideshow(self):
        paths = [Path(p) for p in (self.selected() if len(self.selected()) > 1 else self.visible_paths())]
        if not paths:
            return
        host = type("Host", (), {})()
        host.files, host.index = paths, 0
        host.show_index = lambda i: None
        self._show = V.SlideShow(host)
        self._show.showFullScreen()

    def rotate(self, deg):
        from PySide6.QtGui import QTransform
        errors = []
        for p in self.selected():
            if self.lib.db["items"].get(p, {}).get("video"):
                continue
            err = V.transform_file(p, QTransform().rotate(deg), ["-rotate", str(deg % 360)])
            if err:
                errors.append(f"{os.path.basename(p)}: {err}")
            self.thumbs.images.pop(p, None)
        if errors:
            QMessageBox.warning(self, "Rotate", "\n".join(errors[:5]))
        self.lib.scan()
        self.grid.viewport().update()

    def delete(self):
        sel = self.selected()
        if not sel or QMessageBox.question(self, "Delete File", f"Move {len(sel)} item(s) to the Trash?") != QMessageBox.Yes:
            return
        from PySide6.QtCore import QFile
        for p in sel:
            if QFile.moveToTrash(p):
                self.lib.db["items"].pop(p, None)
                self.lib.db["meta"].pop(p, None)
        self.lib.save()
        self.rebuild()

    def email(self):
        sel = self.selected()
        if sel:
            cmd = ["halcyon-mail", "--attach", *sel] if shutil.which("halcyon-mail") else ["xdg-email", "--attach", sel[0]]
            subprocess.Popen(cmd, start_new_session=True)

    def print_selected(self):
        sel = self.selected()
        if sel:
            v = V.Viewer(Path(sel[0]))
            v.print_photo()

    def add_folder(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Add Folder to Gallery", str(Path.home()))
        if d and d not in self.lib.db["folders"]:
            self.lib.db["folders"].append(d)
            self.lib.save()
            self.lib.scan()

    def import_dialog(self, source=None):
        cams = [source] if source else cameras()
        if not cams:
            QMessageBox.information(self, "Import Pictures and Videos",
                                    "Connect a camera or put in a memory card, then try again.")
            return
        d = ImportDialog(self, cams[0])
        if d.exec():
            self.view = ("recent", None)
            self.rebuild()

    def offer_import(self):
        cams = cameras()
        if cams and QMessageBox.question(self, "Import Pictures and Videos",
                                         f"Import pictures and videos from {cams[0].name}?") == QMessageBox.Yes:
            self.import_dialog(cams[0])


def main():
    import sys
    from PySide6.QtGui import QFontDatabase
    app = QApplication(sys.argv[:1])
    app.setApplicationName("halcyon-photos")
    app.setDesktopFileName("halcyon-photos")
    app.setStyle("Fusion")
    app.setPalette(V.light_palette())
    app.setStyleSheet(QSS)
    f = app.font()
    for fam in ("Segoe UI", "Selawik", "Noto Sans"):
        if fam in QFontDatabase.families():
            f.setFamily(fam)
            break
    f.setPointSizeF(9)
    app.setFont(f)
    w = Gallery()
    w.show()
    if "--import" in sys.argv:
        QTimer.singleShot(400, w.import_dialog)
    return app.exec()
