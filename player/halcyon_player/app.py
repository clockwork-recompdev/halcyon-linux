"""Halcyon Media Player: mpv playback wearing a Windows Media Player skin.

    halcyon-player [--skin SKIN.wmz] [FILE|URL ...]

Stage 1: skins draw and their transport buttons work (play, pause, stop,
next, previous, mute, close, minimize). JScript and live bindings come next.
"""
import argparse
import locale
import os
import re
import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCursor, QImage, QPainter
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QInputDialog,
                               QLineEdit, QMenu, QMessageBox, QToolTip, QWidget)

from .jellyfin import Jellyfin, JellyfinError
from .plex import Plex, PlexError

from .skin import (AreaElement, ButtonElement, ButtonElementInGroup, LIST_TAGS,
                   Skin, SliderElement, TextElement, build)

LAST_SKIN = os.path.expanduser("~/.config/halcyon-player/last-skin")
SKIN_DIRS = [os.path.expanduser("~/.local/share/halcyon-player/skins"),
             "/usr/share/halcyon-player/skins"]


class Player:
    """Thin wrapper around mpv with WMP-flavoured method names."""

    def __init__(self):
        locale.setlocale(locale.LC_NUMERIC, "C")   # libmpv requires it
        import mpv
        # vo=libmpv: mpv draws video frames for us (see video.py).
        self.mpv = mpv.MPV(vo="libmpv", ytdl=True, idle=True, keep_open=False,
                           hwdec="auto-safe", input_default_bindings=False)
        self.playlist = []
        self._apply_af()

    def open(self, items, append=False):
        for i, item in enumerate(items):
            self.mpv.loadfile(item, "append-play" if (append or i) else "replace")

    # mpv's audio filter chain: the equalizer (when set) + the stats the
    # visualizations read.
    VIS_FILTER = "@vis:lavfi=[astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level+Peak_level]"
    _eq_chain = ""

    def set_eq(self, chain):
        self._eq_chain = chain
        self._apply_af()

    def _apply_af(self):
        parts = [f"@eq:lavfi=[{self._eq_chain}]"] if self._eq_chain else []
        parts.append(self.VIS_FILTER)
        try:
            self.mpv["af"] = ",".join(parts)
        except Exception:
            pass

    titles = {}     # url -> display title (mpv forgets per-file titles in its playlist)

    def open_titled(self, entries, headers=None):
        """Queue [{url, title}] (network streams), replacing the playlist."""
        if headers is not None:
            self.mpv["http-header-fields"] = headers
        for e in entries:
            self.titles[e["url"]] = e["title"]
        for i, e in enumerate(entries):
            opts = {"force_media_title": e["title"]}
            if e.get("chapter"):                 # audio CD track = mpv cdda chapter
                k = e["chapter"]
                opts["start"] = f"#{k}"
                if i + 1 < len(entries) and entries[i + 1].get("chapter"):
                    opts["end"] = f"#{k + 1}"
            self.mpv.loadfile(e["url"], "append-play" if i else "replace", **opts)
            self.titles.setdefault(e["url"], e["title"])

    def play(self):
        self.mpv.pause = False
        if self.mpv.idle_active and self.mpv.playlist_count:
            self.mpv.playlist_play_index(0)

    def pause(self): self.mpv.pause = True
    def stop(self): self.mpv.command("stop", "keep-playlist")
    def next(self): self.mpv.command("playlist-next", "force")
    def previous(self): self.mpv.command("playlist-prev", "force")
    def toggle_mute(self): self.mpv.mute = not self.mpv.mute

    # WMP "modes": shuffle and loop (repeat the whole list).
    shuffle = False

    def set_mode(self, mode, on):
        mode = mode.lower()
        if mode == "shuffle" and on != self.shuffle:
            self.shuffle = on
            self.mpv.command("playlist-shuffle" if on else "playlist-unshuffle")
        elif mode in ("loop", "repeat"):
            self.mpv.loop_playlist = "inf" if on else False

    def get_mode(self, mode):
        mode = mode.lower()
        if mode == "shuffle":
            return self.shuffle
        if mode in ("loop", "repeat"):
            return self.mpv.loop_playlist not in (False, "no", None)
        return False

    @property
    def title(self):
        return self.mpv.media_title or ""

    @property
    def fraction(self):
        pos, dur = self.mpv.time_pos, self.mpv.duration
        return (pos / dur) if pos and dur else 0.0

    def seek_fraction(self, f):
        if self.mpv.duration:
            self.mpv.seek(max(0.0, min(1.0, f)) * 100, "absolute-percent")


class SkinWindow(QWidget):
    """A frameless window shaped like one skin VIEW; its scripts run in the
    session's ScriptHost."""

    def __init__(self, session, node, hidden=False):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.session = session
        self.skin, self.player = session.skin, session.player
        self.view = build(self.skin, node)
        self.setWindowTitle(self.view.title)
        self.resize(self.view.size())
        self.hidden_controller = hidden
        self.hovered = self.pressed = self._drag_slider = None
        self._frame = None
        self._dirty = False
        self.timer_ms = 0
        self.view_timer = QTimer(self, timeout=lambda: self.session.host.fire(self.view, "ontimer"))
        for el in self.view.walk():
            for m in el.movies:
                m.frameChanged.connect(self.refresh_soon)
                m.start()
        self.refresh()

    # ---- drawing
    def refresh_soon(self, *_):
        if not self._dirty:
            self._dirty = True
            QTimer.singleShot(0, self.refresh)

    def refresh(self, *_, video_only=False):
        self._dirty = False
        if self.view.size().isEmpty():
            return
        if self.size() != self.view.size():
            self.resize(self.view.size())
        self._frame = self.view.render()
        if not video_only:           # the shape doesn't change frame to frame
            # Re-shaping a window is slow on a real display; only do it when the
            # outline actually changed (Half-Life 2's shutter animates inside it).
            mask = self.view.window_mask(self._frame)
            if mask != getattr(self, "_mask", None):
                self._mask = mask
                self.setMask(mask)
        self.update()

    def video_areas(self):
        """Visible VIDEO elements in this window (with their size)."""
        out = []
        for el in self.view.walk():
            if el.node.tag == "VIDEO" and self._shown(el) and not el.size().isEmpty():
                out.append(el)
        return out

    def _shown(self, el):
        while el is not None:
            if not el.visible:
                return False
            el = el.parent
        return True

    def paintEvent(self, _):
        if self._frame is None:
            return
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(0, 0, self._frame)

    def set_timer(self, ms):
        self.timer_ms = ms
        if ms > 0:
            self.view_timer.start(ms)
        else:
            self.view_timer.stop()

    # ---- interaction
    def element_at(self, pos):
        return self.view.element_at(pos)

    def fire(self, el, event):
        self.session.host.fire(el, event)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._drag_slider is not None:
            self.slide(self._drag_slider, pos)
            return
        hit = self.element_at(pos)
        if hit is not self.hovered:
            if self.hovered is not None:
                self.hovered.hover = False
                self.fire(self.hovered, "onmouseout")
            self.hovered = hit
            if hit is not None:
                hit.hover = True
                self.fire(hit, "onmouseover")
                tip = hit.tooltip
                if tip and not str(tip).lower().startswith(("res:", "wmpprop:", "jscript:")):
                    QToolTip.showText(QCursor.pos(), str(tip), self)
            self.setCursor(Qt.PointingHandCursor if hit is not None else Qt.ArrowCursor)
            self.refresh_soon()

    def enterEvent(self, _):
        self.fire(self.view, "onmouseover")

    def leaveEvent(self, _):
        if self.hovered is not None:
            self.hovered.hover = False
            self.fire(self.hovered, "onmouseout")
            self.hovered = None
            self.refresh_soon()
        self.fire(self.view, "onmouseout")

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        if e.button() == Qt.RightButton:
            self.context_menu(e.globalPosition().toPoint())
            return
        hit = self.element_at(pos)
        self.session.host.set_event(button=1, **self._event_info(e, pos))
        if hit is None:
            self.windowHandle().startSystemMove()   # skins drag by their body
            return
        self.fire(hit, "onmousedown")
        if isinstance(hit, AreaElement) and hit.kind == "LISTBOX":
            hit.selected = hit.row_at((pos - hit.origin()).y())
            self.fire(hit, "selectedIndex_onchange")
            self.fire(hit, "onselchange")
            self.refresh_soon()
            return
        if isinstance(hit, AreaElement) and hit.kind in LIST_TAGS:
            return                       # playlists act on double-click
        if isinstance(hit, SliderElement):
            self._drag_slider = hit
            hit._dragging = True
            self.slide(hit, pos)
            return
        self.pressed = hit
        hit.down = True
        self.refresh_soon()

    def mouseReleaseEvent(self, e):
        if self._drag_slider is not None:
            el, self._drag_slider = self._drag_slider, None
            el._dragging = False
            self.session.host.write_back(el, "value", el.value)
            self.fire(el, "value_onchange")
            self.fire(el, "onchange")
            return
        el, self.pressed = self.pressed, None
        if el is None:
            return
        el.down = getattr(el, "sticky", False) and not el.down
        self.refresh_soon()
        self.fire(el, "onmouseup")
        if el is self.element_at(e.position().toPoint()):
            action = getattr(el, "action", "")
            if action:
                self.session.host.run(action, getattr(el, "api", None) or self.api, f"{el.id}.onclick")

    def _event_info(self, e, pos=None):
        mods = e.modifiers()
        info = {"shiftKey": bool(mods & Qt.ShiftModifier), "ctrlKey": bool(mods & Qt.ControlModifier),
                "altKey": bool(mods & Qt.AltModifier)}
        if pos is not None:
            info.update(x=pos.x(), y=pos.y(), offsetX=pos.x(), offsetY=pos.y())
        return info

    def keyPressEvent(self, e):
        # Windows virtual-key codes match Qt's for letters, digits and most keys.
        code = e.key() if e.key() < 0x01000000 else {Qt.Key_Escape: 27, Qt.Key_Return: 13, Qt.Key_Enter: 13,
                                                       Qt.Key_Space: 32, Qt.Key_Left: 37, Qt.Key_Up: 38,
                                                       Qt.Key_Right: 39, Qt.Key_Down: 40, Qt.Key_Tab: 9,
                                                       Qt.Key_Backspace: 8, Qt.Key_Delete: 46}.get(e.key(), 0)
        self.session.host.set_event(keyCode=code, **self._event_info(e))
        self.fire(self.view, "onkeydown")
        self.fire(self.view, "onkeypress")

    def keyReleaseEvent(self, e):
        self.fire(self.view, "onkeyup")

    def mouseDoubleClickEvent(self, e):
        pos = e.position().toPoint()
        hit = self.element_at(pos)
        if isinstance(hit, AreaElement) and hit.kind == "LISTBOX":
            hit.selected = hit.row_at((pos - hit.origin()).y())
            self.fire(hit, "selectedIndex_onchange")
            self.fire(hit, "ondblclick")
            self.refresh_soon()
            return
        if isinstance(hit, AreaElement) and hit.kind in LIST_TAGS:
            row = hit.row_at((pos - hit.origin()).y())
            if row < (self.player.mpv.playlist_count or 0):
                self.player.mpv.playlist_play_index(row)
                self.player.mpv.pause = False
            return
        self.fire(hit or self.view, "ondblclick")

    def wheelEvent(self, e):
        hit = self.element_at(e.position().toPoint())
        if isinstance(hit, AreaElement) and hit.kind in LIST_TAGS:
            hit.scroll = max(0, hit.scroll + (-3 if e.angleDelta().y() > 0 else 3))
            self.refresh_soon()

    def slide(self, el, pos):
        r = el.rect()
        local = pos - el.origin()
        f = (1 - local.y() / max(1, r.height())) if el.vertical else local.x() / max(1, r.width())
        el.value = el.minimum + (el.maximum - el.minimum) * max(0.0, min(1.0, f))
        self.refresh_soon()

    # ---- files
    def open_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Open media", os.path.expanduser("~/Music"))
        if files:
            self.player.open(files)

    def open_url(self):
        url, ok = QInputDialog.getText(self, "Open URL", "Stream or video address:")
        if ok and url.strip():
            self.player.open([url.strip()])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        items = [u.toLocalFile() or u.toString() for u in e.mimeData().urls()]
        self.player.open(items)

    def closeEvent(self, e):
        self.session.window_closed(self)
        super().closeEvent(e)

    def library_mode(self):
        """Full mode: the Windows Media Player 11-style library window."""
        import subprocess
        self.player.stop()
        subprocess.Popen([sys.executable, "-m", "halcyon_player.app", "--library"], start_new_session=True,
                         env=dict(os.environ))
        QApplication.quit()

    def context_menu(self, at):
        m = QMenu(self)
        m.addAction("Switch to Library", self.library_mode)
        m.addSeparator()
        m.addAction("Open files…", self.open_files)
        m.addAction("Open URL…", self.open_url)
        self.jellyfin_menu(m.addMenu("Jellyfin"))
        plex_menu(self, m.addMenu("Plex"))
        cdm = m.addMenu("Audio CD")
        from . import cdui
        cdm.addAction("Play CD", lambda: cdui.play_cd(self, self.player))
        cdm.addAction("Rip CD…", lambda: cdui.rip_cd(self))
        m.addSeparator()
        m.addAction("Play / Pause", lambda: self.player.pause() if not self.player.mpv.pause else self.player.play())
        m.addAction("Stop", self.player.stop)
        for label, mode in (("Shuffle", "shuffle"), ("Repeat", "loop")):
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.player.get_mode(mode))
            a.toggled.connect(lambda on, mo=mode: self.player.set_mode(mo, on))
        m.addSeparator()
        skins = m.addMenu("Skins")
        for path in find_skins():
            skins.addAction(os.path.splitext(os.path.basename(path))[0], lambda p=path: switch_skin(self, p))
        skins.addSeparator()
        skins.addAction("Install a skin (.wmz)…", lambda: install_skin(self))
        m.addSeparator()
        m.addAction("Exit", QApplication.instance().quit)
        m.exec(at)


class Session:
    """One open skin: its script host and every window (view) it has open."""

    def __init__(self, skin, player, at=None):
        from .script import ScriptHost
        self.skin, self.player = skin, player
        self.windows = []
        self.host = ScriptHost(skin, player, self._open_window, self._close_window, QApplication.instance().quit)
        self.ticker = QTimer(interval=250, timeout=self._tick)
        self.at = at
        from . import skin as skinmod
        from .video import VideoWindow
        from .vis import Levels, Painter
        skinmod.PLAYLIST_SOURCE = self.playlist_rows
        self.levels = Levels(player)
        skinmod.AREA_PAINTERS["EFFECTS"] = Painter(self.levels)
        self.vis_timer = QTimer(interval=40, timeout=self._vis_frame)
        self.vis_timer.start()
        self.video = QApplication.instance().video
        skinmod.VIDEO_SOURCE = self.video
        self.host.video = self.video
        self.video_window = VideoWindow(self.video)
        self.video.updated.connect(self._video_frame)

    def _vis_frame(self):
        live = [w for w in self.windows if w.isVisible() and any(
            getattr(e, "kind", "") == "EFFECTS" and w._shown(e) and not e.size().isEmpty() for e in w.view.walk())]
        if live:
            self.levels.update()
            for w in live:
                w.refresh(video_only=True)

    def playlist_rows(self):
        rows = []
        try:
            for e in self.player.mpv.playlist or []:
                fn = e.get("filename", "")
                title = self.player.titles.get(fn) or e.get("title") or os.path.splitext(os.path.basename(fn))[0]
                rows.append((title, bool(e.get("current") or e.get("playing"))))
        except Exception:
            pass
        return rows

    def _video_frame(self):
        areas = []
        for w in self.windows:
            if w.isVisible():
                a = w.video_areas()
                if a:
                    areas += a
                    w.refresh(video_only=True)
        if areas:
            biggest = max(areas, key=lambda e: e.size().width() * e.size().height())
            self.video.set_target(biggest.size())

    def _tick(self):
        self.host.tick()
        # Lists follow the playlist (track changes, additions).
        sig = tuple(self.playlist_rows())
        if sig != getattr(self, "_last_rows", None):
            self._last_rows = sig
            for w in self.windows:
                if w.isVisible() and any(getattr(e, "kind", "") in LIST_TAGS for e in w.view.walk()):
                    w.refresh_soon()
        # Video with nowhere to show it in the skin: use the plain video window.
        need = self.video.has_video() and not any(w.isVisible() and w.video_areas() for w in self.windows)
        if need and not self.video_window.isVisible():
            self.video_window.show()
        elif not self.video.has_video() and self.video_window.isVisible():
            self.video_window.hide()

    def start(self):
        first = self.skin.start_view()
        self.host.open_view(first.get("id") or "")
        if not self.windows:
            self._open_window(first, None, False)
        # Hidden controller views (no size of their own) run the skin's logic:
        # they open and close the real views from their scripts.
        for node in self.skin.views:
            if node is first or "preview" in (node.get("id") or "").lower():
                continue
            if not (node.get("width") or node.get("height") or node.get("backgroundimage")) and \
                    (node.get("scriptfile") or node.get("onload")):
                self._open_window(node, None, False, hidden=True)
        self.ticker.start()

    def _open_window(self, node, offset, replace, hidden=False):
        opener = next((w for w in self.windows if w.isVisible()), None)
        w = SkinWindow(self, node, hidden=hidden)
        self.windows.append(w)
        if opener is not None and offset is not None:
            w.move(opener.pos() + QPoint(*offset))
        elif opener is not None:
            w.move(opener.pos())
        elif self.at is not None:
            w.move(self.at)
        self.host.attach(w)
        if not hidden and not w.view.size().isEmpty():
            w.show()
        if replace and opener is not None and opener is not w:
            self.host.close_view(opener)

    def _close_window(self, w):
        if w in self.windows:
            self.windows.remove(w)
        w.hide()
        w.deleteLater()
        if not any(x.isVisible() for x in self.windows):
            QApplication.instance().quit()

    def window_closed(self, w):
        if w in self.windows:
            self.windows.remove(w)

    def close_all(self):
        for w in list(self.windows):
            self.windows.remove(w)
            w.hide()
            w.deleteLater()
        self.ticker.stop()
        self.vis_timer.stop()
        self.video_window.hide()


def _jellyfin_menu(self, menu):
    jf = QApplication.instance().jellyfin
    if not jf.connected:
        menu.addAction("Connect to a server…", lambda: jellyfin_connect(self))
        return
    lists = menu.addMenu("Playlists")
    lists.aboutToShow.connect(lambda: fill_playlists(self, lists))
    lists.addAction("Loading…").setEnabled(False)
    menu.addSeparator()
    menu.addAction(f"Signed in as {jf.user_name} — sign out", lambda: (jf.sign_out(), None))


SkinWindow.jellyfin_menu = _jellyfin_menu


def fill_playlists(win, menu):
    jf = QApplication.instance().jellyfin
    menu.clear()
    try:
        lists = jf.playlists()
    except JellyfinError as e:
        menu.addAction(str(e)).setEnabled(False)
        return
    if not lists:
        menu.addAction("No playlists on this server").setEnabled(False)
    for pl in lists:
        label = pl["name"] + (f"  ({pl['count']})" if pl.get("count") else "")
        menu.addAction(label, lambda pid=pl["id"]: play_playlist(win, pid))


def play_playlist(win, pid):
    jf = QApplication.instance().jellyfin
    try:
        items = jf.playlist_items(pid)
    except JellyfinError as e:
        QMessageBox.warning(win, "Jellyfin", str(e))
        return
    if items:
        win.player.open_titled(items, headers=jf.mpv_headers())


def jellyfin_connect(win):
    jf = QApplication.instance().jellyfin
    dlg = QDialog(win)
    dlg.setWindowTitle("Connect to Jellyfin")
    form = QFormLayout(dlg)
    server = QLineEdit(jf.server or "")
    server.setPlaceholderText("http://192.168.1.10:8096")
    user = QLineEdit(jf.user_name or "")
    pw = QLineEdit()
    pw.setEchoMode(QLineEdit.Password)
    form.addRow("Server", server)
    form.addRow("Username", user)
    form.addRow("Password", pw)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    form.addRow(buttons)
    while dlg.exec() == QDialog.Accepted:
        try:
            jf.sign_in(server.text(), user.text(), pw.text())
            QMessageBox.information(win, "Jellyfin", f"Connected as {jf.user_name}. Right-click → Jellyfin → Playlists.")
            return
        except JellyfinError as e:
            QMessageBox.warning(win, "Jellyfin", str(e))


def plex_menu(win, menu):
    px = QApplication.instance().plex
    if not px.signed_in:
        menu.addAction("Sign in with Plex…", lambda: plex_sign_in(win))
        return
    lists = menu.addMenu("Playlists")
    lists.addAction("Loading…").setEnabled(False)

    def fill():
        lists.clear()
        try:
            if not px.server:
                srvs = px.servers()
                if srvs:
                    px.choose_server(srvs[0])
            pls = px.playlists()
        except PlexError as e:
            lists.addAction(str(e)).setEnabled(False)
            return
        if not pls:
            lists.addAction("No playlists on this server").setEnabled(False)
        for pl in pls:
            label = pl["name"] + (f"  ({pl['count']})" if pl.get("count") else "")
            lists.addAction(label, lambda pid=pl["id"]: plex_play(win, pid))
    lists.aboutToShow.connect(fill)

    servers = menu.addMenu("Server: " + (px.server or {}).get("name", "choose…"))

    def fill_servers():
        servers.clear()
        try:
            for srv in px.servers():
                a = servers.addAction(srv["name"], lambda s=srv: plex_pick(win, s))
                a.setCheckable(True)
                a.setChecked(bool(px.server and px.server["name"] == srv["name"]))
        except PlexError as e:
            servers.addAction(str(e)).setEnabled(False)
    servers.aboutToShow.connect(fill_servers)
    menu.addSeparator()
    menu.addAction("Sign out of Plex", px.sign_out)


def plex_pick(win, srv):
    try:
        QApplication.instance().plex.choose_server(srv)
    except PlexError as e:
        QMessageBox.warning(win, "Plex", str(e))


def plex_play(win, pid):
    px = QApplication.instance().plex
    try:
        items = px.playlist_items(pid)
    except PlexError as e:
        QMessageBox.warning(win, "Plex", str(e))
        return
    if items:
        win.player.open_titled(items, headers=px.mpv_headers())


def plex_sign_in(win):
    """Open plex.tv in the browser and wait (up to 5 minutes) for approval."""
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QProgressDialog
    px = QApplication.instance().plex
    try:
        url = px.start_sign_in()
    except PlexError as e:
        QMessageBox.warning(win, "Plex", str(e))
        return
    QDesktopServices.openUrl(QUrl(url))
    prog = QProgressDialog("Approve Halcyon Media Player in your browser…", "Cancel", 0, 0, win)
    prog.setWindowTitle("Sign in with Plex")
    prog.show()
    tries = {"n": 0}
    timer = QTimer(win, interval=2000)

    def poll():
        tries["n"] += 1
        try:
            done = px.poll_sign_in()
        except PlexError:
            done = False
        if done or prog.wasCanceled() or tries["n"] > 150:
            timer.stop()
            prog.close()
            if done:
                try:
                    srvs = px.servers()
                    if srvs:
                        px.choose_server(srvs[0])
                    QMessageBox.information(win, "Plex", "Signed in. Right-click → Plex → Playlists.")
                except PlexError as e:
                    QMessageBox.warning(win, "Plex", f"Signed in, but: {e}")
    timer.timeout.connect(poll)
    timer.start()


WINE_PREFIX = os.environ.get("HALCYON_WINEPREFIX") or os.path.expanduser("~/.local/share/halcyon/windows")


def find_skins():
    """Halcyon's skin folders, plus any WMP skins installed in Halcyon's Windows
    environment (Microsoft Plus! and friends put theirs there)."""
    out = []
    for d in SKIN_DIRS:
        if os.path.isdir(d):
            out += sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".wmz"))
    drive_c = os.path.join(WINE_PREFIX, "drive_c")
    if os.path.isdir(drive_c):
        for base, dirs, files in os.walk(drive_c):
            if "/windows/winsxs" in base.lower():
                dirs[:] = []
                continue
            out += sorted(os.path.join(base, f) for f in files if f.lower().endswith(".wmz"))
    seen, unique = set(), []
    for p in out:
        key = os.path.basename(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def install_skin(win):
    path, _ = QFileDialog.getOpenFileName(win, "Install skin", os.path.expanduser("~"), "Windows Media Player skins (*.wmz)")
    if path:
        os.makedirs(SKIN_DIRS[0], exist_ok=True)
        import shutil
        dest = shutil.copy(path, SKIN_DIRS[0])
        switch_skin(win, dest)


def remember_skin(path):
    os.makedirs(os.path.dirname(LAST_SKIN), exist_ok=True)
    with open(LAST_SKIN, "w") as f:
        f.write(path)


def default_skin():
    try:
        with open(LAST_SKIN) as f:
            last = f.read().strip()
        if os.path.exists(last):
            return last
    except OSError:
        pass
    skins = find_skins()
    for s in skins:
        if os.path.basename(s).lower() == "halcyon.wmz":
            return s
    return skins[0] if skins else None


def switch_skin(win, path):
    remember_skin(path)
    app = QApplication.instance()
    old = app.session
    at = win.pos()
    app.setQuitOnLastWindowClosed(False)
    old.close_all()
    app.session = Session(Skin(path), old.player, at)
    app.session.start()
    app.setQuitOnLastWindowClosed(True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="halcyon-player")
    ap.add_argument("--skin", help="skin file (.wmz) or folder")
    ap.add_argument("--cd", action="store_true", help="play the audio CD in the drive")
    ap.add_argument("--rip", action="store_true", help="rip the audio CD in the drive")
    ap.add_argument("--library", action="store_true", help="open the music library (full mode)")
    ap.add_argument("media", nargs="*")
    args = ap.parse_args(argv)
    if args.library:
        from . import library
        sys.exit(library.main())
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Halcyon Media Player")
    app.jellyfin = Jellyfin()
    app.plex = Plex()
    player = Player()
    from .video import VideoRenderer
    app.video = VideoRenderer(player)
    skin_path = args.skin or default_skin()
    if not skin_path:
        sys.exit("No skins installed. Use --skin SKIN.wmz")
    app.session = Session(Skin(skin_path), player)
    app.session.start()
    if args.cd or args.rip:
        from . import cdui
        win = next((w for w in app.session.windows if w.isVisible()), None)
        QTimer.singleShot(300, lambda: cdui.rip_cd(win) if args.rip else cdui.play_cd(win, player))
    if args.media:
        player.open([os.path.abspath(m) if os.path.exists(m) else m for m in args.media])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
