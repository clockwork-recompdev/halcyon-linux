"""Halcyon Media Center: a 10-foot, Media Center-style front end for Movies and
TV from Jellyfin and Plex. Everything is drawn here (no Microsoft files):
deep blue backdrop with light ribbons, the vertical start menu whose focused
row opens into a strip of tiles, poster galleries, details pages, and
full-screen playback through mpv (resume points are reported back).

Keyboard/remote: arrows, Enter, Esc/Backspace. Gamepad: d-pad/stick, A, B."""
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, QUrl,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QMessageBox, QWidget)

from . import sources as S

APP = "Halcyon Media Center"
# Gallery sort row: label, key, newest/biggest first?
PIVOTS = (("title", lambda i: i["title"].lower(), False),
          ("genre", lambda i: ((i.get("genres") or ["~"])[0].lower(), i["title"].lower()), False),
          ("year", lambda i: (-int(i.get("year") or 0), i["title"].lower()), False),
          ("date added", lambda i: -(i.get("added") or 0), False))
FONT = "Noto Sans"
WHITE = QColor("#ffffff")
SOFT = QColor(200, 222, 255, 200)
DIM = QColor(160, 190, 235, 140)
GLOW = QColor("#7fd0ff")


def font(px, weight=QFont.Light, italic=False):
    f = QFont(FONT)
    f.setPixelSize(max(8, int(px)))
    f.setWeight(weight)
    f.setItalic(italic)
    return f


def clock(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def halcyon_logo():
    for path in ("/usr/share/pixmaps/halcyon-logo.png",
                 Path(__file__).resolve().parent.parent.parent / "branding/assets/logo.png"):
        if os.path.exists(path):
            return QPixmap(str(path))
    return QIcon.fromTheme("halcyon-logo").pixmap(128, 128)


# ---------------------------------------------------------------- threads ---
class Job(QThread):
    done = Signal(object, str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), "")
        except Exception as e:      # network down, signed out, …
            self.done.emit(None, str(e))


class Images:
    """Posters and backdrops, fetched with the source's auth header, cached scaled."""

    def __init__(self, owner):
        self.owner = owner
        self.net = QNetworkAccessManager(owner)
        self.raw, self.scaled, self.pending = {}, {}, set()

    def get(self, url, headers, w, h, crop=True):
        if not url:
            return None
        key = (url, w, h, crop)
        if key in self.scaled:
            return self.scaled[key]
        src = self.raw.get(url)
        if src is None:
            if url not in self.pending:
                self.pending.add(url)
                req = QNetworkRequest(QUrl(url))
                for k, v in (headers or {}).items():
                    req.setRawHeader(k.encode(), v.encode())
                r = self.net.get(req)
                r.finished.connect(lambda r=r, url=url: self._done(r, url))
            return None
        if src.isNull():
            return None
        mode = Qt.KeepAspectRatioByExpanding if crop else Qt.KeepAspectRatio
        s = src.scaled(w, h, mode, Qt.SmoothTransformation)
        if crop:
            s = s.copy((s.width() - w) // 2, (s.height() - h) // 2, w, h)
        if len(self.scaled) > 600:
            self.scaled.clear()
        self.scaled[key] = s
        return s

    def _done(self, reply, url):
        self.pending.discard(url)
        p = QPixmap()
        p.loadFromData(reply.readAll())
        self.raw[url] = p
        reply.deleteLater()
        self.owner.update()


# ------------------------------------------------------------------ player ---
class Playback(threading.Thread):
    """mpv, full screen, starting at the resume point; reports progress back."""

    def __init__(self, source, item, start, on_end):
        super().__init__(daemon=True)
        self.source, self.item, self.start_at, self.on_end = source, item, start, on_end

    def run(self):
        sock = os.path.join(tempfile.gettempdir(), f"halcyon-mc-{os.getpid()}.sock")
        pos = self.start_at
        try:
            url, headers = self.source.stream(self.item)
        except S.SourceError as e:
            self.on_end(self.item, None, str(e))
            return
        cmd = ["mpv", "--fs", "--force-window=immediate", "--keep-open=no", "--osd-level=1",
               f"--input-ipc-server={sock}", f"--start={int(self.start_at)}",
               "--http-header-fields=" + ",".join(h.replace(",", r"\,") for h in headers),
               f"--title={self.item['title']}", url]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.source.report(self.item, "start", pos)
        last_report = time.time()
        conn = None
        while proc.poll() is None:
            time.sleep(1)
            try:
                if conn is None:
                    conn = socket.socket(socket.AF_UNIX)
                    conn.connect(sock)
                    conn.settimeout(1)
                conn.sendall(b'{"command": ["get_property", "time-pos"]}\n')
                for line in conn.recv(4096).decode(errors="replace").splitlines():
                    d = json.loads(line)
                    if isinstance(d.get("data"), (int, float)):
                        pos = d["data"]
            except (OSError, ValueError):
                conn = None
            if time.time() - last_report > 10:
                self.source.report(self.item, "progress", pos)
                last_report = time.time()
        self.source.report(self.item, "stopped", pos)
        try:
            os.unlink(sock)
        except OSError:
            pass
        self.on_end(self.item, pos, "")


# ------------------------------------------------------------------ dialogs ---
class JellyfinSignIn(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Sign in to Jellyfin")
        lay = QFormLayout(self)
        self.server = QLineEdit(placeholderText="http://192.168.1.10:8096")
        self.user = QLineEdit()
        self.pw = QLineEdit(echoMode=QLineEdit.Password)
        lay.addRow("Server", self.server)
        lay.addRow("Username", self.user)
        lay.addRow("Password", self.pw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)


# -------------------------------------------------------------- main window ---
class MediaCenter(QWidget):
    POSTER = 2 / 3

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.img = Images(self)
        self.logo = halcyon_logo()
        self.sources = S.sources()
        self.data = {"movies": [], "shows": [], "resume": [], "recent_movies": [], "recent_tv": []}
        self.loading = True
        self.error = ""
        self.stack = []                 # screens to go back to
        self.screen = "start"
        self.menu = 2                   # start menu row (movies)
        self.tile = {0: 0, 1: 0, 2: 0, 3: 0}  # focused tile per row
        self.grid = 0                   # focus in a poster gallery
        self.items = []                 # what the gallery shows
        self.title = ""
        self.series = None
        self.seasons, self.season = [], 0
        self.episodes, self.ep = [], 0
        self.detail = None
        self.button = 0
        self.scroll = 0.0
        self.message = ""
        self.hits = []
        self.trans = 1.0
        self._from = None
        self._dir = 1                   # 1 going in, -1 going back
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=380,
                                       easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._tick)
        # Smoothly animated positions (menu row, strip and grid scroll, episode list).
        self.smooth = {}                # name -> [from, to, start time, duration]
        self._frame = QTimer(self, interval=16, timeout=self._animate)
        self.pivot = 0                  # gallery sort: title / genre / year / date added
        self.on_pivots = False          # focus on the sort row
        self.pop = 1.0
        self._pop = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=160,
                                      easingCurve=QEasingCurve.OutCubic)
        self._pop.valueChanged.connect(lambda v: (setattr(self, "pop", v), self.update()))
        self.clock = QTimer(self, interval=15000, timeout=self.update)
        self.clock.start()
        self.sfx = None
        try:
            from halcyon_games.sfx import Sounds
            self.sfx = Sounds(self, volume=0.35)
        except Exception:
            pass
        self.pads = None
        try:
            from halcyon_games.gamepad import Gamepads
            self.pads = Gamepads(self)
            self.pads.pressed.connect(self.pad)
        except Exception:
            pass
        self.reload()
        # Opening: "start" fades in and the menu flies in (Media Center, December 2005).
        self.smooth["intro"] = [0.0, 0.0, 0, 0]
        QTimer.singleShot(150, lambda: self.ease_to("intro", 1.0, 1400))

    # --- data
    def reload(self):
        self.loading = True
        self.error = ""
        self.sources = S.sources()
        srcs = self.sources

        def load():
            out = {k: [] for k in self.data}
            for s in srcs:
                for key, fn in (("movies", s.movies), ("shows", s.shows), ("resume", s.resume),
                                ("recent_movies", lambda s=s: s.recent("movie")),
                                ("recent_tv", lambda s=s: s.recent("episode"))):
                    for it in fn():
                        it["_src"] = s
                        out[key].append(it)
            return out
        self.job = Job(load, self)
        self.job.done.connect(self._loaded)
        self.job.start()
        self.update()

    def _loaded(self, data, err):
        self.loading = False
        if data:
            self.data = data
        self.error = err
        if self.screen == "gallery":
            self.items = {"movie library": self.data["movies"], "tv library": self.data["shows"]}.get(
                self.title, self.data["movies"] + self.data["shows"])
        self.update()

    def rows(self):
        """Start menu: (label, tiles). A tile is (caption, item or None, action)."""
        tv = [("tv library", None, "tv")]
        tv += [(f"{i['series'] or i['title']}", i, "item") for i in self.data["resume"] if i["kind"] == "episode"]
        tv += [(i["series"] or i["title"], i, "item") for i in self.data["recent_tv"][:8]]
        movies = [("movie library", None, "movies")]
        movies += [(i["title"], i, "item") for i in self.data["resume"] if i["kind"] == "movie"]
        movies += [(i["title"], i, "item") for i in self.data["recent_movies"][:8]]
        tasks = [("settings", None, "settings"), ("refresh", None, "refresh"), ("close", None, "close")]
        library = [("all media", None, "all"), ("movie library", None, "movies"), ("tv library", None, "tv")]
        return [("library", library), ("tv", tv), ("movies", movies), ("tasks", tasks)]

    # --- navigation
    def sound(self, name):
        if self.sfx:
            self.sfx.play(name)

    def go(self, screen, back=False):
        self._from = self.content_layer()
        self._dir = -1 if back else 1
        self.trans = 0.0
        self._anim.stop()
        self._anim.start()
        if not back:
            self.stack.append(self._state())
        self.screen = screen
        self.button = 0
        self.on_pivots = False
        self.update()

    def _state(self):
        return dict(screen=self.screen, items=self.items, title=self.title, grid=self.grid, pivot=self.pivot,
                    series=self.series, seasons=self.seasons, season=self.season, episodes=self.episodes,
                    ep=self.ep, detail=self.detail, button=self.button)

    def back(self):
        if not self.stack:
            return
        self.sound("back")
        st = self.stack.pop()
        self._from = self.content_layer()
        self._dir = -1
        self.trans = 0.0
        self._anim.stop()
        self._anim.start()
        for k, v in st.items():
            setattr(self, k, v)
        self.on_pivots = False
        self.snap_scrolls()
        self.update()

    # --- smooth values
    def ease_to(self, name, target, ms=260):
        cur = self.val(name, target)
        if abs(cur - target) < 1e-3:
            self.smooth[name] = [target, target, 0, 0]
            return
        self.smooth[name] = [cur, float(target), time.monotonic(), ms / 1000]
        if not self._frame.isActive():
            self._frame.start()

    def val(self, name, default=0.0):
        a = self.smooth.get(name)
        if a is None:
            self.smooth[name] = [default, default, 0, 0]
            return default
        f, t, t0, d = a
        if d <= 0:
            return t
        x = min(1.0, (time.monotonic() - t0) / d)
        return f + (t - f) * (1 - (1 - x) ** 3)

    def _animate(self):
        now = time.monotonic()
        busy = any(d > 0 and now - t0 < d for _f, _t, t0, d in self.smooth.values())
        self.update()
        if not busy:
            self._frame.stop()

    def _tick(self, v):
        self.trans = v
        if v >= 1:
            self._from = None
        self.update()

    def bump(self):
        self._pop.stop()
        self._pop.start()

    def move(self, dx, dy):
        sc = self.screen
        moved = False
        if sc == "start":
            rows = self.rows()
            if dy:
                n = max(0, min(len(rows) - 1, self.menu + dy))
                moved, self.menu = n != self.menu, n
            if dx:
                tiles = rows[self.menu][1]
                n = max(0, min(len(tiles) - 1, self.tile[self.menu] + dx))
                moved, self.tile[self.menu] = n != self.tile[self.menu], n
        elif sc == "gallery":
            rows = self.grid_rows()
            if self.on_pivots:
                if dx:
                    n = max(0, min(len(PIVOTS) - 1, self.pivot + dx))
                    moved = n != self.pivot
                    if moved:
                        self.set_pivot(n)
                elif dy > 0:
                    self.on_pivots, moved = False, True
            elif dy < 0 and self.grid % rows == 0:
                self.on_pivots, moved = True, True       # up from the top row: the sort row
            else:
                n = self.grid + (dx * rows if dx else dy)
                if dy and (self.grid % rows + dy) not in range(rows):
                    n = self.grid
                n = max(0, min(len(self.view_items()) - 1, n))
                moved, self.grid = n != self.grid, n
        elif sc == "series":
            if dx and self.seasons:
                n = max(0, min(len(self.seasons) - 1, self.season + dx))
                if n != self.season:
                    self.season, moved = n, True
                    self.load_episodes()
            if dy and self.episodes:
                n = max(0, min(len(self.episodes) - 1, self.ep + dy))
                moved, self.ep = n != self.ep, n
        elif sc == "details":
            n = max(0, min(len(self.buttons()) - 1, self.button + (dy or dx)))
            moved, self.button = n != self.button, n
        elif sc == "settings":
            n = max(0, min(len(self.settings_rows()) - 1, self.button + (dy or dx)))
            moved, self.button = n != self.button, n
        self.sound("move" if moved else "bump")
        if moved:
            self.bump()
            self.follow()
        self.update()

    def follow(self):
        """Glide menus and lists toward the new focus."""
        self.ease_to("menu", self.menu)
        self.ease_to(f"strip{self.menu}", self.tile.get(self.menu, 0))
        if self.screen == "gallery" and self.items:
            rows, vis = self.grid_rows(), self.grid_cols_visible()
            col = self.grid // rows
            cur = self.val("gscroll")
            target = cur
            if col < cur + 1:
                target = col - 1
            elif col > cur + vis - 2:
                target = col - vis + 2
            last = max(0, (len(self.view_items()) - 1) // rows - vis + 1)
            self.ease_to("gscroll", max(0, min(target, last)), 300)
        if self.screen == "series" and self.episodes:
            vis = 8
            self.ease_to("epscroll", max(0, min(self.ep - vis // 2, len(self.episodes) - vis)))

    def snap_scrolls(self):
        """Jump (no glide) to where the focus is, e.g. after going back."""
        for k in ("menu", "gscroll", "epscroll", "strip0", "strip1", "strip2", "strip3"):  # not "intro"
            self.smooth.pop(k, None)
        self.follow()
        for v in self.smooth.values():
            v[0], v[3] = v[1], 0

    # --- gallery helpers
    def grid_rows(self):
        H = self.height()
        return max(2, int((H * 0.64) // (H * 0.19 + H * 0.018)))

    def grid_cols_visible(self):
        H = self.height()
        return max(1, int((self.width() * 0.88) // (H * 0.19 * self.POSTER + H * 0.018)))

    def set_pivot(self, n):
        items = self.view_items()
        keep = items[self.grid] if items else None
        self.pivot = n
        items = self.view_items()
        self.grid = items.index(keep) if keep in items else 0
        self.smooth.pop("gscroll", None)
        self.follow()

    def view_items(self):
        key = (id(self.items), len(self.items), self.pivot)
        if getattr(self, "_sorted_key", None) != key:
            _label, by, rev = PIVOTS[self.pivot]
            self._sorted = sorted(self.items, key=by, reverse=rev)
            self._sorted_key = key
        return self._sorted

    def activate(self):
        sc = self.screen
        if sc == "start":
            label, tiles = self.rows()[self.menu]
            if not tiles:
                return
            cap, item, action = tiles[self.tile[self.menu]]
            self.run_action(action, item)
        elif sc == "gallery" and self.on_pivots:
            self.on_pivots = False
            self.update()
        elif sc == "gallery" and self.items:
            self.open_item(self.view_items()[self.grid])
        elif sc == "series" and self.episodes:
            self.open_item(self.episodes[self.ep])
        elif sc == "details":
            self.buttons()[self.button][1]()
        elif sc == "settings":
            self.settings_rows()[self.button][1]()

    def run_action(self, action, item=None):
        self.sound("select")
        if action == "movies":
            self.items, self.title, self.grid = self.data["movies"], "movie library", 0
            self.go("gallery")
        elif action == "tv":
            self.items, self.title, self.grid = self.data["shows"], "tv library", 0
            self.go("gallery")
        elif action == "all":
            self.items, self.title, self.grid = self.data["movies"] + self.data["shows"], "all media", 0
            self.go("gallery")
        elif action == "item":
            self.open_item(item)
        elif action == "settings":
            self.go("settings")
        elif action == "refresh":
            self.reload()
        elif action == "close":
            self.close()

    def open_item(self, item):
        self.sound("select")
        if item["kind"] == "series":
            self.series, self.seasons, self.season, self.episodes, self.ep = item, [], 0, [], 0
            self.go("series")
            src = item["_src"]
            job = Job(lambda: src.seasons(item), self)
            job.done.connect(self._seasons)
            job.start()
            self._season_job = job
        else:
            self.detail = item
            self.go("details")

    def _seasons(self, seasons, err):
        self.seasons = seasons or []
        for s in self.seasons:
            s["_src"] = self.series["_src"]
        # Start on the first season that isn't fully watched.
        self.season = next((i for i, s in enumerate(self.seasons) if not s["played"]), 0)
        self.load_episodes()

    def load_episodes(self):
        if not self.seasons:
            self.update()
            return
        series, season = self.series, self.seasons[self.season]
        src = series["_src"]
        self.episodes, self.ep = [], 0
        job = Job(lambda: src.episodes(series, season), self)
        job.done.connect(self._episodes)
        job.start()
        self._ep_job = job

    def _episodes(self, eps, err):
        self.episodes = eps or []
        for e in self.episodes:
            e["_src"] = self.series["_src"]
        self.ep = next((i for i, e in enumerate(self.episodes) if not e["played"]), 0)
        self.smooth.pop("epscroll", None)
        self.update()

    def buttons(self):
        it = self.detail or {}
        out = []
        if it.get("resume", 0) > 30:
            out.append((f"resume ({clock(it['resume'])})", lambda: self.play(it, it["resume"])))
            out.append(("play from beginning", lambda: self.play(it, 0)))
        else:
            out.append(("play", lambda: self.play(it, 0)))
        out.append(("back", self.back))
        return out

    def play(self, item, start):
        self.sound("launch")
        self.message = f"Playing {item['title']}…"
        self.update()
        Playback(item["_src"], item, start, self._played).start()

    def _played(self, item, pos, err):
        # From the playback thread: hop back to the UI thread.
        QTimer.singleShot(0, lambda: self._after_play(item, pos, err))

    def _after_play(self, item, pos, err):
        self.message = err
        if pos is not None:
            item["resume"] = pos if item.get("duration") and pos < item["duration"] * 0.92 else 0
        self.activateWindow()
        self.reload()

    def settings_rows(self):
        js = S.JellyfinSource()
        px = S.PlexSource()
        rows = [(("Jellyfin: signed in as " + (js.c.user_name or "")) if js.ready else "sign in to Jellyfin",
                 self.jellyfin_sign_in),
                (("Plex: " + px.c.server["name"]) if px.ready else "sign in to Plex", self.plex_sign_in),
                ("back", self.back)]
        return rows

    def jellyfin_sign_in(self):
        d = JellyfinSignIn(self)
        if d.exec() != QDialog.Accepted:
            return
        try:
            S.Jellyfin().sign_in(d.server.text(), d.user.text(), d.pw.text())
        except S.JellyfinError as e:
            QMessageBox.warning(self, APP, str(e))
            return
        self.reload()

    def plex_sign_in(self):
        from PySide6.QtGui import QDesktopServices
        p = S.Plex()
        try:
            url = p.start_sign_in()
        except S.PlexError as e:
            QMessageBox.warning(self, APP, str(e))
            return
        QDesktopServices.openUrl(QUrl(url))
        self.message = "Approve Halcyon in the Plex page that just opened…"
        self.update()
        tries = {"n": 0}

        def poll():
            tries["n"] += 1
            try:
                if p.poll_sign_in():
                    servers = p.servers()
                    if servers:
                        p.choose_server(servers[0])
                    self.message = ""
                    self.reload()
                    return
            except S.PlexError as e:
                self.message = str(e)
                self.update()
                return
            if tries["n"] < 120:
                QTimer.singleShot(2000, poll)
        QTimer.singleShot(2000, poll)

    # --- input
    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Left:
            self.move(-1, 0)
        elif k == Qt.Key_Right:
            self.move(1, 0)
        elif k == Qt.Key_Up:
            self.move(0, -1)
        elif k == Qt.Key_Down:
            self.move(0, 1)
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activate()
        elif k in (Qt.Key_Escape, Qt.Key_Backspace, Qt.Key_Back):
            if self.stack:
                self.back()
            elif k == Qt.Key_Escape:
                self.close()
        elif k == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        else:
            super().keyPressEvent(e)

    def pad(self, name):
        if not self.isActiveWindow():
            return
        {"left": lambda: self.move(-1, 0), "right": lambda: self.move(1, 0), "up": lambda: self.move(0, -1),
         "down": lambda: self.move(0, 1), "a": self.activate, "b": self.back,
         "lb": lambda: self.move(-5, 0), "rb": lambda: self.move(5, 0)}.get(name, lambda: None)()

    def wheelEvent(self, e):
        d = 1 if e.angleDelta().y() < 0 else -1
        if self.screen == "gallery":
            self.move(d, 0)
        else:
            self.move(0, d)

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        if e.button() == Qt.RightButton:
            self.back()
            return
        for r, act in reversed(self.hits):
            if r.contains(pos):
                act()
                return

    def hit(self, rect, act):
        self.hits.append((QRect(rect), act))

    # --- drawing helpers
    def glow_text(self, p, rect, s, f, color, align=Qt.AlignLeft | Qt.AlignVCenter, glow=110):
        """Media Center's lit-from-behind text: a soft blurred halo under crisp letters."""
        if not s:
            return
        key = (s, f.family(), f.pixelSize(), f.weight(), color.rgba(), glow, rect.width(), rect.height(), int(align))
        cache = self.__dict__.setdefault("_glow", {})
        pm = cache.get(key)
        if pm is None:
            pad = max(6, f.pixelSize() // 3)
            w, h = rect.width() + 2 * pad, rect.height() + 2 * pad
            from PySide6.QtGui import QImage
            mask = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            mask.fill(Qt.transparent)
            q = QPainter(mask)
            q.setRenderHint(QPainter.TextAntialiasing)
            q.setFont(f)
            q.setPen(QColor(150, 210, 255, glow))
            q.drawText(QRect(pad, pad, rect.width(), rect.height()), align, s)
            q.end()
            # Cheap blur: shrink and grow back smoothly, twice.
            for k in (4, 2):
                mask = mask.scaled(max(1, w // k), max(1, h // k), Qt.IgnoreAspectRatio, Qt.SmoothTransformation) \
                    .scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            pm = QPixmap(w, h)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.TextAntialiasing)
            q.drawImage(0, 0, mask)
            q.setFont(f)
            q.setPen(QColor(0, 20, 50, 70))
            q.drawText(QRect(pad + 1, pad + 2, rect.width(), rect.height()), align, s)
            q.setPen(color)
            q.drawText(QRect(pad, pad, rect.width(), rect.height()), align, s)
            q.end()
            if len(cache) > 400:
                cache.clear()
            cache[key] = pm
        pad = (pm.width() - rect.width()) // 2
        p.drawPixmap(rect.x() - pad, rect.y() - pad, pm)

    def text(self, p, rect, s, f, color, align=Qt.AlignLeft | Qt.AlignVCenter, glow=False):
        p.setFont(f)
        if glow:
            p.setPen(QColor(120, 200, 255, 70))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 2)):
                p.drawText(QRect(rect).translated(dx, dy), align, s)
        p.setPen(color)
        p.drawText(rect, align, s)

    def background(self, p, W, H):
        key = (W, H)
        if getattr(self, "_bg_key", None) != key:
            self._bg = self._render_background(W, H)
            self._bg_key = key
        p.drawPixmap(0, 0, self._bg)

    @staticmethod
    def _render_background(W, H):
        """Navy at the top, hazy blue in the middle, bright cyan light along the bottom,
        soft out-of-focus dots (measured from Media Center, December 2005)."""
        import random
        pm = QPixmap(W, H)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, QColor(5, 40, 86))
        g.setColorAt(0.35, QColor(10, 52, 98))
        g.setColorAt(0.62, QColor(30, 86, 140))
        g.setColorAt(0.88, QColor(28, 110, 180))
        g.setColorAt(1, QColor(30, 125, 195))
        p.fillRect(0, 0, W, H, g)
        # Hazy light in the middle-right and a bright pool low in the middle.
        for cx, cy, rad, col in ((0.62, 0.48, 0.42, QColor(90, 140, 185, 70)),
                                 (0.6, 1.02, 0.45, QColor(80, 200, 255, 170)),
                                 (0.86, 0.95, 0.25, QColor(120, 220, 255, 90))):
            r = QRadialGradient(W * cx, H * cy, W * rad)
            r.setColorAt(0, col)
            c2 = QColor(col)
            c2.setAlpha(0)
            r.setColorAt(1, c2)
            p.fillRect(0, 0, W, H, r)
        # Out-of-focus dots (fixed pattern).
        rnd = random.Random(7)
        for _ in range(26):
            x, y = rnd.random() * W, H * (0.15 + rnd.random() * 0.8)
            rad = H * (0.01 + rnd.random() * 0.035)
            d = QRadialGradient(x, y, rad)
            d.setColorAt(0, QColor(170, 225, 255, rnd.randint(8, 24)))
            d.setColorAt(0.7, QColor(170, 225, 255, 10))
            d.setColorAt(1, QColor(170, 225, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(d)
            p.drawEllipse(QPointF(x, y), rad, rad)
        p.end()
        return pm

    def chrome(self, p, W, H):
        cy = int(H * 0.05)
        orb = int(H * 0.042)
        ox = W - int(W * 0.03) - orb
        if not self.logo.isNull():
            pm = self.logo.scaled(orb, orb, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(ox, cy - pm.height() // 2, pm)
            self.hit(QRect(ox, cy - orb // 2, orb, orb),
                     lambda: (self.stack.clear(), self.go("start", back=True)) if self.screen != "start" else None)
        # "Halcyon Media Center", the first word heavier, like the original wordmark.
        f1, f2 = font(H * 0.026, QFont.DemiBold), font(H * 0.026, QFont.Normal)
        w2 = QFontMetrics(f2).horizontalAdvance(" Media Center")
        w1 = QFontMetrics(f1).horizontalAdvance("Halcyon")
        x = ox - int(W * 0.008) - w1 - w2
        self.glow_text(p, QRect(x, cy - orb // 2, w1 + 4, orb), "Halcyon", f1, WHITE, glow=60)
        self.glow_text(p, QRect(x + w1, cy - orb // 2, w2 + 4, orb), " Media Center", f2, WHITE, glow=60)
        self.text(p, QRect(0, cy + orb // 2, ox + orb, int(H * 0.03)), time.strftime("%-I:%M %p"),
                  font(H * 0.02, QFont.Normal), QColor(220, 235, 255, 210), Qt.AlignRight | Qt.AlignTop)
        if self.screen != "start" or self.stack:
            r = int(H * 0.026)
            cx = int(W * 0.035)
            p.setPen(QPen(QColor(220, 235, 255, 210), max(2, r // 8)))
            p.setBrush(QColor(255, 255, 255, 25))
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.drawLine(QPointF(cx + r * 0.45, cy), QPointF(cx - r * 0.45, cy))
            p.drawLine(QPointF(cx - r * 0.45, cy), QPointF(cx - r * 0.05, cy - r * 0.4))
            p.drawLine(QPointF(cx - r * 0.45, cy), QPointF(cx - r * 0.05, cy + r * 0.4))
            self.hit(QRect(cx - r, cy - r, 2 * r, 2 * r), self.back)
        if self.message or self.error or self.loading:
            msg = self.message or (f"Couldn't load your library: {self.error}" if self.error else "Loading your library…")
            self.text(p, QRect(int(W * 0.05), int(H * 0.94), int(W * 0.9), int(H * 0.04)), msg,
                      font(H * 0.022), SOFT, Qt.AlignRight | Qt.AlignVCenter)

    def poster(self, p, item, r, focused, label_below=False):
        src = item.get("_src")
        pm = self.img.get(item.get("poster"), src.image_headers() if src else None, r.width(), r.height())
        if pm is not None:
            p.drawPixmap(r.topLeft(), pm)
        else:
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0, QColor(60, 110, 190, 160))
            g.setColorAt(1, QColor(20, 50, 110, 160))
            p.fillRect(r, g)
            self.text(p, r.adjusted(8, 8, -8, -8), item.get("title", ""), font(r.height() * 0.08), SOFT,
                      Qt.AlignCenter | Qt.TextWordWrap)
        if item.get("played"):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 120))
            p.drawEllipse(QPointF(r.right() - r.width() * 0.12, r.top() + r.width() * 0.12), r.width() * 0.08,
                          r.width() * 0.08)
            self.text(p, QRect(int(r.right() - r.width() * 0.2), r.top(), int(r.width() * 0.16),
                               int(r.width() * 0.24)), "✓", font(r.width() * 0.1, QFont.Bold), WHITE, Qt.AlignCenter)
        if item.get("resume") and item.get("duration"):
            frac = min(1.0, item["resume"] / item["duration"])
            p.fillRect(QRect(r.left(), r.bottom() - 4, r.width(), 4), QColor(0, 0, 0, 150))
            p.fillRect(QRect(r.left(), r.bottom() - 4, int(r.width() * frac), 4), GLOW)
        if focused:
            for i, a in enumerate((180, 90, 40)):
                p.setPen(QPen(QColor(150, 215, 255, a), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(r).adjusted(-2 - i * 2, -2 - i * 2, 2 + i * 2, 2 + i * 2), 4, 4)

    def tile_box(self, p, r, caption, focused):
        """A start-menu text tile: glassy box, caption."""
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        g.setColorAt(0, QColor(255, 255, 255, 60 if focused else 22))
        g.setColorAt(1, QColor(255, 255, 255, 18 if focused else 6))
        p.setPen(QPen(QColor(170, 220, 255, 230 if focused else 60), 2 if focused else 1))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(r), 6, 6)
        px = min(r.height() * 0.42, max(r.height() * 0.17, self.height() * 0.026))
        self.text(p, r.adjusted(10, 0, -10, 0), caption, font(px), WHITE if focused else SOFT,
                  Qt.AlignCenter | Qt.TextWordWrap)

    # --- screens
    def content_layer(self, record=False):
        """The current screen's content (no background, no top bar) as a picture.
        record=True keeps its click areas (for the screen being shown)."""
        pm = QPixmap(self.size() * self.devicePixelRatioF())
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        q.setRenderHint(QPainter.SmoothPixmapTransform)
        hits = self.hits
        getattr(self, "paint_" + self.screen)(q, self.width(), self.height())
        if not record:
            self.hits = hits
        q.end()
        return pm

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self.hits = []
        W, H = self.width(), self.height()
        self.background(p, W, H)
        if self._from is not None and self.trans < 1:
            # Media Center's zoom: going in, the old screen swells and fades while
            # the new one settles from slightly small; going back, the reverse.
            t = self.trans
            new = self.content_layer(record=True)

            def draw(pm, scale, alpha):
                w, h = W * scale, H * scale
                p.setOpacity(max(0.0, min(1.0, alpha)))
                p.drawPixmap(QRectF((W - w) / 2, (H - h) / 2, w, h), pm, QRectF(pm.rect()))
            out_t = min(1.0, t / 0.6)
            draw(self._from, 1 + 0.08 * out_t * self._dir, 1 - out_t)
            in_t = max(0.0, (t - 0.15) / 0.85)
            draw(new, 1 - 0.06 * (1 - in_t) * self._dir, in_t)
            p.setOpacity(1)
        else:
            getattr(self, "paint_" + self.screen)(p, W, H)
        self.chrome(p, W, H)

    def glyph(self, p, kind, c, size, color):
        """Simple line icons for strip items without pictures."""
        p.save()
        p.setPen(QPen(color, max(2, size * 0.08), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        x, y, r = c.x(), c.y(), size / 2
        if kind == "settings":
            p.drawEllipse(QPointF(x, y), r * 0.38, r * 0.38)
            for k in range(8):
                a = k * math.pi / 4
                p.drawLine(QPointF(x + math.cos(a) * r * 0.55, y + math.sin(a) * r * 0.55),
                           QPointF(x + math.cos(a) * r * 0.85, y + math.sin(a) * r * 0.85))
        elif kind == "refresh":
            p.drawArc(QRectF(x - r * 0.7, y - r * 0.7, r * 1.4, r * 1.4), 40 * 16, 290 * 16)
            p.drawLine(QPointF(x + r * 0.55, y - r * 0.45), QPointF(x + r * 0.62, y - r * 0.05))
            p.drawLine(QPointF(x + r * 0.55, y - r * 0.45), QPointF(x + r * 0.15, y - r * 0.38))
        elif kind == "close":
            p.drawLine(QPointF(x - r * 0.5, y - r * 0.5), QPointF(x + r * 0.5, y + r * 0.5))
            p.drawLine(QPointF(x + r * 0.5, y - r * 0.5), QPointF(x - r * 0.5, y + r * 0.5))
        else:                                   # libraries: a little grid of covers
            for gx in range(3):
                for gy in range(2):
                    p.drawRect(QRectF(x - r * 0.75 + gx * r * 0.52, y - r * 0.6 + gy * r * 0.62, r * 0.4, r * 0.52))
        p.restore()

    def collage(self, p, r, items):
        """Media Center's library tile: a grid of covers inside the frame."""
        cols, rows = 6, 3
        cw, ch = r.width() / cols, r.height() / rows
        for k, it in enumerate(items[:cols * rows]):
            cell = QRect(int(r.x() + (k % cols) * cw) + 1, int(r.y() + (k // cols) * ch) + 1,
                         int(cw) - 2, int(ch) - 2)
            src = it.get("_src")
            pm = self.img.get(it.get("poster"), src.image_headers() if src else None, cell.width(), cell.height())
            if pm is not None:
                p.drawPixmap(cell.topLeft(), pm)
            else:
                p.fillRect(cell, QColor(60, 110, 190, 120))

    def library_items(self, action):
        return {"movies": self.data["movies"], "tv": self.data["shows"],
                "all": self.data["movies"][:6] + self.data["shows"][:6]}.get(action, [])

    def paint_start(self, p, W, H):
        rows = self.rows()
        intro = self.val("intro", 1.0)                 # 0 -> 1 when the app opens
        # The big ghosted "start".
        wf = font(H * 0.215, QFont.Light)
        p.setOpacity(min(1.0, intro * 1.6))
        self.text(p, QRect(int(W * 0.023), int(H * 0.17), int(W * 0.29), int(H * 0.3)), "start", wf,
                  QColor(150, 180, 210, 95), Qt.AlignRight | Qt.AlignBottom)
        p.setOpacity(1)
        x0 = int(W * 0.345)
        lf = font(H * 0.044, QFont.DemiBold)
        fy = H * 0.426                                   # focused label's centre
        step = H * 0.093
        tile_h = H * 0.217
        mpos = self.val("menu", self.menu)
        for i, (label, tiles) in enumerate(rows):
            off = i - mpos
            near = max(0.0, 1 - abs(off))
            # Rows below the open one leave room for its strip.
            y = fy + off * step + (max(0.0, min(1.0, off)) * (tile_h + H * 0.025))
            # Menu flies in from the right when the app opens, rows one after another.
            fly = max(0.0, min(1.0, intro * 1.8 - abs(off) * 0.18))
            dx = (1 - fly) ** 2 * W * 0.25
            fade = 1.0 if abs(off) < 0.5 else (0.9 if abs(off) < 1.5 else 0.45)
            c = QColor(255, 255, 255) if near > 0.5 else QColor(205, 222, 240)
            c.setAlpha(int(255 * fade * fly))
            name = {"tv": "TV"}.get(label, label.title())
            self.glow_text(p, QRect(int(x0 + dx), int(y - H * 0.035), int(W * 0.5), int(H * 0.07)), name, lf, c,
                           glow=120 if near > 0.5 else 50)
            self.hit(QRect(int(x0), int(y - H * 0.03), int(W * 0.25), int(H * 0.06)),
                     lambda i=i: (setattr(self, "menu", i), self.bump(), self.follow(), self.update()))
            if i != self.menu:
                continue
            p.setOpacity(max(0.0, (1 - abs(off) * 1.5)) * fly)
            ty = int(y + H * 0.035)
            th = int(tile_h)
            tw_focus = int(W * 0.246)
            slot = int(W * 0.14)
            spos = self.val(f"strip{i}", self.tile[i])
            cur = self.tile[i]
            for j, (cap, item, action) in enumerate(tiles):
                # Focused tile sits under the name; earlier items to its left, later ones to the right.
                rel = j - spos
                if rel <= -0.5:
                    x = x0 + rel * slot - W * 0.02
                elif rel < 0.5:
                    x = x0 + rel * slot
                else:
                    x = x0 + tw_focus + W * 0.1 + (rel - 1) * slot
                foc = j == cur
                if x > W or x + slot < 0:
                    continue
                if foc:
                    r = QRect(int(x), ty, tw_focus, th)
                    k = 0.03 * self.pop
                    r = r.adjusted(-int(tw_focus * k / 2), -int(th * k / 2), int(tw_focus * k / 2), int(th * k / 2))
                    self.glass_tile(p, r, cap if item is None else (item["series"] or item["title"]
                                                                     if item["kind"] == "episode" else item["title"]),
                                    item, action)
                    self.hit(r, lambda j=j, i=i: self._tile_click(i, j))
                else:
                    r = QRect(int(x), ty, slot - int(W * 0.015), th)
                    text = cap.lower() if item is None else (item["series"] or item["title"]) if item["kind"] == "episode" \
                        else item["title"]
                    cf = font(H * 0.032, QFont.Normal)
                    self.glow_text(p, QRect(r.x(), r.y() + int(th * 0.12), r.width(), int(H * 0.05)),
                                   QFontMetrics(cf).elidedText(text, Qt.ElideRight, r.width()), cf,
                                   QColor(200, 218, 236, 200), Qt.AlignHCenter | Qt.AlignVCenter, glow=40)
                    art_r = QRect(r.x() + r.width() // 4, r.y() + int(th * 0.38), r.width() // 2, int(th * 0.45))
                    if item is not None and (item.get("poster") or item.get("thumb")):
                        p.setOpacity(p.opacity() * 0.55)
                        pr = QRect(art_r.center().x() - int(art_r.height() * self.POSTER / 2), art_r.y(),
                                   int(art_r.height() * self.POSTER), art_r.height())
                        self.poster(p, item, pr, False)
                        p.setOpacity(max(0.0, (1 - abs(off) * 1.5)) * fly)
                    else:
                        self.placeholder(p, art_r)
                    self.hit(r, lambda j=j, i=i: self._tile_click(i, j))
            p.setOpacity(1)
        if not self.sources and not self.loading:
            self.text(p, QRect(x0, int(H * 0.9), int(W * 0.6), int(H * 0.05)),
                      "Sign in to Jellyfin or Plex: Tasks › settings", font(H * 0.028), SOFT)

    def placeholder(self, p, r):
        """The faint stacked lines Media Center showed for items without pictures."""
        p.save()
        for col in range(2):
            x = r.x() + col * (r.width() * 0.55)
            for k in range(5):
                y = r.y() + r.height() * (0.15 + k * 0.14)
                g = QLinearGradient(x, 0, x + r.width() * 0.42, 0)
                g.setColorAt(0, QColor(200, 230, 255, 0))
                g.setColorAt(0.5, QColor(200, 230, 255, 60))
                g.setColorAt(1, QColor(200, 230, 255, 0))
                p.fillRect(QRectF(x, y, r.width() * 0.42, max(1.0, r.height() * 0.02)), g)
        p.restore()

    def glass_tile(self, p, r, caption, item, action):
        """The focused start-menu tile: a pane of glass, caption top right, covers along the bottom."""
        H = self.height()
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 15, 40, 90))
        p.drawRoundedRect(QRectF(r).adjusted(-3, -1, 6, 8), 4, 4)        # soft shadow
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        g.setColorAt(0, QColor(150, 185, 215, 235))
        g.setColorAt(0.42, QColor(80, 120, 165, 210))
        g.setColorAt(1, QColor(40, 85, 140, 200))
        p.setBrush(g)
        p.setPen(QPen(QColor(220, 238, 255, 230), 1.2))
        p.drawRoundedRect(QRectF(r), 3, 3)
        shine = QLinearGradient(0, r.top(), 0, r.top() + r.height() * 0.45)
        shine.setColorAt(0, QColor(255, 255, 255, 70))
        shine.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(shine)
        p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -r.height() * 0.55), 2, 2)
        cf = font(H * 0.038, QFont.DemiBold)
        cap_r = QRect(r.x() + 10, r.y() + int(r.height() * 0.08), r.width() - int(r.width() * 0.07), int(H * 0.055))
        self.glow_text(p, cap_r, QFontMetrics(cf).elidedText(caption, Qt.ElideLeft, cap_r.width()), cf, WHITE,
                       Qt.AlignRight | Qt.AlignVCenter, glow=90)
        # Covers along the bottom, fading out to the right.
        covers = self.library_items(action) if item is None else [item]
        ch = int(r.height() * 0.36)
        cw = int(ch * self.POSTER)
        cx = r.x() + int(r.width() * 0.07)
        cy = r.y() + int(r.height() * 0.48)
        n = max(1, (r.width() - int(r.width() * 0.1)) // (cw + 4))
        for k, it in enumerate(covers[:n]):
            p.setOpacity(1.0 if k < n - 2 else (0.55 if k == n - 2 else 0.25))
            src = it.get("_src")
            pm = self.img.get(it.get("poster"), src.image_headers() if src else None, cw, ch)
            cell = QRect(cx + k * (cw + 4), cy, cw, ch)
            if pm is not None:
                p.drawPixmap(cell.topLeft(), pm)
            else:
                p.fillRect(cell, QColor(60, 110, 190, 140))
        p.restore()

    def _tile_click(self, i, j):
        if self.menu == i and self.tile[i] == j:
            self.activate()
        else:
            self.menu, self.tile[i] = i, j
            self.bump()
            self.follow()
            self.update()

    def paint_gallery(self, p, W, H):
        x0 = int(W * 0.06)
        self.text(p, QRect(x0, int(H * 0.09), int(W * 0.8), int(H * 0.08)), self.title,
                  font(H * 0.055), WHITE, glow=True)
        # Sort row (Media Center's pivot): the current one lit, focusable from the top row.
        px = x0
        for i, (label, _k, _r) in enumerate(PIVOTS):
            on = i == self.pivot
            f = font(H * 0.028, QFont.Normal if on else QFont.Light)
            w = QFontMetrics(f).horizontalAdvance(label) + int(W * 0.03)
            r = QRect(px, int(H * 0.165), w, int(H * 0.045))
            if on and self.on_pivots:
                p.setPen(QPen(QColor(170, 220, 255, 200), 1))
                p.setBrush(QColor(255, 255, 255, 30))
                p.drawRoundedRect(QRectF(r).adjusted(-8, 2, -int(W * 0.015), -2), 4, 4)
            self.text(p, r, label, f, GLOW if on else DIM)
            self.hit(r, lambda i=i: (self.set_pivot(i), self.update()))
            px += w
        items = self.view_items()
        if not items:
            self.text(p, QRect(x0, int(H * 0.4), int(W * 0.8), int(H * 0.05)),
                      "Loading…" if self.loading else "Nothing here yet.", font(H * 0.035), SOFT)
            return
        rows = self.grid_rows()
        ph = int(H * 0.19)
        pw = int(ph * self.POSTER)
        gap = int(H * 0.018)
        y0 = int(H * 0.25)
        scroll = self.val("gscroll")
        first = max(0, int(scroll) - 1)
        label_fn = {1: lambda it: (it.get("genres") or ["other"])[0], 2: lambda it: str(it.get("year") or "—")}.get(
            self.pivot)
        last_label = None
        if label_fn and first > 0:
            last_label = label_fn(items[first * rows - 1])
        focused_rect = None
        for i in range(first * rows, len(items)):
            c, r = i // rows, i % rows
            x = x0 + (c - scroll) * (pw + gap)
            if x > W:
                break
            rect = QRect(int(x), y0 + r * (ph + gap), pw, ph)
            if label_fn and r == 0:
                lab = label_fn(items[i])
                if lab != last_label:            # a new genre / year starts in this column
                    self.text(p, QRect(int(x), y0 - int(H * 0.035), int(W * 0.3), int(H * 0.03)), lab.lower(),
                              font(H * 0.022, QFont.Normal), GLOW)
                    last_label = lab
            elif label_fn:
                last_label = label_fn(items[i])
            if i == self.grid and not self.on_pivots:
                focused_rect = rect
                continue
            self.poster(p, items[i], rect, False)
            self.hit(rect, lambda i=i: self._grid_click(i))
        if focused_rect is not None:
            # Focused cover zooms over its neighbours.
            k = 0.10 + 0.06 * self.pop
            fr = focused_rect.adjusted(-int(pw * k / 2), -int(ph * k / 2), int(pw * k / 2), int(ph * k / 2))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 10, 40, 120))
            p.drawRoundedRect(QRectF(fr).adjusted(-6, -4, 6, 10), 6, 6)
            self.poster(p, items[self.grid], fr, True)
            self.hit(fr, lambda: self._grid_click(self.grid))
        it = items[min(self.grid, len(items) - 1)]
        meta = " · ".join(str(x) for x in (it["year"], it["rating"],
                                            f"{it['minutes']} min" if it["minutes"] else "",
                                            ", ".join(it.get("genres", [])[:2]),
                                            f"★ {it['score']:.1f}" if isinstance(it.get("score"), (int, float)) else "")
                          if x)
        y = y0 + rows * (ph + gap) + int(H * 0.01)
        self.text(p, QRect(x0, y, int(W * 0.84), int(H * 0.055)), it["title"], font(H * 0.04), WHITE)
        self.text(p, QRect(x0, y + int(H * 0.05), int(W * 0.84), int(H * 0.035)), meta, font(H * 0.024), SOFT)
        self.text(p, QRect(x0, y + int(H * 0.05), int(W * 0.88), int(H * 0.035)),
                  f"{self.grid + 1} of {len(items)}", font(H * 0.022), DIM, Qt.AlignRight | Qt.AlignVCenter)

    def _grid_click(self, i):
        if i == self.grid and not self.on_pivots:
            self.activate()
        else:
            self.grid, self.on_pivots = i, False
            self.bump()
            self.follow()
            self.update()

    def paint_series(self, p, W, H):
        s = self.series or {}
        back = self.img.get(s.get("backdrop"), s["_src"].image_headers() if s.get("_src") else None, W, H)
        if back is not None:
            p.setOpacity(0.18)
            p.drawPixmap(0, 0, back)
            p.setOpacity(1)
        self.text(p, QRect(int(W * 0.08), int(H * 0.1), int(W * 0.84), int(H * 0.09)), s.get("title", ""),
                  font(H * 0.06), WHITE, glow=True)
        ph = int(H * 0.5)
        pr = QRect(int(W * 0.08), int(H * 0.24), int(ph * self.POSTER), ph)
        self.poster(p, s, pr, False)
        x = pr.right() + int(W * 0.04)
        # Seasons across the top, like Media Center's pivot.
        sx = x
        for i, se in enumerate(self.seasons):
            f = font(H * 0.032, QFont.Normal if i == self.season else QFont.Light)
            label = se["title"].lower()
            w = QFontMetrics(f).horizontalAdvance(label) + int(W * 0.025)
            self.text(p, QRect(sx, int(H * 0.2), w, int(H * 0.05)), label, f,
                      GLOW if i == self.season else DIM)
            self.hit(QRect(sx, int(H * 0.2), w, int(H * 0.05)),
                     lambda i=i: (setattr(self, "season", i), self.load_episodes()))
            sx += w
        if not self.episodes:
            self.text(p, QRect(x, int(H * 0.3), int(W * 0.5), int(H * 0.05)), "Loading…", font(H * 0.03), SOFT)
            return
        rh = int(H * 0.058)
        vis = 8
        scroll = self.val("epscroll", max(0, min(self.ep - vis // 2, len(self.episodes) - vis)))
        top = int(H * 0.28)
        p.save()
        p.setClipRect(QRect(x, top - 4, W - x, vis * rh + 8))
        first = max(0, int(scroll))
        for i in range(first, min(len(self.episodes), first + vis + 1)):
            e = self.episodes[i]
            y = int(top + (i - scroll) * rh)
            r = QRect(x, y, int(W * 0.84) - x + int(W * 0.08), rh)
            foc = i == self.ep
            if foc:
                g = QLinearGradient(0, r.top(), 0, r.bottom())
                g.setColorAt(0, QColor(120, 190, 255, 90))
                g.setColorAt(1, QColor(60, 130, 230, 60))
                p.setPen(QPen(QColor(170, 220, 255, 200), 1))
                p.setBrush(g)
                p.drawRoundedRect(QRectF(r), 4, 4)
            num = f"{e['number']}." if e.get("number") is not None else ""
            self.text(p, r.adjusted(int(W * 0.01), 0, 0, 0), num, font(rh * 0.42), DIM)
            self.text(p, r.adjusted(int(W * 0.045), 0, -int(W * 0.12), 0), e["title"], font(rh * 0.45),
                      WHITE if foc else SOFT)
            tail = ("✓  " if e.get("played") else "") + (f"{e['minutes']} min" if e["minutes"] else "")
            self.text(p, r.adjusted(0, 0, -int(W * 0.01), 0), tail, font(rh * 0.38), DIM,
                      Qt.AlignRight | Qt.AlignVCenter)
            if e.get("resume") and e.get("duration") and not e.get("played"):
                p.fillRect(QRect(r.left() + int(W * 0.045), r.bottom() - 3,
                                 int((r.width() - W * 0.17) * min(1, e["resume"] / e["duration"])), 3), GLOW)
            self.hit(r, lambda i=i: self._ep_click(i))
        p.restore()
        y = top + vis * rh
        e = self.episodes[self.ep]
        if e.get("overview"):
            self.text(p, QRect(x, y + int(H * 0.02), int(W * 0.84) - x + int(W * 0.08), int(H * 0.16)),
                      e["overview"], font(H * 0.024), SOFT, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap)

    def _ep_click(self, i):
        if i == self.ep:
            self.activate()
        else:
            self.ep = i
            self.follow()
            self.update()

    def paint_details(self, p, W, H):
        it = self.detail or {}
        src = it.get("_src")
        back = self.img.get(it.get("backdrop"), src.image_headers() if src else None, W, H)
        if back is not None:
            p.setOpacity(0.25)
            p.drawPixmap(0, 0, back)
            p.setOpacity(1)
        ph = int(H * 0.55)
        pr = QRect(int(W * 0.08), int(H * 0.2), int(ph * self.POSTER), ph)
        self.poster(p, dict(it, poster=it.get("poster") if it.get("kind") != "episode" else (it.get("thumb") or it.get("poster"))),
                    pr if it.get("kind") != "episode" else QRect(pr.x(), pr.y(), int(pr.height() * 0.9), int(pr.height() * 0.5)),
                    False)
        x = pr.right() + int(W * 0.05) if it.get("kind") != "episode" else pr.x() + int(pr.height() * 0.9) + int(W * 0.05)
        title = it.get("title", "")
        if it.get("kind") == "episode":
            sup = f"{it.get('series', '')} · season {it.get('season_number') or '?'}, episode {it.get('number') or '?'}"
            self.text(p, QRect(x, int(H * 0.15), int(W * 0.92) - x, int(H * 0.04)), sup, font(H * 0.028), GLOW)
        self.text(p, QRect(x, int(H * 0.19), int(W * 0.92) - x, int(H * 0.08)), title, font(H * 0.055), WHITE,
                  glow=True)
        meta = " · ".join(str(v) for v in (it.get("year"), it.get("rating"),
                                            f"{it['minutes']} min" if it.get("minutes") else "",
                                            f"★ {it['score']:.1f}" if isinstance(it.get("score"), (int, float)) else "")
                          if v)
        self.text(p, QRect(x, int(H * 0.27), int(W * 0.92) - x, int(H * 0.04)), meta, font(H * 0.028), SOFT)
        self.text(p, QRect(x, int(H * 0.33), int(W * 0.92) - x, int(H * 0.22)), it.get("overview", ""),
                  font(H * 0.026), SOFT, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap)
        by = int(H * 0.58)
        for i, (label, _fn) in enumerate(self.buttons()):
            r = QRect(x, by + i * int(H * 0.075), int(W * 0.26), int(H * 0.06))
            foc = i == self.button
            self.tile_box(p, r, label, foc)
            self.hit(r, lambda i=i: (setattr(self, "button", i), self.activate()))

    def paint_settings(self, p, W, H):
        self.text(p, QRect(int(W * 0.08), int(H * 0.1), int(W * 0.8), int(H * 0.09)), "settings",
                  font(H * 0.06), WHITE, glow=True)
        for i, (label, _fn) in enumerate(self.settings_rows()):
            r = QRect(int(W * 0.08), int(H * 0.25) + i * int(H * 0.09), int(W * 0.4), int(H * 0.07))
            self.tile_box(p, r, label, i == self.button)
            self.hit(r, lambda i=i: (setattr(self, "button", i), self.activate()))


def use_segoe():
    """Media Center's typeface is Segoe; use the copy imported for Halcyon Messenger
    (from the user's own Messenger installer) when it's there."""
    global FONT
    from PySide6.QtGui import QFontDatabase
    d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon/messenger/skin/fonts"
    for f in ("segoeui.ttf", "segoeuil.ttf", "segoeuisl.ttf"):
        if (d / f).exists():
            fid = QFontDatabase.addApplicationFont(str(d / f))
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                FONT = fams[0]


def main():
    app = QApplication(sys.argv)
    use_segoe()
    app.setApplicationName("halcyon-mediacenter")
    app.setDesktopFileName("halcyon-mediacenter")
    w = MediaCenter()
    if "--window" in sys.argv:
        w.resize(1280, 720)
        w.show()
    else:
        w.showFullScreen()
    # Keep the ribbons drifting.
    t = QTimer(w, interval=80, timeout=w.update)
    t.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
