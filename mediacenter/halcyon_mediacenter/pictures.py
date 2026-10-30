"""Pictures for Media Center: folders and images under the user's Pictures
folder, with thumbnails made in the background (and cached on disk)."""
import hashlib
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader

EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".avif"}
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "halcyon-mediacenter" / "thumbs"


def pictures_dir():
    try:
        d = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True).stdout.strip()
        if d and d != str(Path.home()):
            return Path(d)
    except OSError:
        pass
    return Path.home() / "Pictures"


def listing(folder):
    """[(kind 'folder'/'picture', Path)] for one folder: subfolders first, newest pictures first."""
    folder = Path(folder)
    dirs, pics = [], []
    try:
        for e in os.scandir(folder):
            if e.name.startswith("."):
                continue
            p = Path(e.path)
            if e.is_dir():
                if any(f.suffix.lower() in EXTS for f in _walk_some(p)):
                    dirs.append(p)
            elif p.suffix.lower() in EXTS:
                pics.append(p)
    except OSError:
        pass
    dirs.sort(key=lambda p: p.name.lower())
    pics.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [("folder", d) for d in dirs] + [("picture", p) for p in pics]


def _walk_some(folder, limit=400):
    n = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            yield Path(root) / f
            n += 1
            if n > limit:
                return


def all_pictures(folder, limit=2000):
    out = [p for p in _walk_some(folder, limit * 3) if p.suffix.lower() in EXTS]
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def first_picture(folder):
    return next((p for p in _walk_some(folder) if p.suffix.lower() in EXTS), None)


class _Emitter(QObject):
    done = Signal(str, QImage, int)


class _Job(QRunnable):
    def __init__(self, path, size, emitter):
        super().__init__()
        self.path, self.size, self.emitter = path, size, emitter

    def run(self):
        try:
            stamp = self.path.stat().st_mtime
        except OSError:
            stamp = 0
        key = hashlib.sha1(f"{self.path}:{stamp}:{self.size}".encode()).hexdigest()
        cached = CACHE / f"{key}.jpg"
        img = QImage(str(cached)) if cached.exists() else QImage()
        if img.isNull():
            r = QImageReader(str(self.path))
            r.setAutoTransform(True)            # phone photos: honour the rotation
            s = r.size()
            if s.isValid():
                r.setScaledSize(s.scaled(QSize(self.size, self.size), Qt.KeepAspectRatioByExpanding))
            img = r.read()
            if not img.isNull():
                CACHE.mkdir(parents=True, exist_ok=True)
                img.save(str(cached), "JPG", 85)
        self.emitter.done.emit(str(self.path), img, self.size)


class Thumbnails(QObject):
    """Scaled pictures made off the UI thread; `ready` fires as each arrives."""
    ready = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.images, self.pending = {}, set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.emitter = _Emitter()
        self.emitter.done.connect(self._store)

    def get(self, path, size=320):
        key = (str(path), size)
        if key in self.images:
            return self.images[key]
        if key not in self.pending:
            self.pending.add(key)
            self.pool.start(_Job(Path(path), size, self.emitter))
        return None

    def _store(self, path, img, size):
        self.pending.discard((path, size))
        if len(self.images) > 800:
            self.images.clear()
        self.images[(path, size)] = img
        self.ready.emit(path)
