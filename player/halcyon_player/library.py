"""Halcyon Player's library ("full mode"), after Windows Media Player 11:
black glass across the top with Now Playing / Library / Rip / Burn, a
navigation pane (Recently Added, Artist, Album, Songs, Genre, Year,
Playlists), album art tiles, the list pane on the right, and the
transport bar with the big round play button along the bottom.

    halcyon-player --library

Music comes from the Music folder (and any folders added in Options);
tags are read with mutagen and cached."""
import hashlib
import json
import locale
import os
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QFontDatabase, QIcon, QImage, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QSlider, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

AUDIO = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma", ".wav", ".aiff", ".aif", ".ape", ".wv",
         ".mpc", ".alac", ".mka"}
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "halcyon-player"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "halcyon-player" / "library.json"
APP = "Halcyon Player"
BLUE = "#7fc4ff"


def music_dir():
    try:
        d = subprocess.run(["xdg-user-dir", "MUSIC"], capture_output=True, text=True).stdout.strip()
        if d and d != str(Path.home()):
            return Path(d)
    except OSError:
        pass
    return Path.home() / "Music"


def load_cfg():
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("folders", [str(music_dir())])
    cfg.setdefault("playlists", {})
    cfg.setdefault("volume", 80)
    cfg.setdefault("plays", {})
    return cfg


def save_cfg(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1))


def fmt_time(s):
    s = int(s or 0)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ------------------------------------------------------------------- tags ---
def _first(v):
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v).strip() if v is not None else ""


def read_tags(path):
    """{title, artist, albumartist, album, track, disc, year, genre, length} - mutagen if it's
    installed, otherwise names from the folder and file."""
    p = Path(path)
    t = {"title": "", "artist": "", "albumartist": "", "album": "", "track": 0, "disc": 1, "year": "",
         "genre": "", "length": 0}
    try:
        import mutagen
        f = mutagen.File(p, easy=True)
        if f is not None:
            tags = f.tags or {}
            get = lambda k: _first(tags.get(k)) if hasattr(tags, "get") else ""
            t.update(title=get("title"), artist=get("artist"), albumartist=get("albumartist"), album=get("album"),
                     genre=get("genre"), year=get("date")[:4] or get("year")[:4])
            for key, field in (("tracknumber", "track"), ("discnumber", "disc")):
                m = re.match(r"\d+", get(key))
                if m:
                    t[field] = int(m.group())
            t["length"] = float(getattr(f.info, "length", 0) or 0)
    except Exception:
        pass
    if not t["title"]:
        stem = re.sub(r"^\d+[\s.\-_]+", "", p.stem)
        if " - " in stem and not t["artist"]:
            t["artist"], stem = stem.split(" - ", 1)
        t["title"] = stem
    if not t["track"]:
        m = re.match(r"(\d+)", p.stem)
        t["track"] = int(m.group(1)) if m else 0
    if not t["album"]:
        t["album"] = p.parent.name
    if not t["artist"]:
        t["artist"] = p.parent.parent.name if p.parent.parent != p.parent else "Unknown Artist"
    return t


def embedded_cover(path):
    """Album art inside the file (ID3 APIC, MP4 covr, FLAC/Vorbis pictures), as bytes."""
    try:
        import mutagen
        f = mutagen.File(path)
        if f is None:
            return None
        if hasattr(f, "pictures") and f.pictures:
            return f.pictures[0].data
        tags = f.tags
        if tags is None:
            return None
        for k in list(tags.keys()):
            if str(k).startswith("APIC"):
                return tags[k].data
        if "covr" in tags and tags["covr"]:
            return bytes(tags["covr"][0])
        if "metadata_block_picture" in tags:
            import base64
            from mutagen.flac import Picture
            return Picture(base64.b64decode(tags["metadata_block_picture"][0])).data
    except Exception:
        return None
    return None


COVER_NAMES = ("folder.jpg", "cover.jpg", "front.jpg", "album.jpg", "folder.png", "cover.png", "albumartsmall.jpg")


def cover_path(album_key, sample_file):
    """A cached, square-ish cover for an album, or None."""
    out = CACHE / "covers" / (hashlib.sha1(album_key.encode()).hexdigest() + ".jpg")
    if out.exists():
        return str(out)
    d = Path(sample_file).parent
    src = None
    try:
        names = {n.lower(): n for n in os.listdir(d)}
        for n in COVER_NAMES:
            if n in names:
                src = QImage(str(d / names[n]))
                break
    except OSError:
        pass
    if src is None or src.isNull():
        data = embedded_cover(sample_file)
        if data:
            src = QImage.fromData(data)
    if src is None or src.isNull():
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    src.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(out), "JPG", 88)
    return str(out)


class Library(QObject):
    """All the songs, scanned in the background and cached by modification time."""
    changed = Signal()
    progress = Signal(int)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.tracks = []
        self.db_path = CACHE / "library.json"
        try:
            self.tracks = list(json.loads(self.db_path.read_text()).values())
        except (OSError, ValueError):
            self.tracks = []
        self.scanning = False

    def scan(self):
        if self.scanning:
            return
        self.scanning = True
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        old = {t["path"]: t for t in self.tracks}
        found, n = {}, 0
        for folder in self.cfg["folders"]:
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    p = os.path.join(root, f)
                    if os.path.splitext(f)[1].lower() not in AUDIO:
                        continue
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    t = old.get(p)
                    if not t or t.get("mtime") != st.st_mtime:
                        t = dict(read_tags(p), path=p, mtime=st.st_mtime, added=t.get("added") if t else time.time())
                    found[p] = t
                    n += 1
                    if n % 200 == 0:
                        self.progress.emit(n)
        for t in found.values():
            key = album_key(t)
            if "cover" not in t or (t["cover"] and not os.path.exists(t["cover"])):
                t["cover"] = cover_path(key, t["path"]) or ""
        # one cover per album: share it with tracks that had none
        covers = {}
        for t in found.values():
            if t.get("cover"):
                covers.setdefault(album_key(t), t["cover"])
        for t in found.values():
            if not t.get("cover"):
                t["cover"] = covers.get(album_key(t), "")
        self.tracks = list(found.values())
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path.write_text(json.dumps(found))
        except OSError:
            pass
        self.scanning = False
        self.changed.emit()

    def albums(self):
        groups = {}
        for t in self.tracks:
            groups.setdefault(album_key(t), []).append(t)
        out = []
        for key, ts in groups.items():
            ts.sort(key=lambda t: (t.get("disc", 1), t.get("track", 0), t["title"].lower()))
            a = ts[0]
            out.append({"key": key, "album": a["album"], "artist": a.get("albumartist") or a["artist"],
                        "year": next((t["year"] for t in ts if t.get("year")), ""),
                        "genre": next((t["genre"] for t in ts if t.get("genre")), ""),
                        "cover": next((t["cover"] for t in ts if t.get("cover")), ""), "tracks": ts,
                        "added": max(t.get("added", 0) for t in ts)})
        return out


def album_key(t):
    return f"{(t.get('albumartist') or t['artist']).lower()}\x00{t['album'].lower()}"


# --------------------------------------------------------------- playback ---
class Engine(QObject):
    """mpv for audio, with our own play queue."""
    position = Signal(float, float)
    state = Signal(bool)
    track = Signal(object)
    finished = Signal()              # a song played to its end (sent from mpv's thread)

    def __init__(self):
        super().__init__()
        locale.setlocale(locale.LC_NUMERIC, "C")
        import mpv
        args = dict(vo="null", video=False, idle=True, ytdl=False)
        for kv in os.environ.get("HALCYON_PLAYER_MPV", "").split():
            k, _, v = kv.lstrip("-").partition("=")
            args[k.replace("-", "_")] = v or True
        self.mpv = mpv.MPV(**args)
        self.queue, self.index = [], -1
        self.shuffle, self.repeat = False, False
        self._pos, self._dur = 0.0, 0.0
        self.mpv.observe_property("time-pos", lambda n, v: self._time(v, None))
        self.mpv.observe_property("duration", lambda n, v: self._time(None, v))
        self.mpv.observe_property("pause", lambda n, v: self.state.emit(not v and self.index >= 0))
        self.mpv.event_callback("end-file")(self._ended)
        self.finished.connect(lambda: self.next(auto=True))

    def _time(self, pos, dur):
        if pos is not None:
            self._pos = pos
        if dur is not None:
            self._dur = dur
        self.position.emit(self._pos, self._dur)

    def _ended(self, event):
        data = getattr(event, "data", None)
        reason = getattr(data, "reason", None)
        if reason is None and isinstance(event, dict):
            reason = (event.get("event") or {}).get("reason")
        if reason in (0, "eof") or str(reason).lower().endswith("eof"):
            self.finished.emit()

    def play_list(self, tracks, start=0):
        self.queue = list(tracks)
        self.play_index(start)

    def play_index(self, i):
        if not self.queue:
            return
        self.index = i % len(self.queue)
        t = self.queue[self.index]
        self.mpv.play(t["path"])
        self.mpv.pause = False
        self.track.emit(t)
        self.state.emit(True)          # pause was already off while idle, so mpv won't say so

    def current(self):
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def toggle(self):
        if self.index < 0:
            return
        self.mpv.pause = not self.mpv.pause

    def stop(self):
        self.mpv.command("stop")
        self.state.emit(False)
        self.position.emit(0, 0)

    def next(self, auto=False):
        if not self.queue:
            return
        if self.shuffle and len(self.queue) > 1:
            choices = [i for i in range(len(self.queue)) if i != self.index]
            return self.play_index(random.choice(choices))
        if self.index + 1 >= len(self.queue) and auto and not self.repeat:
            self.state.emit(False)
            return
        self.play_index(self.index + 1)

    def previous(self):
        if self._pos > 3:
            self.mpv.seek(0, "absolute")
        elif self.queue:
            self.play_index(self.index - 1)

    def seek(self, s):
        try:
            self.mpv.seek(s, "absolute")
        except Exception:
            pass

    def volume(self, v):
        self.mpv.volume = v

    def mute(self, on):
        self.mpv.mute = on

    def close(self):
        try:
            self.mpv.terminate()
        except Exception:
            pass


# ----------------------------------------------------------------- chrome ---
def glass(p, rect, top="#4b5563", mid="#1c2129", bottom="#0b0d11", line="#000000"):
    g = QLinearGradient(0, rect.top(), 0, rect.bottom())
    g.setColorAt(0, QColor(top))
    g.setColorAt(0.48, QColor(mid))
    g.setColorAt(0.5, QColor(bottom))
    g.setColorAt(1, QColor("#14181e"))
    p.fillRect(rect, g)
    p.setPen(QColor(255, 255, 255, 40))
    p.drawLine(rect.left(), rect.top(), rect.right(), rect.top())
    p.setPen(QColor(line))
    p.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())


class TabBar(QWidget):
    """WMP11's black glass top strip: Now Playing / Library / Rip / Burn."""
    picked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(40)
        self.tabs = [("nowplaying", "Now Playing"), ("library", "Library"), ("rip", "Rip"), ("burn", "Burn")]
        self.current = "library"
        self.hover = None
        self.setMouseTracking(True)
        self.rects = {}

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        glass(p, self.rect())
        f = QFont(self.font())
        f.setPointSizeF(10)
        p.setFont(f)
        x = 14
        self.rects = {}
        for key, label in self.tabs:
            w = p.fontMetrics().horizontalAdvance(label) + 34
            r = QRect(x, 5, w, self.height() - 8)
            self.rects[key] = r
            if key == self.current or key == self.hover:
                g = QLinearGradient(0, r.top(), 0, r.bottom())
                if key == self.current:
                    g.setColorAt(0, QColor(110, 170, 230, 150))
                    g.setColorAt(0.5, QColor(40, 90, 160, 170))
                    g.setColorAt(0.51, QColor(20, 50, 110, 200))
                    g.setColorAt(1, QColor(60, 130, 210, 170))
                else:
                    g.setColorAt(0, QColor(255, 255, 255, 50))
                    g.setColorAt(1, QColor(255, 255, 255, 10))
                path = QPainterPath()
                path.addRoundedRect(QRectF(r), 3, 3)
                p.fillPath(path, g)
                p.setPen(QPen(QColor(150, 200, 255, 120) if key == self.current else QColor(255, 255, 255, 60), 1))
                p.drawPath(path)
            p.setPen(QColor("white"))
            p.drawText(r.adjusted(0, 0, -10, 0), Qt.AlignCenter, label)
            # the little ▾ WMP put on each tab
            p.setPen(QColor(200, 210, 225))
            p.drawText(QRect(r.right() - 16, r.top(), 12, r.height()), Qt.AlignCenter, "▾")
            x += w + 4
        p.setPen(QColor(170, 190, 215))
        f.setPointSizeF(9)
        f.setItalic(True)
        p.setFont(f)
        p.drawText(self.rect().adjusted(0, 0, -16, 0), Qt.AlignRight | Qt.AlignVCenter, "Halcyon Player")

    def mouseMoveEvent(self, e):
        h = next((k for k, r in self.rects.items() if r.contains(e.position().toPoint())), None)
        if h != self.hover:
            self.hover = h
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        k = next((k for k, r in self.rects.items() if r.contains(e.position().toPoint())), None)
        if k:
            self.picked.emit(k)

    def set_current(self, k):
        self.current = k
        self.update()


class RoundButton(QToolButton):
    """Transport buttons; `big` is the round glass play/pause in the middle."""
    def __init__(self, kind, big=False, tip=""):
        super().__init__()
        self.kind, self.big = kind, big
        self.setToolTip(tip)
        self.setFixedSize(52 if big else 30, 52 if big else 30)
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        self.active = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(2, 2, self.width() - 4, self.height() - 4)
        hover, down = self.underMouse(), self.isDown()
        if self.big:
            g = QRadialGradient(r.center().x(), r.top() + r.height() * 0.3, r.width() * 0.7)
            g.setColorAt(0, QColor("#8fd3ff" if hover else "#6cbcff"))
            g.setColorAt(0.5, QColor("#1f6fd0" if not down else "#174f99"))
            g.setColorAt(1, QColor("#0b2c5c"))
            p.setPen(QPen(QColor("#0a1a33"), 1.5))
            p.setBrush(g)
            p.drawEllipse(r)
            hl = QLinearGradient(0, r.top(), 0, r.center().y())
            hl.setColorAt(0, QColor(255, 255, 255, 200))
            hl.setColorAt(1, QColor(255, 255, 255, 20))
            p.setPen(Qt.NoPen)
            p.setBrush(hl)
            p.drawEllipse(QRectF(r.left() + 6, r.top() + 2, r.width() - 12, r.height() * 0.48))
            if hover:
                glow = QRadialGradient(r.center().x(), r.bottom(), r.width() * 0.6)
                glow.setColorAt(0, QColor(120, 220, 255, 150))
                glow.setColorAt(1, QColor(120, 220, 255, 0))
                p.setBrush(glow)
                p.drawEllipse(r)
            color = QColor("white")
        else:
            if hover or self.active:
                g = QRadialGradient(r.center(), r.width() / 2)
                g.setColorAt(0, QColor(130, 190, 255, 120 if not self.active else 160))
                g.setColorAt(1, QColor(130, 190, 255, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(g)
                p.drawEllipse(r)
            color = QColor("#e8eef6") if self.isEnabled() else QColor("#555d68")
            if self.active:
                color = QColor(BLUE)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        c = r.center()
        s = r.width() / (3.2 if self.big else 2.6)
        k = self.kind
        if k == "play":
            p.drawPolygon(QPolygonF([QPointF(c.x() - s * 0.4, c.y() - s * 0.6), QPointF(c.x() + s * 0.65, c.y()),
                                     QPointF(c.x() - s * 0.4, c.y() + s * 0.6)]))
        elif k == "pause":
            p.drawRect(QRectF(c.x() - s * 0.5, c.y() - s * 0.55, s * 0.32, s * 1.1))
            p.drawRect(QRectF(c.x() + s * 0.18, c.y() - s * 0.55, s * 0.32, s * 1.1))
        elif k == "stop":
            p.drawRect(QRectF(c.x() - s * 0.42, c.y() - s * 0.42, s * 0.84, s * 0.84))
        elif k in ("next", "prev"):
            d = 1 if k == "next" else -1
            for off in (-0.3, 0.3):
                x = c.x() + d * off * s - d * s * 0.1
                p.drawPolygon(QPolygonF([QPointF(x - d * s * 0.3, c.y() - s * 0.45), QPointF(x + d * s * 0.3, c.y()),
                                         QPointF(x - d * s * 0.3, c.y() + s * 0.45)]))
            p.drawRect(QRectF(c.x() + d * s * 0.5 - s * 0.06, c.y() - s * 0.45, s * 0.12, s * 0.9))
        elif k in ("shuffle", "repeat"):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap))
            if k == "repeat":
                p.drawRoundedRect(QRectF(c.x() - s * 0.6, c.y() - s * 0.35, s * 1.2, s * 0.7), 4, 4)
                p.setBrush(color)
                p.setPen(Qt.NoPen)
                p.drawPolygon(QPolygonF([QPointF(c.x() + s * 0.05, c.y() - s * 0.6), QPointF(c.x() + s * 0.32, c.y() - s * 0.35),
                                         QPointF(c.x() + s * 0.05, c.y() - s * 0.1)]))
            else:
                p.drawLine(QPointF(c.x() - s * 0.6, c.y() - s * 0.35), QPointF(c.x() + s * 0.5, c.y() + s * 0.35))
                p.drawLine(QPointF(c.x() - s * 0.6, c.y() + s * 0.35), QPointF(c.x() + s * 0.5, c.y() - s * 0.35))
                p.setBrush(color)
                p.setPen(Qt.NoPen)
                for y in (-0.35, 0.35):
                    tip = QPointF(c.x() + s * 0.65, c.y() + y * s)
                    p.drawPolygon(QPolygonF([tip, tip + QPointF(-s * 0.25, -s * 0.15), tip + QPointF(-s * 0.25, s * 0.15)]))
        elif k in ("mute", "unmute"):
            p.drawPolygon(QPolygonF([QPointF(c.x() - s * 0.6, c.y() - s * 0.2), QPointF(c.x() - s * 0.3, c.y() - s * 0.2),
                                     QPointF(c.x(), c.y() - s * 0.5), QPointF(c.x(), c.y() + s * 0.5),
                                     QPointF(c.x() - s * 0.3, c.y() + s * 0.2), QPointF(c.x() - s * 0.6, c.y() + s * 0.2)]))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(color, 1.6))
            if k == "unmute":
                p.drawArc(QRectF(c.x() - s * 0.2, c.y() - s * 0.35, s * 0.6, s * 0.7), -50 * 16, 100 * 16)
            else:
                p.setPen(QPen(QColor("#ff7a6a"), 1.8))
                p.drawLine(QPointF(c.x() + s * 0.2, c.y() - s * 0.2), QPointF(c.x() + s * 0.6, c.y() + s * 0.2))
                p.drawLine(QPointF(c.x() + s * 0.6, c.y() - s * 0.2), QPointF(c.x() + s * 0.2, c.y() + s * 0.2))


class Transport(QWidget):
    def paintEvent(self, e):
        p = QPainter(self)
        glass(p, self.rect(), top="#3d4552", mid="#1a1e25", bottom="#07090c")


SLIDER = """
QSlider::groove:horizontal { height: 4px; background: #2a3039; border: 1px solid #000; border-radius: 2px; }
QSlider::sub-page:horizontal { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #9fd6ff, stop:1 #2f7fd8);
    border-radius: 2px; }
QSlider::handle:horizontal { width: 10px; height: 10px; margin: -4px 0; border-radius: 5px;
    background: qradialgradient(cx:0.5, cy:0.35, radius:0.6, stop:0 #ffffff, stop:1 #7fb8ec); border: 1px solid #0b2c5c; }
"""

QSS = f"""
QMainWindow {{ background: #10141a; }}
QWidget#nav, QTreeWidget#nav {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f4f6f9, stop:1 #e7ecf2);
    border: none; border-right: 1px solid #b9c3d0; color: #1e2733; outline: 0; }}
QTreeWidget#nav::item {{ padding: 3px 2px; border: 1px solid transparent; }}
QTreeWidget#nav::item:hover {{ background: #e3edf8; border-color: #c4d7ec; border-radius: 2px; }}
QTreeWidget#nav::item:selected {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dfeefd, stop:1 #c2dcf7);
    border-color: #84acd8; border-radius: 2px; color: black; }}
QListWidget#tiles, QTreeWidget#songs {{ background: white; border: none; outline: 0; color: #1e2733; }}
QTreeWidget#songs::item {{ padding: 2px; }}
QTreeWidget#songs::item:selected, QListWidget#tiles::item:selected {{ background: #cfe3fa; color: black; }}
QHeaderView::section {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e6ebf1);
    border: none; border-right: 1px solid #d3dae3; border-bottom: 1px solid #c3ccd8; padding: 4px 6px; color: #3b4b60; }}
QListWidget#listpane {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f3f5f8, stop:1 #e9edf2);
    border: none; border-left: 1px solid #b9c3d0; color: #1e2733; outline: 0; }}
QListWidget#listpane::item {{ padding: 3px 6px; }}
QListWidget#listpane::item:selected {{ background: #cfe3fa; color: black; }}
QWidget#crumbs {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f7f9fb, stop:1 #dfe5ec);
    border-bottom: 1px solid #b9c3d0; }}
QWidget#crumbs QLabel {{ color: #2b3a4e; }}
QWidget#crumbs QToolButton {{ border: 1px solid transparent; border-radius: 3px; padding: 2px 6px; color: #2b3a4e; }}
QWidget#crumbs QToolButton:hover {{ border-color: #9fbbdc; background: #e3eefa; }}
QLineEdit#search {{ border: 1px solid #9aa9bb; border-radius: 2px; padding: 3px 6px; background: white; color: black; }}
QLabel#np_title {{ color: white; }}
QLabel#np_sub, QLabel#time {{ color: #9fb0c6; }}
QMenu {{ background: #f7f9fc; border: 1px solid #97a8c0; padding: 2px; color: black; }}
QMenu::item {{ padding: 4px 24px 4px 26px; }}
QMenu::item:selected {{ background: #d9e9fb; border: 1px solid #a8c8ec; border-radius: 2px; }}
QWidget#nowplaying {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0d1622, stop:0.6 #05080c, stop:1 #000000); }}
QWidget#nowplaying QLabel {{ color: white; }}
QListWidget#np_list {{ background: transparent; border: none; color: #c9d6e6; outline: 0; }}
QListWidget#np_list::item:selected {{ background: rgba(120, 180, 255, 60); color: white; }}
""" + SLIDER


class TileDelegate(QStyledItemDelegate):
    """Album / artist / genre tiles: art (or a stack of art) over two lines of text."""
    SIZE = QSize(150, 196)

    def __init__(self, parent, art):
        super().__init__(parent)
        self.art = art

    def sizeHint(self, option, index):
        return self.SIZE

    def paint(self, p, option, index):
        d = index.data(Qt.UserRole)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(6, 6, -6, -6)
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            sel = option.state & QStyle.State_Selected
            p.setPen(QPen(QColor("#84acd8" if sel else "#c4d7ec"), 1))
            p.setBrush(QColor("#d6e8fb" if sel else "#eef5fd"))
            p.drawRoundedRect(QRectF(r).adjusted(-3, -3, 3, 3), 4, 4)
        art = QRect(r.left() + (r.width() - 128) // 2, r.top() + 4, 128, 128)
        covers = d.get("covers") or ([d.get("cover")] if d.get("cover") else [])
        if len(covers) > 1:                       # stacked, like WMP's artist and genre tiles
            for i, c in enumerate(reversed(covers[:3])):
                off = (len(covers[:3]) - 1 - i) * 7
                self.draw_art(p, QRect(art.left() + off - 7, art.top() - off + 7, 116, 116), c, d)
        else:
            self.draw_art(p, art, covers[0] if covers else None, d)
        f = QFont(option.font)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#1e2733"))
        tr = QRect(r.left(), art.bottom() + 6, r.width(), 18)
        p.drawText(tr, Qt.AlignHCenter | Qt.AlignTop, p.fontMetrics().elidedText(d["title"], Qt.ElideRight, r.width()))
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor("#5a6b80"))
        p.drawText(tr.translated(0, 17), Qt.AlignHCenter | Qt.AlignTop,
                   p.fontMetrics().elidedText(d.get("sub", ""), Qt.ElideRight, r.width()))
        p.restore()

    def draw_art(self, p, rect, cover, d):
        pm = self.art(cover) if cover else None
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        if pm:
            p.drawPixmap(rect, pm)
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect)
        else:
            g = QLinearGradient(0, rect.top(), 0, rect.bottom())
            g.setColorAt(0, QColor("#5f7896"))
            g.setColorAt(1, QColor("#26364a"))
            p.setBrush(g)
            p.drawRect(rect)
            p.setPen(QColor(255, 255, 255, 170))
            f = QFont(p.font())
            f.setPointSizeF(rect.height() / 3.2)
            p.setFont(f)
            p.drawText(rect, Qt.AlignCenter, "♪")


# ------------------------------------------------------------------ window ---
class LibraryWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon.fromTheme("multimedia-player"))
        self.resize(1180, 760)
        self.cfg = load_cfg()
        self.lib = Library(self.cfg)
        self.lib.changed.connect(self.refresh)
        self.lib.progress.connect(lambda n: self.status.setText(f"Adding music to the library… {n} songs"))
        self.engine = Engine()
        self.engine.position.connect(self.on_position)
        self.engine.state.connect(self.on_state)
        self.engine.track.connect(self.on_track)
        self.engine.volume(self.cfg["volume"])
        self.pixcache = {}
        self.history = []
        self.view = ("albums", None)
        self.list_items = []               # the list pane
        self.list_name = "Unsaved List"

        w = QWidget()
        self.setCentralWidget(w)
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.tabs = TabBar()
        self.tabs.picked.connect(self.tab)
        col.addWidget(self.tabs)

        self.pages = QStackedWidget()
        col.addWidget(self.pages, 1)

        # --- Library page
        libpage = QWidget()
        lp = QVBoxLayout(libpage)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)
        crumbs = QWidget(objectName="crumbs")
        cl = QHBoxLayout(crumbs)
        cl.setContentsMargins(8, 3, 8, 3)
        self.back_btn = QToolButton(text="◀", toolTip="Back")
        self.back_btn.clicked.connect(self.go_back)
        cl.addWidget(self.back_btn)
        self.crumb = QLabel()
        cl.addWidget(self.crumb, 1)
        self.status = QLabel()
        self.status.setStyleSheet("color: #5a6b80;")
        cl.addWidget(self.status)
        self.search = QLineEdit(objectName="search", placeholderText="Search")
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(lambda _: self.show_view(*self.view, push=False))
        cl.addWidget(self.search)
        lp.addWidget(crumbs)
        split = QSplitter(Qt.Horizontal, handleWidth=1, childrenCollapsible=False)
        self.nav = QTreeWidget(objectName="nav", headerHidden=True, indentation=12)
        self.nav.setIconSize(QSize(18, 18))
        self.nav.itemClicked.connect(self.nav_clicked)
        self.build_nav()
        self.center = QStackedWidget()
        self.tiles = QListWidget(objectName="tiles", viewMode=QListWidget.IconMode, resizeMode=QListWidget.Adjust,
                                 movement=QListWidget.Static, spacing=4, uniformItemSizes=True)
        self.tiles.setMouseTracking(True)
        self.tiles.setItemDelegate(TileDelegate(self.tiles, self.art))
        self.tiles.itemDoubleClicked.connect(self.tile_open)
        self.tiles.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tiles.customContextMenuRequested.connect(self.tile_menu)
        self.songs = QTreeWidget(objectName="songs", rootIsDecorated=False, alternatingRowColors=False,
                                 selectionMode=QAbstractItemView.ExtendedSelection, uniformRowHeights=True)
        self.songs.setHeaderLabels(["Title", "Length", "Contributing Artist", "Album", "Genre", "Year", "#"])
        self.songs.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for i, wdt in ((1, 60), (2, 170), (3, 190), (4, 100), (5, 50), (6, 36)):
            self.songs.setColumnWidth(i, wdt)
        self.songs.itemDoubleClicked.connect(self.song_open)
        self.songs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.songs.customContextMenuRequested.connect(self.song_menu)
        self.center.addWidget(self.tiles)
        self.center.addWidget(self.songs)
        right = QWidget(objectName="listside")
        right.setStyleSheet("#listside { background: #eef1f5; border-left: 1px solid #b9c3d0; }")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        lhead = QWidget(objectName="crumbs")
        lhl = QHBoxLayout(lhead)
        lhl.setContentsMargins(8, 4, 6, 4)
        self.list_title = QLabel(f"<b>{self.list_name}</b>")
        lhl.addWidget(self.list_title, 1)
        lmenu = QMenu(self)
        lmenu.addAction("Save List…", self.save_list)
        lmenu.addAction("Clear List", self.clear_list)
        lmenu.addAction("Shuffle List", self.shuffle_list)
        lbtn = QToolButton(text="List ▾", popupMode=QToolButton.InstantPopup)
        lbtn.setMenu(lmenu)
        lhl.addWidget(lbtn)
        rl.addWidget(lhead)
        self.listpane = QListWidget(objectName="listpane", dragDropMode=QListWidget.InternalMove,
                                    selectionMode=QAbstractItemView.ExtendedSelection)
        self.listpane.itemDoubleClicked.connect(lambda it: self.play_listpane(self.listpane.row(it)))
        self.listpane.model().rowsMoved.connect(self.list_reordered)
        self.listpane.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listpane.customContextMenuRequested.connect(self.list_menu)
        rl.addWidget(self.listpane, 1)
        self.list_hint = QLabel("Drag items here\nor right-click → Add to List\nto create a playlist.",
                                alignment=Qt.AlignCenter)
        self.list_hint.setStyleSheet("color: #7a8899; background: transparent;")
        self.list_total = QLabel()
        self.list_total.setStyleSheet("color: #5a6b80; padding: 4px 8px;")
        rl.addWidget(self.list_total)
        split.addWidget(self.nav)
        split.addWidget(self.center)
        split.addWidget(right)
        split.setSizes([200, 700, 260])
        split.setStretchFactor(1, 1)
        lp.addWidget(split, 1)
        self.pages.addWidget(libpage)

        # --- Now Playing page
        np = QWidget(objectName="nowplaying")
        npl = QHBoxLayout(np)
        npl.setContentsMargins(40, 30, 30, 30)
        npl.setSpacing(30)
        self.np_art = QLabel(alignment=Qt.AlignCenter)
        self.np_art.setMinimumSize(320, 380)
        npl.addWidget(self.np_art, 1)
        npc = QVBoxLayout()
        self.np_name = QLabel(wordWrap=True)
        f = self.np_name.font()
        f.setPointSizeF(16)
        self.np_name.setFont(f)
        self.np_info = QLabel(wordWrap=True)
        self.np_info.setStyleSheet("color: #9fb0c6;")
        npc.addWidget(self.np_name)
        npc.addWidget(self.np_info)
        self.np_list = QListWidget(objectName="np_list")
        self.np_list.itemDoubleClicked.connect(lambda it: self.engine.play_index(self.np_list.row(it)))
        npc.addWidget(self.np_list, 1)
        npl.addLayout(npc, 1)
        self.pages.addWidget(np)

        # --- transport
        tr = Transport()
        tr.setFixedHeight(84)
        tl = QVBoxLayout(tr)
        tl.setContentsMargins(12, 6, 12, 6)
        tl.setSpacing(2)
        seek_row = QHBoxLayout()
        self.t_pos = QLabel("0:00", objectName="time")
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderReleased.connect(lambda: self.engine.seek(self.seek.value() / 1000 * self.engine._dur))
        self.t_dur = QLabel("0:00", objectName="time")
        seek_row.addWidget(self.t_pos)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.t_dur)
        tl.addLayout(seek_row)
        row = QHBoxLayout()
        self.mini_art = QLabel()
        self.mini_art.setFixedSize(40, 40)
        row.addWidget(self.mini_art)
        txt = QVBoxLayout()
        txt.setSpacing(0)
        self.np_title = QLabel(objectName="np_title")
        self.np_sub = QLabel(objectName="np_sub")
        txt.addWidget(self.np_title)
        txt.addWidget(self.np_sub)
        holder = QWidget()
        holder.setLayout(txt)
        holder.setFixedWidth(260)
        row.addWidget(holder)
        row.addStretch(1)
        self.shuffle_btn = RoundButton("shuffle", tip="Turn shuffle on (Ctrl+H)")
        self.repeat_btn = RoundButton("repeat", tip="Turn repeat on (Ctrl+T)")
        self.stop_btn = RoundButton("stop", tip="Stop")
        self.prev_btn = RoundButton("prev", tip="Previous (Ctrl+B)")
        self.play_btn = RoundButton("play", big=True, tip="Play (Ctrl+P)")
        self.next_btn = RoundButton("next", tip="Next (Ctrl+F)")
        self.mute_btn = RoundButton("unmute", tip="Mute (F7)")
        for b in (self.shuffle_btn, self.repeat_btn):
            row.addWidget(b)
        row.addSpacing(12)
        for b in (self.stop_btn, self.prev_btn, self.play_btn, self.next_btn):
            row.addWidget(b)
        row.addSpacing(12)
        row.addWidget(self.mute_btn)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(self.cfg["volume"])
        self.vol.setFixedWidth(90)
        self.vol.valueChanged.connect(self.set_volume)
        row.addWidget(self.vol)
        row.addStretch(1)
        skin_btn = QToolButton(text="Skin mode", toolTip="Switch to skin mode (Ctrl+2)")
        skin_btn.setStyleSheet("QToolButton { color: #c9d6e6; border: 1px solid #3a4250; border-radius: 3px; "
                               "padding: 3px 8px; background: transparent; } QToolButton:hover { border-color: #6f9ccc; }")
        skin_btn.clicked.connect(self.skin_mode)
        row.addWidget(skin_btn)
        tl.addLayout(row)
        col.addWidget(tr)

        self.play_btn.clicked.connect(self.play_pause)
        self.stop_btn.clicked.connect(self.engine.stop)
        self.next_btn.clicked.connect(lambda: self.engine.next())
        self.prev_btn.clicked.connect(self.engine.previous)
        self.shuffle_btn.clicked.connect(self.toggle_shuffle)
        self.repeat_btn.clicked.connect(self.toggle_repeat)
        self.mute_btn.clicked.connect(self.toggle_mute)
        for key, fn in (("Ctrl+P", self.play_pause), ("Ctrl+F", lambda: self.engine.next()),
                        ("Ctrl+B", self.engine.previous), ("Ctrl+S", self.engine.stop),
                        ("Ctrl+H", self.toggle_shuffle), ("Ctrl+T", self.toggle_repeat), ("F7", self.toggle_mute),
                        ("F8", lambda: self.vol.setValue(self.vol.value() - 5)),
                        ("F9", lambda: self.vol.setValue(self.vol.value() + 5)),
                        ("Ctrl+E", self.search.setFocus), ("Ctrl+1", lambda: self.tab("library")),
                        ("Ctrl+3", lambda: self.tab("nowplaying")), ("Ctrl+2", self.skin_mode),
                        ("Alt+Left", self.go_back)):
            a = QAction(self, shortcut=QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
        self.refresh()
        self.update_list()
        self.lib.scan()

    # --- art
    def art(self, path, size=128):
        if not path:
            return None
        key = (path, size)
        if key not in self.pixcache:
            img = QImage(path)
            self.pixcache[key] = QPixmap.fromImage(img.scaled(size, size, Qt.KeepAspectRatioByExpanding,
                                                              Qt.SmoothTransformation)) if not img.isNull() else None
        return self.pixcache[key]

    # --- navigation
    def build_nav(self):
        self.nav.clear()
        pl = QTreeWidgetItem(["Playlists"])
        pl.setIcon(0, QIcon.fromTheme("view-media-playlist"))
        pl.setData(0, Qt.UserRole, ("playlists", None))
        self.nav.addTopLevelItem(pl)
        for name in sorted(self.cfg["playlists"]):
            it = QTreeWidgetItem([name])
            it.setIcon(0, QIcon.fromTheme("audio-x-generic"))
            it.setData(0, Qt.UserRole, ("playlist", name))
            pl.addChild(it)
        pl.setExpanded(True)
        lib = QTreeWidgetItem(["Library"])
        lib.setIcon(0, QIcon.fromTheme("folder-sound", QIcon.fromTheme("folder-music")))
        lib.setData(0, Qt.UserRole, ("albums", None))
        self.nav.addTopLevelItem(lib)
        for label, key, icon in (("Recently Added", "recent", "document-open-recent"), ("Artist", "artists", "user-identity"),
                                 ("Album", "albums", "media-optical-audio"), ("Songs", "songs", "audio-x-generic"),
                                 ("Genre", "genres", "view-list-icons"), ("Year", "years", "view-calendar")):
            it = QTreeWidgetItem([label])
            it.setIcon(0, QIcon.fromTheme(icon))
            it.setData(0, Qt.UserRole, (key, None))
            lib.addChild(it)
        lib.setExpanded(True)
        opts = QTreeWidgetItem(["Add folder to library…"])
        opts.setIcon(0, QIcon.fromTheme("folder-new"))
        opts.setData(0, Qt.UserRole, ("addfolder", None))
        self.nav.addTopLevelItem(opts)

    def nav_clicked(self, it, *_):
        kind, arg = it.data(0, Qt.UserRole)
        if kind == "addfolder":
            return self.add_folder()
        if kind == "playlists":
            return
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.show_view(kind, arg)

    def tab(self, key):
        if key == "rip":
            subprocess.Popen([sys.executable, "-m", "halcyon_player.app", "--rip"], start_new_session=True,
                             env=dict(os.environ))
            return
        if key == "burn":
            import shutil
            if shutil.which("k3b"):
                subprocess.Popen(["k3b", "--audiocd"], start_new_session=True)
            else:
                QMessageBox.information(self, APP, "Burning audio CDs needs K3b (Gaming Setup / pacman -S k3b).")
            return
        self.tabs.set_current(key)
        self.pages.setCurrentIndex(0 if key == "library" else 1)

    def go_back(self):
        if self.history:
            self.show_view(*self.history.pop(), push=False)

    def refresh(self):
        n = len(self.lib.tracks)
        self.status.setText("" if not self.lib.scanning else self.status.text())
        if not n and not self.lib.scanning:
            self.status.setText("")
        self.show_view(*self.view, push=False)

    def matches(self, t):
        q = self.search.text().strip().lower()
        return not q or q in f"{t['title']} {t['artist']} {t['album']} {t.get('genre', '')}".lower()

    def show_view(self, kind, arg=None, push=True):
        if push and (kind, arg) != self.view:
            self.history.append(self.view)
        self.view = (kind, arg)
        self.back_btn.setEnabled(bool(self.history))
        albums = self.lib.albums()
        q = self.search.text().strip().lower()
        crumbs = {"albums": "Album", "artists": "Artist", "songs": "Songs", "genres": "Genre", "years": "Year",
                  "recent": "Recently Added", "artist": "Artist", "genre": "Genre", "year": "Year",
                  "album": "Album", "playlist": "Playlists"}
        path = ["Music", "Library", crumbs.get(kind, "")]
        if arg and kind != "album":
            path.append(str(arg))
        elif kind == "album":
            path.append(next((a["album"] for a in albums if a["key"] == arg), ""))
        self.crumb.setText("  ▸  ".join(f"<b>{x}</b>" if i == len(path) - 1 else x for i, x in enumerate(path)))
        if not self.lib.tracks:
            self.center.setCurrentWidget(self.tiles)
            self.tiles.clear()
            msg = ("Looking for music…" if self.lib.scanning else
                   f"There's no music in {', '.join(self.cfg['folders'])} yet.\nUse “Add folder to library…” on the left.")
            it = QListWidgetItem()
            it.setData(Qt.UserRole, {"title": msg.split("\n")[0], "sub": msg.split("\n")[1] if "\n" in msg else ""})
            it.setFlags(Qt.NoItemFlags)
            self.tiles.addItem(it)
            return
        if kind in ("albums", "recent", "artist", "genre", "year"):
            if kind == "artist":
                albums = [a for a in albums if a["artist"] == arg or any(t["artist"] == arg for t in a["tracks"])]
            elif kind == "genre":
                albums = [a for a in albums if (a["genre"] or "Unknown Genre") == arg]
            elif kind == "year":
                albums = [a for a in albums if (a["year"] or "Unknown Year") == arg]
            if q:
                albums = [a for a in albums if q in f"{a['album']} {a['artist']}".lower()
                          or any(self.matches(t) for t in a["tracks"])]
            albums.sort(key=(lambda a: -a["added"]) if kind == "recent" else (lambda a: (a["artist"].lower(), a["year"],
                                                                                         a["album"].lower())))
            if kind == "recent":
                albums = albums[:48]
            self.fill_tiles([{"title": a["album"], "sub": f"{a['artist']}" + (f" · {a['year']}" if a["year"] else ""),
                              "cover": a["cover"], "open": ("album", a["key"]), "tracks": a["tracks"]} for a in albums])
        elif kind in ("artists", "genres", "years"):
            field = {"artists": "artist", "genres": "genre", "years": "year"}[kind]
            unknown = {"genre": "Unknown Genre", "year": "Unknown Year", "artist": "Unknown Artist"}[field]
            groups = {}
            for a in albums:
                groups.setdefault(a[field] or unknown, []).append(a)
            tiles = []
            for name, als in sorted(groups.items(), key=lambda kv: str(kv[0]).lower(),
                                    reverse=kind == "years"):
                if q and q not in str(name).lower():
                    continue
                n = sum(len(a["tracks"]) for a in als)
                tiles.append({"title": str(name), "sub": f"{len(als)} album{'s' if len(als) != 1 else ''}, {n} songs",
                              "covers": [a["cover"] for a in als if a["cover"]][:3],
                              "open": (field, name), "tracks": [t for a in als for t in a["tracks"]]})
            self.fill_tiles(tiles)
        elif kind in ("songs", "album", "playlist"):
            if kind == "album":
                tracks = next((a["tracks"] for a in albums if a["key"] == arg), [])
            elif kind == "playlist":
                bypath = {t["path"]: t for t in self.lib.tracks}
                tracks = [bypath[p] for p in self.cfg["playlists"].get(arg, []) if p in bypath]
            else:
                tracks = sorted(self.lib.tracks, key=lambda t: (t["artist"].lower(), t["album"].lower(),
                                                               t.get("disc", 1), t.get("track", 0)))
            self.fill_songs([t for t in tracks if self.matches(t)], keep_order=kind != "songs")

    def fill_tiles(self, tiles):
        self.center.setCurrentWidget(self.tiles)
        self.tiles.clear()
        for d in tiles:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, d)
            it.setSizeHint(TileDelegate.SIZE)
            self.tiles.addItem(it)

    def fill_songs(self, tracks, keep_order=True):
        self.center.setCurrentWidget(self.songs)
        self.songs.clear()
        for t in tracks:
            it = QTreeWidgetItem([t["title"], fmt_time(t.get("length")), t["artist"], t["album"], t.get("genre", ""),
                                  t.get("year", ""), str(t.get("track") or "")])
            it.setData(0, Qt.UserRole, t)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self.songs.addTopLevelItem(it)
        self.song_rows = tracks

    # --- tiles and songs
    def tile_open(self, it):
        d = it.data(Qt.UserRole)
        if d and d.get("open"):
            self.show_view(*d["open"])

    def selected_tracks(self, widget):
        if widget is self.tiles:
            return [t for it in self.tiles.selectedItems() for t in (it.data(Qt.UserRole) or {}).get("tracks", [])]
        return [it.data(0, Qt.UserRole) for it in self.songs.selectedItems()]

    def tile_menu(self, pos):
        it = self.tiles.itemAt(pos)
        if not it or not (it.data(Qt.UserRole) or {}).get("tracks"):
            return
        if not it.isSelected():
            self.tiles.setCurrentItem(it)
        self.track_menu(self.selected_tracks(self.tiles), self.tiles.viewport().mapToGlobal(pos))

    def song_menu(self, pos):
        if not self.songs.itemAt(pos):
            return
        self.track_menu(self.selected_tracks(self.songs), self.songs.viewport().mapToGlobal(pos))

    def track_menu(self, tracks, where):
        m = QMenu(self)
        m.addAction("Play", lambda: self.play_tracks(tracks))
        m.addAction("Add to List", lambda: self.add_to_list(tracks))
        add = m.addMenu("Add to playlist")
        for name in sorted(self.cfg["playlists"]):
            add.addAction(name, lambda n=name: self.add_to_playlist(n, tracks))
        add.addAction("New playlist…", lambda: self.new_playlist(tracks))
        m.addSeparator()
        m.addAction("Open file location", lambda: subprocess.Popen(["xdg-open", str(Path(tracks[0]["path"]).parent)],
                                                                    start_new_session=True))
        m.exec(where)

    def song_open(self, it, *_):
        rows = self.song_rows
        t = it.data(0, Qt.UserRole)
        self.play_tracks(rows, rows.index(t))

    def play_tracks(self, tracks, start=0):
        if not tracks:
            return
        self.list_items = list(tracks)
        self.list_name = "Now Playing"
        self.update_list()
        self.engine.play_list(self.list_items, start)

    # --- list pane
    def update_list(self):
        self.listpane.clear()
        cur = self.engine.current()
        for t in self.list_items:
            it = QListWidgetItem(f"{t['title']}\n{t['artist']}  ·  {fmt_time(t.get('length'))}")
            it.setData(Qt.UserRole, t)
            if cur is not None and t is cur:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                it.setForeground(QColor("#1e5aa8"))
            self.listpane.addItem(it)
        total = sum(t.get("length") or 0 for t in self.list_items)
        self.list_total.setText(f"{len(self.list_items)} items, {fmt_time(total)}" if self.list_items else "")
        self.list_title.setText(f"<b>{self.list_name}</b>")
        if not self.list_items:
            it = QListWidgetItem(self.list_hint.text())
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor("#7a8899"))
            it.setTextAlignment(Qt.AlignCenter)
            self.listpane.addItem(it)
        self.np_list.clear()
        for t in self.engine.queue:
            self.np_list.addItem(f"{t['title']}  —  {t['artist']}")
        if 0 <= self.engine.index < self.np_list.count():
            self.np_list.setCurrentRow(self.engine.index)

    def list_reordered(self, *_):
        self.list_items = [self.listpane.item(i).data(Qt.UserRole) for i in range(self.listpane.count())
                           if self.listpane.item(i).data(Qt.UserRole)]
        if self.engine.queue and self.list_name == "Now Playing":
            cur = self.engine.current()
            self.engine.queue = list(self.list_items)
            self.engine.index = self.engine.queue.index(cur) if cur in self.engine.queue else -1

    def list_menu(self, pos):
        m = QMenu(self)
        sel = [it.data(Qt.UserRole) for it in self.listpane.selectedItems() if it.data(Qt.UserRole)]
        if sel:
            m.addAction("Play", lambda: self.play_listpane(self.list_items.index(sel[0])))
            m.addAction("Remove from List", lambda: self.remove_from_list(sel))
            m.addSeparator()
        m.addAction("Save List…", self.save_list)
        m.addAction("Clear List", self.clear_list)
        m.exec(self.listpane.viewport().mapToGlobal(pos))

    def add_to_list(self, tracks):
        self.list_items += [t for t in tracks if t not in self.list_items]
        if self.list_name == "Now Playing" and self.engine.queue:
            self.engine.queue = list(self.list_items)
        self.update_list()

    def remove_from_list(self, tracks):
        self.list_items = [t for t in self.list_items if t not in tracks]
        self.update_list()

    def clear_list(self):
        self.list_items = []
        self.list_name = "Unsaved List"
        self.update_list()

    def shuffle_list(self):
        random.shuffle(self.list_items)
        self.update_list()

    def play_listpane(self, row):
        if 0 <= row < len(self.list_items):
            self.engine.play_list(self.list_items, row)

    def save_list(self):
        if not self.list_items:
            return
        name, ok = QInputDialog.getText(self, "Save List", "Playlist name:",
                                        text="" if self.list_name in ("Unsaved List", "Now Playing") else self.list_name)
        if ok and name.strip():
            self.cfg["playlists"][name.strip()] = [t["path"] for t in self.list_items]
            save_cfg(self.cfg)
            self.list_name = name.strip()
            self.build_nav()
            self.update_list()

    def new_playlist(self, tracks):
        name, ok = QInputDialog.getText(self, "Create Playlist", "Playlist name:")
        if ok and name.strip():
            self.cfg["playlists"][name.strip()] = [t["path"] for t in tracks]
            save_cfg(self.cfg)
            self.build_nav()

    def add_to_playlist(self, name, tracks):
        cur = self.cfg["playlists"].setdefault(name, [])
        cur += [t["path"] for t in tracks if t["path"] not in cur]
        save_cfg(self.cfg)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add Folder to Library", str(Path.home()))
        if d and d not in self.cfg["folders"]:
            self.cfg["folders"].append(d)
            save_cfg(self.cfg)
            self.lib.scan()

    # --- transport
    def play_pause(self):
        if self.engine.index < 0:
            tracks = self.list_items or getattr(self, "song_rows", [])
            if not tracks:
                tracks = sorted(self.lib.tracks, key=lambda t: (t["artist"].lower(), t["album"].lower(),
                                                               t.get("track", 0)))
            self.play_tracks(tracks)
        else:
            self.engine.toggle()

    def on_state(self, playing):
        self.play_btn.kind = "pause" if playing else "play"
        self.play_btn.setToolTip("Pause (Ctrl+P)" if playing else "Play (Ctrl+P)")
        self.play_btn.update()

    def on_position(self, pos, dur):
        if not self.seek.isSliderDown():
            self.seek.setValue(int(pos / dur * 1000) if dur else 0)
        self.t_pos.setText(fmt_time(pos))
        self.t_dur.setText(fmt_time(dur))

    def on_track(self, t):
        self.np_title.setText(t["title"])
        self.np_sub.setText(f"{t['artist']}  ·  {t['album']}")
        pm = self.art(t.get("cover"), 40)
        self.mini_art.setPixmap(pm if pm else QPixmap())
        self.setWindowTitle(f"{t['title']} - {APP}")
        big = self.art(t.get("cover"), 300)
        self.np_art.setPixmap(self.reflect(big) if big else QPixmap())
        self.np_name.setText(t["title"])
        self.np_info.setText(f"{t['artist']}<br>{t['album']}" + (f" ({t['year']})" if t.get("year") else ""))
        plays = self.cfg["plays"]
        plays[t["path"]] = plays.get(t["path"], 0) + 1
        self.update_list()

    def reflect(self, pm):
        """Album art standing on a glossy floor, as in Now Playing."""
        h = pm.height()
        out = QPixmap(pm.width(), int(h * 1.35))
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.drawPixmap(0, 0, pm)
        p.save()
        p.translate(0, h * 2)
        p.scale(1, -1)
        p.setOpacity(0.35)
        p.drawPixmap(0, 0, pm)
        p.restore()
        g = QLinearGradient(0, h, 0, out.height())
        g.setColorAt(0, QColor(0, 0, 0, 60))
        g.setColorAt(1, QColor(0, 0, 0, 255))
        p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        p.fillRect(QRect(0, h, pm.width(), out.height() - h), g)
        p.end()
        return out

    def set_volume(self, v):
        self.engine.volume(v)
        self.cfg["volume"] = v

    def toggle_mute(self):
        on = self.mute_btn.kind == "unmute"
        self.engine.mute(on)
        self.mute_btn.kind = "mute" if on else "unmute"
        self.mute_btn.update()

    def toggle_shuffle(self):
        self.engine.shuffle = not self.engine.shuffle
        self.shuffle_btn.active = self.engine.shuffle
        self.shuffle_btn.update()

    def toggle_repeat(self):
        self.engine.repeat = not self.engine.repeat
        self.repeat_btn.active = self.engine.repeat
        self.repeat_btn.update()

    def skin_mode(self):
        """Hand the current song to the skinned player and step aside."""
        t = self.engine.current()
        args = [sys.executable, "-m", "halcyon_player.app"] + ([t["path"]] if t else [])
        subprocess.Popen(args, start_new_session=True, env=dict(os.environ))
        self.close()

    def closeEvent(self, e):
        save_cfg(self.cfg)
        self.engine.close()
        e.accept()


def light_palette():
    pal = QPalette()
    for role, color in ((QPalette.Window, "#eef2f6"), (QPalette.WindowText, "#000000"), (QPalette.Base, "#ffffff"),
                        (QPalette.Text, "#000000"), (QPalette.Button, "#eef3f9"), (QPalette.ButtonText, "#000000"),
                        (QPalette.Highlight, "#3399ff"), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.ToolTipBase, "#ffffe1"), (QPalette.ToolTipText, "#000000")):
        pal.setColor(role, QColor(color))
    return pal


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("halcyon-player")
    app.setDesktopFileName("halcyon-player")
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
    w = LibraryWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
