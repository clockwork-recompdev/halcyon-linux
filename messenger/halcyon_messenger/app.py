"""Halcyon Messenger: a Live Messenger 2009-style client for your Discord
friends and direct messages.

Discord's own web client runs hidden in the background (you sign in to it
normally); bridge.js reads friends, presence and messages from it and sends
through it. Everything you see is drawn here, with the look imported from your
own copy of Messenger 2009 (see skin.py)."""
import html
import json
import os
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import (QByteArray, QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QTextCursor, QTextDocument)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QProgressDialog, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate,
                               QSystemTrayIcon, QTextBrowser, QTextEdit, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import skin

APP = "Halcyon Messenger"
HERE = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "halcyon" / "messenger.json"
NUDGE_MARK = "​⁠​"            # invisible tag so Halcyon Messenger knows a nudge
NUDGE_TEXT = "*sent you a nudge!*" + NUDGE_MARK
STATUS = {  # discord status -> (Messenger label, skin name)
    "online": ("Available", "online"),
    "idle": ("Away", "away"),
    "dnd": ("Busy", "busy"),
    "invisible": ("Appear offline", "offline"),
    "offline": ("Offline", "offline"),
}
FONT = "Segoe UI"
TEXT = QColor("#1e395b")
GRAY = QColor("#6d7f95")
LINK = QColor("#2e64a8")
DEBUG = bool(os.environ.get("HALCYON_MESSENGER_DEBUG"))


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1))


def pix(name):
    p = skin.path(name)
    return QPixmap(p) if p else QPixmap()


def icon(name):
    p = skin.path(name)
    return QIcon(p) if p else QIcon()


def status_icon(status):
    return icon(f"dot_{STATUS.get(status, STATUS['offline'])[1]}.ico")


def font(px, bold=False, italic=False):
    f = QFont(FONT)
    f.setPixelSize(px)
    f.setBold(bold)
    f.setItalic(italic)
    return f


# ---------------------------------------------------------------- sounds ---
class Sounds:
    def __init__(self):
        self.fx = {}
        try:
            from PySide6.QtMultimedia import QSoundEffect
        except ImportError:
            return
        for name in skin.SOUNDS:
            p = skin.path(f"sounds/{name}.wav")
            if p:
                e = QSoundEffect()
                e.setSource(QUrl.fromLocalFile(p))
                e.setVolume(0.8)
                self.fx[name] = e

    def play(self, name):
        e = self.fx.get(name)
        if e:
            e.stop()
            e.play()


# ---------------------------------------------------------- avatar cache ---
class Avatars(QObject):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.net = QNetworkAccessManager(self)
        self.pix = {}
        self.pending = set()
        self.default = pix("usertile_default.png")
        if self.default.isNull():
            self.default = QPixmap(96, 96)
            self.default.fill(QColor("#9fb8d6"))

    def get(self, url, fallback=True):
        if not url:
            return self.default if fallback else QPixmap()
        p = self.pix.get(url)
        if p is not None:
            return p
        if url not in self.pending:
            self.pending.add(url)
            r = self.net.get(QNetworkRequest(QUrl(url)))
            r.finished.connect(lambda r=r, url=url: self._done(r, url))
        return self.default if fallback else QPixmap()

    def _done(self, reply, url):
        self.pending.discard(url)
        p = QPixmap()
        if p.loadFromData(reply.readAll()):
            self.pix[url] = p
            self.changed.emit(url)
        reply.deleteLater()


def framed(picture, size):
    """A display picture inside Messenger's glass frame, scaled to size."""
    frame = pix("frame_large.png")
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.Antialiasing)
    k = size / 139
    hole = QRectF(25 * k, 19 * k, 94 * k, 94 * k)
    if frame.isNull():
        hole = QRectF(2, 2, size - 4, size - 4)
    path = QPainterPath()
    path.addRoundedRect(hole, 3 * k, 3 * k)
    p.setClipPath(path)
    p.drawPixmap(hole, picture.scaled(int(hole.width()) + 1, int(hole.height()) + 1, Qt.KeepAspectRatioByExpanding,
                                      Qt.SmoothTransformation), QRectF(0, 0, hole.width(), hole.height()))
    p.setClipping(False)
    if not frame.isNull():
        p.drawPixmap(QRectF(0, 0, size, size), frame, QRectF(frame.rect()))
    p.end()
    return out


# ----------------------------------------------------------------- bridge ---
class Bridge(QObject):
    """Discord's web client in a hidden page, driven by bridge.js."""
    ready = Signal(dict)
    snapshot = Signal(dict)
    message = Signal(dict)
    typing = Signal(dict)
    presence = Signal(dict)
    login = Signal()

    def __init__(self):
        super().__init__()
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.profile = QWebEngineProfile("halcyon-messenger", self)
        store = skin.DATA / "web"
        self.profile.setPersistentStoragePath(str(store))
        self.profile.setCachePath(str(store / "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        ua = self.profile.httpUserAgent()
        self.profile.setHttpUserAgent(re.sub(r"\s*QtWebEngine/\S+", "", ua))
        script = QWebEngineScript()
        script.setName("halcyon-bridge")
        script.setSourceCode((HERE / "bridge.js").read_text())
        script.setInjectionPoint(QWebEngineScript.DocumentReady)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(False)
        self.profile.scripts().insert(script)
        hook = QWebEngineScript()
        hook.setName("halcyon-hook")
        hook.setSourceCode((HERE / "hook.js").read_text())
        hook.setInjectionPoint(QWebEngineScript.DocumentCreation)
        hook.setWorldId(QWebEngineScript.MainWorld)
        hook.setRunsOnSubFrames(False)
        self.profile.scripts().insert(hook)

        bridge = self

        class Page(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, msg, line, src):
                if msg.startswith("HMSG"):
                    bridge._incoming(msg[4:])
                elif DEBUG and level != QWebEnginePage.InfoMessageLevel:
                    print("[discord]", msg[:300], file=sys.stderr)

        self.page = Page(self.profile, self)
        self.view = QWebEngineView()
        self.view.setPage(self.page)
        self.view.setWindowTitle(f"{APP} - Sign in to Discord")
        self.view.resize(980, 720)
        self.calls = {}
        self.rid = 0
        self.page.load(QUrl("https://discord.com/app"))
        if DEBUG and os.environ.get("HALCYON_MESSENGER_EVAL"):
            # Development: run snippets from a file inside the page (results printed).
            self._eval_path = Path(os.environ["HALCYON_MESSENGER_EVAL"])
            self._eval_timer = QTimer(self, interval=500, timeout=self._eval_poll)
            self._eval_timer.start()

    def _eval_poll(self):
        p = self._eval_path
        if p.exists():
            code = p.read_text()
            p.unlink()
            wrapped = ("(async () => { try { const r = await (async () => {" + code + "})();"
                       " console.log('HMSG' + JSON.stringify({type: 'eval', data: r})); }"
                       " catch (e) { console.log('HMSG' + JSON.stringify({type: 'eval', data: 'ERR ' + e})); } })()")
            self.page.runJavaScript(wrapped)

    def _incoming(self, raw):
        try:
            m = json.loads(raw)
        except ValueError:
            return
        t, d = m.get("type"), m.get("data")
        if t == "reply":
            cb = self.calls.pop(d.get("rid"), None)
            if DEBUG and not d.get("ok"):
                print("[bridge] call failed:", d.get("error"), file=sys.stderr)
            if cb:
                cb(d.get("result") if d.get("ok") else None)
        elif t == "ready":
            self.view.hide()
            self.ready.emit(d)
        elif t == "login":
            self.login.emit()
        elif t == "eval":
            out = Path(str(self._eval_path) + ".out")
            out.write_text(json.dumps(d, indent=1, default=str)[:200000])
        elif t == "waiting":
            print("[bridge] waiting:", json.dumps(d), file=sys.stderr)
        elif t == "error":
            if DEBUG:
                print("[bridge]", d, file=sys.stderr)
        elif t in ("snapshot", "message", "typing", "presence"):
            getattr(self, t).emit(d)
        if DEBUG and t != "reply":
            print("[bridge] event:", t, file=sys.stderr)

    def call(self, name, args=None, cb=None):
        self.rid += 1
        if cb:
            self.calls[self.rid] = cb
        self.page.runJavaScript(f"window.__hm && window.__hm.cmd({self.rid}, {json.dumps(name)}, "
                                f"{json.dumps(args or {})})")

    def show_login(self):
        self.view.show()
        self.view.raise_()
        self.view.activateWindow()


# ---------------------------------------------------------- scene header ---
class Scene(QWidget):
    """The picture strip across the top of Messenger windows, fading into the window."""

    def __init__(self, height, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.image = QPixmap()

    def set_image(self, path):
        self.image = QPixmap(path) if path else QPixmap()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        W, H = self.width(), self.height()
        if not self.image.isNull():
            s = self.image.scaled(W, H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(0, 0, s, (s.width() - W) // 2, 0, W, H)
        else:
            g = QLinearGradient(0, 0, 0, H)
            g.setColorAt(0, QColor("#b9dcf5"))
            g.setColorAt(1, QColor("#eaf4fc"))
            p.fillRect(self.rect(), g)
        fade = QLinearGradient(0, H * 0.45, 0, H)
        fade.setColorAt(0, QColor(255, 255, 255, 0))
        fade.setColorAt(1, QColor(255, 255, 255, 255))
        p.fillRect(self.rect(), fade)


class Body(QWidget):
    """White window body with Messenger's faint blue wash at the edges."""

    def paintEvent(self, _e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#ffffff"))
        g.setColorAt(1, QColor("#eef5fb"))
        p.fillRect(self.rect(), g)


def halcyon_logo():
    for path in ("/usr/share/pixmaps/halcyon-logo.png", HERE.parent.parent / "branding/assets/logo.png"):
        if os.path.exists(path):
            return QPixmap(str(path))
    return QIcon.fromTheme("halcyon-logo").pixmap(64, 64)


class Sidebar(QWidget):
    """Messenger's tab strip down the left of the list: contacts, then one tab per server."""
    TAB = 30

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setFixedWidth(32)
        self.setMouseTracking(True)
        self.offset = 0
        self.hover = None

    def tabs(self):
        return [("contacts", None)] + [(g["id"], g) for g in self.win.app.guilds]

    def _arrows(self):
        return len(self.tabs()) * self.TAB > self.height()

    def _area(self):
        return self.height() - (22 if self._arrows() else 0)

    def _max_offset(self):
        return max(0, len(self.tabs()) * self.TAB - self._area())

    def tab_at(self, pos):
        if pos.y() >= self._area():
            return None
        i = (pos.y() + self.offset) // self.TAB
        tabs = self.tabs()
        return tabs[i][0] if 0 <= i < len(tabs) else None

    def scroll(self, d):
        self.offset = max(0, min(self._max_offset(), self.offset + d * self.TAB))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        W = self.width()
        g = QLinearGradient(0, 0, W, 0)
        g.setColorAt(0, QColor("#f4f8fc"))
        g.setColorAt(1, QColor("#e6eef7"))
        p.fillRect(self.rect(), g)
        p.fillRect(W - 1, 0, 1, self.height(), QColor("#c5d6e8"))
        p.save()
        p.setClipRect(0, 0, W, self._area())
        for i, (key, guild) in enumerate(self.tabs()):
            r = QRect(2, i * self.TAB - self.offset + 1, W - 5, self.TAB - 2)
            if r.bottom() < 0 or r.top() > self._area():
                continue
            sel = key == self.win.view
            if sel or key == self.hover:
                bg = QLinearGradient(0, r.top(), 0, r.bottom())
                bg.setColorAt(0, QColor("#ffffff") if not sel else QColor("#e3f0fb"))
                bg.setColorAt(1, QColor("#e4effa") if not sel else QColor("#c4def6"))
                p.setPen(QPen(QColor("#a8cbeb") if not sel else QColor("#7fb0de")))
                p.setBrush(bg)
                p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            icon_r = QRect(r.x() + (r.width() - 22) // 2, r.y() + (r.height() - 22) // 2, 22, 22)
            if guild is None:
                fig = pix("state_online.png")
                if not fig.isNull():
                    p.drawPixmap(icon_r, fig)
                else:
                    status_icon("online").paint(p, icon_r)
                continue
            pic = self.win.app.avatars.get(guild.get("icon"), fallback=False)
            path = QPainterPath()
            path.addRoundedRect(QRectF(icon_r), 5, 5)
            p.save()
            p.setClipPath(path, Qt.IntersectClip)
            if not pic.isNull():
                p.drawPixmap(icon_r, pic)
            else:
                # No server icon: its initials on a glassy tile, as Discord does.
                t = QLinearGradient(0, icon_r.top(), 0, icon_r.bottom())
                t.setColorAt(0, QColor("#8fc2ec"))
                t.setColorAt(1, QColor("#3f86c9"))
                p.fillRect(icon_r, t)
                initials = "".join(w[0] for w in guild["name"].split()[:3] if w)[:3]
                p.setPen(QColor("white"))
                p.setFont(font(9, bold=True))
                p.drawText(icon_r, Qt.AlignCenter, initials)
            p.restore()
        p.restore()
        if self._arrows():
            y = self._area()
            p.setPen(GRAY if self.offset > 0 else QColor("#c5d1de"))
            p.setFont(font(9))
            p.drawText(QRect(0, y, W, 11), Qt.AlignCenter, "▲")
            p.setPen(GRAY if self.offset < self._max_offset() else QColor("#c5d1de"))
            p.drawText(QRect(0, y + 11, W, 11), Qt.AlignCenter, "▼")

    def mouseMoveEvent(self, e):
        key = self.tab_at(e.position().toPoint())
        if key != self.hover:
            self.hover = key
            g = next((g for g in self.win.app.guilds if g["id"] == key), None)
            self.setToolTip(g["name"] if g else ("Contacts" if key == "contacts" else ""))
            self.update()

    def leaveEvent(self, _e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        if pos.y() >= self._area():
            self.scroll(-3 if pos.y() < self._area() + 11 else 3)
            return
        key = self.tab_at(pos)
        if key and key != self.win.view:
            self.win.show_view(key)

    def wheelEvent(self, e):
        self.scroll(-1 if e.angleDelta().y() > 0 else 1)


class AdBanner(QWidget):
    """Messenger's 234x60 ad slot, running a house ad for Halcyon Live Messenger."""

    def __init__(self):
        super().__init__()
        self.logo = halcyon_logo()
        self.sky = pix("scene_default.jpg")
        self.setFixedSize(236, 76)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setFont(font(9))
        p.setPen(QColor("#9aa9bb"))
        p.drawText(QRect(1, 0, 234, 14), Qt.AlignLeft | Qt.AlignVCenter, "Advertisement")
        ad = QRect(1, 15, 234, 60)
        if not self.sky.isNull():
            s = self.sky.scaled(ad.width(), ad.height(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(ad, s, QRect((s.width() - ad.width()) // 2, 0, ad.width(), ad.height()))
        else:
            g = QLinearGradient(0, ad.top(), 0, ad.bottom())
            g.setColorAt(0, QColor("#7ec3f0"))
            g.setColorAt(1, QColor("#e9f5fd"))
            p.fillRect(ad, g)
        x = ad.x() + 10
        if not self.logo.isNull():
            lg = self.logo.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(x, ad.y() + (ad.height() - lg.height()) // 2, lg)
            x += lg.width() + 8
        r = QRect(x, ad.y(), ad.right() - x - 6, ad.height())
        f = font(15, bold=True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(r.translated(0, 1), Qt.AlignVCenter | Qt.TextWordWrap, "Halcyon Live Messenger")
        p.setPen(QColor("#1e4a7a"))
        p.drawText(r, Qt.AlignVCenter | Qt.TextWordWrap, "Halcyon Live Messenger")
        p.setPen(QPen(QColor("#9fbad6")))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(ad).adjusted(0.5, 0.5, -0.5, -0.5))


# ----------------------------------------------------------- contact list ---
class ContactDelegate(QStyledItemDelegate):
    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def sizeHint(self, opt, idx):
        kind = idx.data(Qt.UserRole + 1)
        return QSize(opt.rect.width(), 24 if kind == "group" else 20 if kind == "category" else 22)

    def paint(self, p, opt, idx):
        kind = idx.data(Qt.UserRole + 1)
        r = opt.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        hover = opt.state & QStyle.State_MouseOver
        sel = opt.state & QStyle.State_Selected
        if kind != "group" and (hover or sel):
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0, QColor("#f2f8fd") if not sel else QColor("#e3f0fb"))
            g.setColorAt(1, QColor("#dcebf9") if not sel else QColor("#c4def6"))
            p.setPen(QPen(QColor("#a8cbeb") if not sel else QColor("#84b3e0")))
            p.setBrush(g)
            p.drawRoundedRect(QRectF(r).adjusted(1.5, 0.5, -1.5, -0.5), 3, 3)
        if kind == "group":
            item = self.win.tree.itemFromIndex(idx)
            arrow = "▾" if item.isExpanded() else "▸"
            p.setPen(GRAY)
            p.setFont(font(12))
            p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignVCenter, arrow)
            g = idx.data(Qt.UserRole + 2) or {}
            x = 18
            if g.get("id"):
                pic = self.win.app.avatars.get(g.get("icon"), fallback=False)
                if not pic.isNull():
                    p.drawPixmap(QRect(r.x() + 18, r.y() + (r.height() - 16) // 2, 16, 16), pic)
                x = 40
            p.setPen(TEXT)
            p.setFont(font(12, bold=False))
            p.drawText(r.adjusted(x, 0, 0, 0), Qt.AlignVCenter, idx.data(Qt.DisplayRole))
        else:
            c = idx.data(Qt.UserRole + 2) or {}
            if kind == "category":
                item = self.win.tree.itemFromIndex(idx)
                p.setPen(GRAY)
                p.setFont(font(10))
                p.drawText(QRect(r.x() + 4, r.y(), 12, r.height()), Qt.AlignCenter,
                           "▾" if item.isExpanded() else "▸")
                f = font(10, bold=True)
                f.setCapitalization(QFont.AllUppercase)
                f.setLetterSpacing(QFont.AbsoluteSpacing, 0.5)
                p.setFont(f)
                p.drawText(QRect(r.x() + 18, r.y(), r.width() - 24, r.height()), Qt.AlignVCenter,
                           QFontMetrics(f).elidedText(c.get("name", ""), Qt.ElideRight, r.width() - 24))
                p.restore()
                return
            if kind in ("channel", "loading"):
                if idx.parent().isValid():
                    r = r.adjusted(12, 0, 0, 0)     # inside a category
                f = font(12, bold=bool(c.get("unread")))
                p.setFont(font(13))
                p.setPen(GRAY)
                p.drawText(QRect(r.x() + 4, r.y(), 16, r.height()), Qt.AlignCenter, "#" if kind == "channel" else "")
                p.setFont(f)
                p.setPen(TEXT if kind == "channel" else GRAY)
                fm = QFontMetrics(f)
                text = c.get("name", "")
                p.drawText(QRect(r.x() + 22, r.y(), r.right() - r.x() - 28, r.height()), Qt.AlignVCenter,
                           fm.elidedText(text, Qt.ElideRight, r.right() - r.x() - 28))
                p.restore()
                return
            ic = status_icon(c.get("status") if kind == "contact" else "online")
            if kind == "groupdm":
                ic = icon("dot_online.ico")
            ic.paint(p, QRect(r.x() + 18, r.y() + (r.height() - 16) // 2, 16, 16))
            x = r.x() + 40
            name = c.get("name", "")
            f = font(12)
            p.setFont(f)
            fm = QFontMetrics(f)
            p.setPen(TEXT if c.get("status", "online") != "offline" or kind != "contact" else GRAY)
            name_w = min(fm.horizontalAdvance(name) + 2, r.right() - x - 8)
            p.drawText(QRect(x, r.y(), name_w + 4, r.height()), Qt.AlignVCenter,
                       fm.elidedText(name, Qt.ElideRight, name_w))
            extra = c.get("psm") or c.get("playing") or ""
            if kind == "contact" and c.get("status") in ("idle", "dnd"):
                label = " (%s)" % STATUS[c["status"]][0]
            else:
                label = ""
            tail = (label + (" - " + extra if extra else "")).strip()
            if tail:
                x2 = x + name_w + 4
                p.setPen(GRAY)
                p.drawText(QRect(x2, r.y(), r.right() - x2 - 6, r.height()), Qt.AlignVCenter,
                           fm.elidedText(tail if tail.startswith("(") else tail, Qt.ElideRight, r.right() - x2 - 6))
        p.restore()


class ContactList(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle(APP)
        self.setWindowIcon(icon("app.ico"))
        self.resize(330, 640)
        self.setMinimumWidth(260)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.scene = Scene(118)
        hl = QHBoxLayout(self.scene)
        hl.setContentsMargins(10, 10, 10, 8)
        hl.setSpacing(8)
        self.pic = QLabel()
        self.pic.setFixedSize(76, 76)
        hl.addWidget(self.pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 8, 0, 0)
        self.name = QToolButton(objectName="me")
        self.name.setPopupMode(QToolButton.InstantPopup)
        self.name.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.status_menu = QMenu(self)
        for st in ("online", "dnd", "idle", "invisible"):
            a = self.status_menu.addAction(status_icon(st), STATUS[st][0])
            a.triggered.connect(lambda _c=False, st=st: self.app.set_status(st))
        self.status_menu.addSeparator()
        self.status_menu.addAction("Change scene", self.pick_scene)
        self.status_menu.addSeparator()
        self.status_menu.addAction("Sign out", self.app.quit)
        self.name.setMenu(self.status_menu)
        col.addWidget(self.name, 0, Qt.AlignLeft)
        self.psm = QLineEdit(objectName="psm", placeholderText="<Share a quick message>")
        self.psm.editingFinished.connect(lambda: self.app.set_psm(self.psm.text()))
        col.addWidget(self.psm)
        col.addStretch(1)
        hl.addLayout(col, 1)
        lay.addWidget(self.scene)

        body = Body()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(8, 2, 8, 6)
        bl.setSpacing(6)
        self.view = "contacts"
        self.search = QLineEdit(objectName="search", placeholderText="Search contacts", clearButtonEnabled=True)
        self.search.textChanged.connect(self.refill)
        bl.addWidget(self.search)
        mid = QHBoxLayout()
        mid.setSpacing(4)
        self.sidebar = Sidebar(self)
        mid.addWidget(self.sidebar)
        self.tree = QTreeWidget(objectName="contacts", headerHidden=True, rootIsDecorated=False,
                                indentation=0, mouseTracking=True)
        self.tree.setItemDelegate(ContactDelegate(self))
        self.tree.itemActivated.connect(self.activate)
        self.tree.itemClicked.connect(lambda it, _c: it.setExpanded(not it.isExpanded())
                                      if it.data(0, Qt.UserRole + 1) in ("group", "category") else None)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.setExpandsOnDoubleClick(False)
        mid.addWidget(self.tree, 1)
        bl.addLayout(mid, 1)
        bl.addWidget(AdBanner(), 0, Qt.AlignHCenter)
        lay.addWidget(body, 1)
        self.collapsed = set(self.app.cfg.get("collapsed", []))
        self.expanded = set(self.app.cfg.get("expanded", []))
        self._filling = False
        self.tree.itemExpanded.connect(self._toggled)
        self.tree.itemCollapsed.connect(self._toggled)
        self.scene.set_image(self.app.cfg.get("scene") or skin.path("scene_default.jpg"))

    def pick_scene(self):
        m = QMenu(self)
        for name, path in skin.scenes():
            m.addAction(name, lambda path=path: self.set_scene(path))
        m.exec(self.mapToGlobal(QPoint(20, 60)))

    def set_scene(self, path):
        self.app.cfg["scene"] = path
        save_config(self.app.cfg)
        self.scene.set_image(path)
        for w in self.app.convos.values():
            w.scene.set_image(path)

    def update_me(self):
        me = self.app.me
        st = me.get("status")
        label = STATUS.get(st if isinstance(st, str) else "online", STATUS["online"])[0]
        self.name.setText(f"{me.get('name', '')}  ({label}) ▾")
        self.name.setMinimumWidth(min(self.name.sizeHint().width(), max(120, self.width() - 110)))
        self.pic.setPixmap(framed(self.app.avatars.get(me.get("avatar")), 76))
        if not self.psm.hasFocus():
            self.psm.setText(me.get("psm", ""))

    def refill(self):
        q = self.search.text().strip().casefold()
        bar = self.tree.verticalScrollBar()
        keep = bar.value()
        self._filling = True
        self.tree.clear()
        if self.view == "contacts":
            self._fill_contacts(q)
        else:
            self._fill_server(q)
        self._filling = False
        bar.setValue(keep)
        self.sidebar.update()

    def _fill_contacts(self, q):
        friends = [c for c in self.app.contacts.values() if not c.get("stranger") and (
                   not q or q in c["name"].casefold() or q in c.get("username", "").casefold())]
        online = sorted((c for c in friends if c["status"] != "offline"), key=lambda c: c["name"].casefold())
        offline = sorted((c for c in friends if c["status"] == "offline"), key=lambda c: c["name"].casefold())
        groups = [g for g in self.app.groups if not q or q in g["name"].casefold()]
        recent = [r for r in self.app.recent if not q or q in r["name"].casefold()][:15]
        total = sum(1 for c in self.app.contacts.values() if not c.get("stranger"))
        sections = [("Recent", f"Recent conversations ({len(recent)})", [("contact", r) for r in recent]),
                    ("Online", f"Online ({len(online)}/{total})", [("contact", c) for c in online]),
                    ("Groups", f"Groups ({len(groups)})", [("groupdm", g) for g in groups]),
                    ("Offline", f"Offline ({len(offline)})", [("contact", c) for c in offline])]
        for key, title, rows in sections:
            if not rows and key in ("Groups", "Recent"):
                continue
            h = QTreeWidgetItem([title])
            h.setData(0, Qt.UserRole + 1, "group")
            h.setData(0, Qt.UserRole + 3, key)
            self.tree.addTopLevelItem(h)
            for kind, c in rows:
                self._row(h, kind, c)
            h.setExpanded(self.is_open(key))

    def _fill_server(self, q):
        g = next((g for g in self.app.guilds if g["id"] == self.view), None)
        if g is None:
            self.view = "contacts"
            return self._fill_contacts(q)
        chans = self.app.guild_channels.get(g["id"])
        if chans is None:
            self._row(self.tree.invisibleRootItem(), "loading", {"name": "Loading channels…"})
            return
        chans = [c for c in chans if not q or q in c["name"].casefold()]
        # Uncategorized channels first, then each category in Discord's order.
        cat = None
        for c in sorted(chans, key=lambda c: (c.get("categoryPos", -1), c.get("pos", 0))):
            c = dict(c, guild=g["id"], guildName=g["name"], icon=g.get("icon"))
            if not c.get("categoryId"):
                self._row(self.tree.invisibleRootItem(), "channel", c)
                continue
            if cat is None or cat.data(0, Qt.UserRole + 2)["id"] != c["categoryId"]:
                if cat is not None:
                    cat.setExpanded(self.is_open(cat.data(0, Qt.UserRole + 3)) or bool(q))
                cat = self._row(self.tree.invisibleRootItem(), "category",
                                {"id": c["categoryId"], "name": c.get("category", ""), "guild": g["id"]})
                cat.setData(0, Qt.UserRole + 3, f"cat:{g['id']}:{c['categoryId']}")
            self._row(cat, "channel", c)
        if cat is not None:
            cat.setExpanded(self.is_open(cat.data(0, Qt.UserRole + 3)) or bool(q))
        if not chans:
            self._row(self.tree.invisibleRootItem(), "loading", {"name": "No channels"})

    def show_view(self, view):
        self.view = view
        self.app.cfg["view"] = view
        save_config(self.app.cfg)
        g = next((g for g in self.app.guilds if g["id"] == view), None)
        self.search.setPlaceholderText(f"Search {g['name']}" if g else "Search contacts")
        if g and g["id"] not in self.app.guild_channels:
            self.app.load_channels(g["id"])
        self.refill()
        self.tree.verticalScrollBar().setValue(0)

    def _row(self, parent, kind, c):
        it = QTreeWidgetItem(parent, [c["name"]])
        it.setData(0, Qt.UserRole + 1, kind)
        it.setData(0, Qt.UserRole + 2, c)
        if kind == "contact":
            it.setToolTip(0, f"{c['name']} <{c.get('username', '')}>")
        elif kind == "channel":
            it.setToolTip(0, c.get("topic") or f"#{c['name']}")
        return it

    def is_open(self, key):
        if key.startswith("guild:"):
            return key in self.expanded
        return key not in self.collapsed

    def _toggled(self, it):
        if self._filling:
            return
        key = it.data(0, Qt.UserRole + 3)
        if not key:
            return
        if key.startswith("guild:"):
            (self.expanded.add if it.isExpanded() else self.expanded.discard)(key)
        else:
            (self.collapsed.discard if it.isExpanded() else self.collapsed.add)(key)
        self.app.cfg["collapsed"] = sorted(self.collapsed)
        self.app.cfg["expanded"] = sorted(self.expanded)
        save_config(self.app.cfg)

    def activate(self, it, _col=0):
        kind = it.data(0, Qt.UserRole + 1)
        c = it.data(0, Qt.UserRole + 2)
        if kind == "contact":
            self.app.open_contact(c["id"])
        elif kind == "groupdm":
            self.app.open_group(c)
        elif kind == "channel":
            self.app.open_channel(c)

    def closeEvent(self, e):
        # Like Messenger: closing the list keeps you signed in, in the tray.
        if self.app.tray and self.app.tray.isVisible() and not self.app.quitting:
            e.ignore()
            self.hide()


# ------------------------------------------------------- conversation window ---
class Log(QTextBrowser):
    """Conversation history. Remote images (attachments, embeds) load in the background;
    GIFs animate, and Tenor/Giphy 'GIFs' (really short muted videos) loop in place."""
    MAX_ANIMATED = 8

    def __init__(self, app):
        super().__init__(objectName="log", openExternalLinks=True)
        self.app = app
        self.images = {}
        self.pending = set()
        self.animated = []          # (key, QMovie or (player, sink)), oldest first
        self.videos = {}            # image key -> video url to loop there

    def loadResource(self, kind, url):
        if url.scheme() in ("http", "https"):
            key = url.toString()
            img = self.images.get(key)
            if img is not None:
                return img
            if key not in self.pending:
                self.pending.add(key)
                r = self.app.avatars.net.get(QNetworkRequest(url))
                r.finished.connect(lambda r=r, key=key: self._loaded(r, key))
            return QImage()
        return super().loadResource(kind, url)

    def _loaded(self, reply, key):
        self.pending.discard(key)
        data = bytes(reply.readAll())
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        err = reply.errorString() if reply.error() != reply.NetworkError.NoError else ""
        reply.deleteLater()
        img = QImage()
        if not img.loadFromData(data):
            if DEBUG:
                print(f"[image] failed {QUrl(key).host()} status={status} bytes={len(data)} "
                      f"head={data[:12]!r} {err}", file=sys.stderr)
            return
        self._set(key, img, relayout=True)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            self._animate_gif(key, data)
        elif key in self.videos:
            self._animate_video(key, self.videos[key])

    def _set(self, key, img, relayout=False):
        self.images[key] = img
        doc = self.document()
        doc.addResource(QTextDocument.ImageResource, QUrl(key), img)
        if relayout:
            bar = self.verticalScrollBar()
            at_end = bar.value() >= bar.maximum() - 4
            doc.markContentsDirty(0, doc.characterCount())
            if at_end:
                QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
        else:
            self.viewport().update()

    def _keep(self, key, anim):
        self.animated.append((key, anim))
        while len(self.animated) > self.MAX_ANIMATED:
            _k, old = self.animated.pop(0)
            if isinstance(old, tuple):
                old[0].stop()
            else:
                old.stop()

    def _animate_gif(self, key, data):
        from PySide6.QtCore import QBuffer, QByteArray as QBA
        from PySide6.QtGui import QMovie
        buf = QBuffer(self)
        buf.setData(QBA(data))
        buf.open(QBuffer.ReadOnly)
        movie = QMovie(buf, QByteArray(), self)
        if movie.frameCount() == 1:
            return
        movie.frameChanged.connect(lambda _n, key=key, movie=movie: self._set(key, movie.currentImage()))
        movie.start()
        self._keep(key, movie)

    def _animate_video(self, key, video_url):
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        except ImportError:
            return
        player = QMediaPlayer(self)
        sink = QVideoSink(self)
        player.setVideoSink(sink)
        player.setLoops(QMediaPlayer.Infinite)
        size = self.images[key].size()

        def frame(f, key=key):
            img = f.toImage()
            if not img.isNull():
                self._set(key, img.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        sink.videoFrameChanged.connect(frame)
        player.setSource(QUrl(video_url))
        player.play()
        self._keep(key, (player, sink))


def fit(w, h, max_w=320, max_h=240):
    if not w or not h:
        return max_w, 0
    k = min(1.0, max_w / w, max_h / h)
    return max(1, int(w * k)), max(1, int(h * k))


def attr_url(u):
    """A URL for an HTML attribute. Not entity-escaped: Qt keeps '&amp;' literally in
    img src, which breaks Discord's signed media links (404)."""
    return u.replace("'", "%27").replace('"', "%22").replace("<", "%3C").replace(">", "%3E")


def img_html(src, link, w, h, max_w=320, max_h=240):
    w, h = fit(w, h, max_w, max_h)
    size = f"width='{w}'" + (f" height='{h}'" if h else "")
    return f"<a href='{attr_url(link)}'><img src='{attr_url(src)}' {size}></a>"


class FrameGrabber(QObject):
    """One shared video player that fetches a still for each video GIF tile, in turn."""

    def __init__(self, parent):
        super().__init__(parent)
        self.queue = []
        self.current = None
        self.player = None
        self.timeout = QTimer(self, singleShot=True, interval=5000, timeout=self._next)

    def request(self, tile):
        self.queue.append(tile)
        if self.current is None:
            self._next()

    def _setup(self):
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        self.player = QMediaPlayer(self)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._frame)

    def _next(self):
        if self.player is None:
            try:
                self._setup()
            except ImportError:
                return
        self.player.stop()
        self.current = None
        while self.queue:
            t = self.queue.pop(0)
            if t.isVisible() or t.parent() is not None:
                self.current = t
                break
        if self.current is None:
            return
        self.player.setSource(QUrl(self.current.src))
        self.player.play()
        self.timeout.start()

    def _frame(self, f):
        if self.current is None:
            return
        img = f.toImage()
        if img.isNull():
            return
        try:
            self.current.set_still(img)
        except RuntimeError:          # tile was deleted (new search)
            pass
        self.timeout.stop()
        QTimer.singleShot(0, self._next)


class GifTile(QLabel):
    """One GIF in the picker: a still until hovered, then it plays."""
    picked = Signal(str)
    W = 104

    def __init__(self, picker, g):
        super().__init__()
        self.g = g
        self.picker = picker
        self.src = g.get("gif") or g.get("src")
        self.video = bool(g.get("video")) and not g.get("gif")
        self.data = None
        self.movie = None
        self.player = None
        h = max(40, min(150, int(self.W * (g.get("height") or 1) / max(1, g.get("width") or 1))))
        self.setFixedSize(self.W, h)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(g.get("title", ""))
        self.setStyleSheet("background:#e8f0f8; border:1px solid #c5d6e8;")
        if self.video:
            picker.grabber.request(self)
        else:
            r = picker.net.get(QNetworkRequest(QUrl(self.src)))
            r.finished.connect(lambda r=r: self._loaded(r))

    def set_still(self, img):
        self.setPixmap(QPixmap.fromImage(img).scaled(self.size(), Qt.KeepAspectRatioByExpanding,
                                                     Qt.SmoothTransformation))

    def _loaded(self, reply):
        self.data = bytes(reply.readAll())
        reply.deleteLater()
        img = QImage()
        if img.loadFromData(self.data):
            self.set_still(img)

    def enterEvent(self, _e):
        if self.video:
            try:
                from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
            except ImportError:
                return
            if self.player is None:
                self.player = QMediaPlayer(self)
                self._sink = QVideoSink(self)
                self.player.setVideoSink(self._sink)
                self.player.setLoops(QMediaPlayer.Infinite)
                self._sink.videoFrameChanged.connect(lambda f: (not f.toImage().isNull()) and
                                                     self.set_still(f.toImage()))
                self.player.setSource(QUrl(self.src))
            self.player.play()
            return
        if self.data and not self.movie:
            from PySide6.QtCore import QBuffer
            from PySide6.QtGui import QMovie
            buf = QBuffer(self)
            buf.setData(QByteArray(self.data))
            buf.open(QBuffer.ReadOnly)
            self.movie = QMovie(buf, QByteArray(), self)
            self.movie.setScaledSize(self.size())
            self.setMovie(self.movie)
        if self.movie:
            self.movie.start()

    def leaveEvent(self, _e):
        if self.movie:
            self.movie.setPaused(True)
        if self.player:
            self.player.pause()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.picked.emit(self.g["url"])


class GifPicker(QFrame):
    """GIFs, like Discord's picker: your favorites first, trending, or a search."""
    picked = Signal(str)
    PAGE = 48

    def __init__(self, app, parent=None):
        super().__init__(parent, Qt.Popup)
        self.app = app
        self.net = app.avatars.net
        self.grabber = FrameGrabber(self)
        self.setObjectName("gifpicker")
        self.setFixedSize(360, 440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.search = QLineEdit(objectName="search", placeholderText="Search GIFs", clearButtonEnabled=True)
        lay.addWidget(self.search)
        tabs = QHBoxLayout()
        tabs.setSpacing(2)
        self.tab_buttons = {}
        for key, text in (("fav", "★ Favorites"), ("trending", "Trending")):
            b = QToolButton(text=text, objectName="tool", checkable=True)
            b.clicked.connect(lambda _c=False, k=key: self.show_tab(k))
            tabs.addWidget(b)
            self.tab_buttons[key] = b
        tabs.addStretch(1)
        lay.addLayout(tabs)
        from PySide6.QtWidgets import QScrollArea
        self.area = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.area.verticalScrollBar().valueChanged.connect(self._scrolled)
        lay.addWidget(self.area, 1)
        self.status = QLabel("Loading…", objectName="convstatus")
        lay.addWidget(self.status)
        self.timer = QTimer(self, singleShot=True, interval=400, timeout=self.run)
        self.search.textChanged.connect(lambda _t: self.timer.start())
        self.items, self.shown = [], 0
        self.tab = "fav"
        self.show_tab("fav")

    def show_tab(self, key):
        self.tab = key
        for k, b in self.tab_buttons.items():
            b.setChecked(k == key)
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        self.run()

    def run(self):
        q = self.search.text().strip()
        if q:
            for b in self.tab_buttons.values():
                b.setChecked(False)
            self.status.setText("Searching…")
            self.app.bridge.call("gifs", {"q": q}, lambda r, q=q: self.show_results(r, q))
        elif self.tab == "fav":
            self.status.setText("Loading your favorites…")
            cached = getattr(self.app, "fav_gifs", None)
            if cached is not None:
                self.show_results(cached, "")
            self.app.bridge.call("favGifs", {}, self._favs)
        else:
            self.status.setText("Loading trending GIFs…")
            self.app.bridge.call("gifs", {"q": ""}, lambda r: self.show_results(r, ""))

    def _favs(self, favs):
        fresh = favs is not None and favs != getattr(self.app, "fav_gifs", None)
        self.app.fav_gifs = favs or []
        if self.tab == "fav" and not self.search.text().strip() and (fresh or not self.items):
            self.show_results(self.app.fav_gifs, "")

    def show_results(self, gifs, q):
        if q != self.search.text().strip():
            return
        self.items = [g for g in (gifs or []) if g.get("gif") or g.get("src")]
        self.shown = 0
        self.grabber.queue.clear()
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.cols = []
        for _ in range(3):
            c = QVBoxLayout()
            c.setSpacing(6)
            c.addStretch(1)
            row.addLayout(c, 0)
            self.cols.append(c)
        self.heights = [0, 0, 0]
        self.area.setWidget(holder)
        self.more()
        if not self.items:
            self.status.setText("No GIFs found." if q else
                                ("You don't have any favorite GIFs yet." if self.tab == "fav" and not q
                                 else "Couldn't load GIFs."))
        elif self.tab == "fav" and not q:
            self.status.setText(f"{len(self.items)} favorites")
        else:
            self.status.setText("")

    def more(self):
        for g in self.items[self.shown:self.shown + self.PAGE]:
            t = GifTile(self, g)
            t.picked.connect(self._pick)
            i = self.heights.index(min(self.heights))      # masonry: shortest column next
            self.cols[i].insertWidget(self.cols[i].count() - 1, t)
            self.heights[i] += t.height() + 6
        self.shown = min(len(self.items), self.shown + self.PAGE)

    def _scrolled(self, v):
        bar = self.area.verticalScrollBar()
        if self.shown < len(self.items) and v >= bar.maximum() - 200:
            self.more()

    def _pick(self, url):
        self.picked.emit(url)
        self.close()


class Conversation(QWidget):
    def __init__(self, app, channel_id, peer=None, title=None):
        super().__init__()
        self.app = app
        self.channel_id = channel_id
        self.peer = peer            # user id for 1:1 chats
        self.title = title
        self.guild = None           # server channel info, for channel windows
        self.last_typing_sent = 0
        self.setWindowIcon(icon("app.ico"))
        self.resize(560, 470)
        self.setMinimumSize(420, 340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.scene = Scene(64)
        sl = QVBoxLayout(self.scene)
        sl.setContentsMargins(12, 8, 12, 4)
        sl.setSpacing(0)
        self.who = QLabel(objectName="who")
        self.who_psm = QLabel(objectName="whopsm")
        sl.addWidget(self.who)
        sl.addWidget(self.who_psm)
        sl.addStretch(1)
        self.scene.set_image(app.cfg.get("scene") or skin.path("scene_default.jpg"))
        lay.addWidget(self.scene)

        body = Body()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(10, 4, 10, 6)
        bl.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(4)
        self.log = Log(app)
        left.addWidget(self.log, 1)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        nudge = QToolButton(text="Nudge", objectName="tool", toolTip="Send a nudge")
        nudge.clicked.connect(self.send_nudge)
        bar.addWidget(nudge)
        gif = QToolButton(text="GIF", objectName="tool", toolTip="Send a GIF")
        gif.clicked.connect(lambda: self.pick_gif(gif))
        bar.addWidget(gif)
        bar.addStretch(1)
        left.addLayout(bar)
        self.input = QTextEdit(objectName="input", acceptRichText=False)
        self.input.setFixedHeight(64)
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self.typed)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.input, 1)
        send = QPushButton("Send", objectName="send")
        send.setFixedHeight(64)
        send.clicked.connect(self.send)
        row.addWidget(send)
        left.addLayout(row)
        self.status = QLabel(objectName="convstatus")
        left.addWidget(self.status)
        bl.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(0)
        self.their_pic = QLabel()
        self.their_pic.setFixedSize(120, 120)
        self.my_pic = QLabel()
        self.my_pic.setFixedSize(120, 120)
        right.addWidget(self.their_pic)
        right.addStretch(1)
        right.addWidget(self.my_pic)
        bl.addLayout(right)
        lay.addWidget(body, 1)
        self.typing_timer = QTimer(self, singleShot=True, interval=6000, timeout=self.show_last_time)
        self.oldest_id = None
        self.loading_older = False
        self.no_more = False
        self.log.verticalScrollBar().valueChanged.connect(lambda v: self.load_older() if v == 0 else None)
        self.last_received = None
        self.refresh()

    # --- header / pictures
    def refresh(self):
        c = self.app.contacts.get(self.peer) if self.peer else None
        if c:
            label = STATUS.get(c["status"], STATUS["offline"])[0]
            self.who.setText(f"<b>{html.escape(c['name'])}</b> <span style='color:#6d7f95'>({label})</span>")
            self.who_psm.setText(html.escape(c.get("psm") or c.get("playing") or c.get("username", "")))
            self.setWindowTitle(c["name"])
            self.their_pic.setPixmap(framed(self.app.avatars.get(c.get("avatar")), 120))
        else:
            g = self.guild or {}
            self.who.setText(f"<b>{html.escape(self.title or 'Group conversation')}</b>"
                             + (f" <span style='color:#6d7f95'>({html.escape(g['guildName'])})</span>"
                                if g.get("guildName") else ""))
            self.who_psm.setText(html.escape(g.get("topic", ""))[:200])
            self.setWindowTitle(f"{self.title} - {g['guildName']}" if g.get("guildName") else
                                (self.title or "Group conversation"))
            self.their_pic.setPixmap(framed(self.app.avatars.get(g.get("icon")), 120))
        self.my_pic.setPixmap(framed(self.app.avatars.get(self.app.me.get("avatar")), 120))

    # --- log
    def render(self, m):
        """One message as HTML (no side effects)."""
        name = "You" if m.get("mine") else m.get("authorName", "")
        text = m.get("content", "")
        if self.is_nudge(text):
            line = "You have just sent a nudge." if m.get("mine") else f"{html.escape(name)} just sent you a nudge."
            return f"<p style='color:#6d7f95; margin:6px 0'>{line}</p>"
        body = self.app.format(text)
        embeds = m.get("embeds", [])
        # A bare link whose preview is just the picture: show the picture, not the link.
        if len(embeds) == 1 and embeds[0]["type"] in ("image", "gifv") and text.strip() == embeds[0]["url"]:
            body = ""
        for a in m.get("attachments", []):
            if a.get("type", "").startswith("image/") or re.search(r"\.(png|jpe?g|gif|webp)$", a["name"], re.I):
                is_gif = a.get("type") == "image/gif" or a["name"].lower().endswith(".gif")
                src = a["proxy"] if is_gif else a["proxy"] + ("&" if "?" in a["proxy"] else "?") + "format=png&width=640"
                body += ("<br>" if body else "") + img_html(src, a["url"], a.get("width"), a.get("height"))
            else:
                body += (f"<br><a href='{attr_url(a['url'])}' style='color:#2e64a8'>"
                         f"📎 {html.escape(a['name'])}</a>")
        for e in embeds:
            body += ("<br>" if body else "") + self.embed_html(e)
        for s in m.get("stickers", []):
            body += f"<br><i style='color:#6d7f95'>[sticker: {html.escape(s)}]</i>"
        says = f"{html.escape(name)} says:"
        return (f"<p style='color:#6d7f95; margin:8px 0 0 0'>{says}</p>"
                f"<p style='margin:1px 0 0 14px; color:#000'>{body}</p>")

    @staticmethod
    def is_nudge(text):
        return NUDGE_MARK in text or text.strip() == NUDGE_TEXT.replace(NUDGE_MARK, "")

    def add(self, m, sound=True):
        """Append a message; sound=False for history (no shake, no noise)."""
        if self.oldest_id is None and m.get("id"):
            self.oldest_id = m["id"]
        self._append(self.render(m))
        if self.is_nudge(m.get("content", "")):
            if not m.get("mine") and sound:
                self.shake()
                self.app.sounds.play("nudge")
            return
        if not m.get("mine"):
            self.last_received = m.get("time") / 1000 if m.get("time") else time.time()
            self.show_last_time()

    def load_older(self):
        if self.loading_older or self.no_more or not self.oldest_id:
            return
        self.loading_older = True
        self.app.bridge.call("older", {"channelId": self.channel_id, "before": self.oldest_id}, self._older)

    def _older(self, msgs):
        self.loading_older = False
        if not msgs:
            self.no_more = True
            return
        self.oldest_id = msgs[0]["id"]
        bar = self.log.verticalScrollBar()
        before = bar.maximum() - bar.value()
        cur = QTextCursor(self.log.document())
        cur.movePosition(QTextCursor.Start)
        cur.insertHtml("".join(self.render(m) for m in msgs) + "<br>")
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum() - before))
        if len(msgs) < 50:
            self.no_more = True

    def embed_html(self, e):
        fmt = self.app.format
        if e["type"] in ("image", "gifv") and (e.get("thumbnail") or e.get("image")):
            im = e.get("image") or e.get("thumbnail")
            if e["type"] == "gifv" and e.get("video"):
                self.log.videos[im["url"]] = e["video"]["url"]
            return img_html(im["url"], e["url"] or im["url"], im["width"], im["height"])
        parts = []
        if e.get("provider"):
            parts.append(f"<span style='color:#6d7f95; font-size:8pt'>{html.escape(e['provider'])}</span>")
        if e.get("author"):
            parts.append(f"<b style='font-size:9pt'>{html.escape(e['author'])}</b>")
        if e.get("title"):
            t = html.escape(e["title"])
            parts.append(f"<a href='{attr_url(e['url'])}' style='color:#2e64a8; font-weight:bold'>{t}</a>"
                         if e.get("url") else f"<b>{t}</b>")
        if e.get("description"):
            d = e["description"]
            parts.append(fmt(d[:500] + ("…" if len(d) > 500 else "")))
        for f in e.get("fields", []):
            parts.append(f"<b>{html.escape(f['name'])}</b><br>{fmt(f['value'])}")
        im = e.get("image") or (e.get("thumbnail") if e["type"] in ("video", "article", "link", "rich") else None)
        if im:
            parts.append(img_html(im["url"], e.get("url") or im["url"], im["width"], im["height"], 280, 200))
        if e.get("footer"):
            parts.append(f"<span style='color:#6d7f95; font-size:8pt'>{html.escape(e['footer'])}</span>")
        if not parts:
            return ""
        bar = e.get("color") or "#a9c3de"
        return (f"<table cellspacing='0' cellpadding='0' style='margin-top:4px'><tr>"
                f"<td width='4' bgcolor='{html.escape(bar)}'></td>"
                f"<td bgcolor='#f3f7fb' style='padding:6px 10px'>{'<br>'.join(parts)}</td></tr></table>")

    def _append(self, h):
        self.log.moveCursor(QTextCursor.End)
        self.log.insertHtml(h)
        self.log.append("")
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def show_last_time(self):
        if self.last_received:
            t = time.localtime(self.last_received)
            self.status.setText(time.strftime("Last message received at %-I:%M %p on %-m/%-d/%Y.", t))
        else:
            self.status.setText("")

    def show_typing(self, name):
        self.status.setText(f"{name} is writing a message...")
        self.typing_timer.start()

    # --- sending
    def eventFilter(self, obj, e):
        if obj is self.input and e.type() == e.Type.KeyPress and e.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not e.modifiers() & Qt.ShiftModifier:
            self.send()
            return True
        return False

    def typed(self):
        now = time.time()
        if self.input.toPlainText() and now - self.last_typing_sent > 8:
            self.last_typing_sent = now
            self.app.bridge.call("typing", {"channelId": self.channel_id})

    def send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.last_typing_sent = 0
        self.app.bridge.call("send", {"channelId": self.channel_id, "text": text})

    def pick_gif(self, button):
        p = GifPicker(self.app, self)
        p.picked.connect(lambda url: self.app.bridge.call("send", {"channelId": self.channel_id, "text": url}))
        pos = button.mapToGlobal(QPoint(0, 0))
        p.move(pos.x(), pos.y() - p.height() - 4)
        p.show()
        p.search.setFocus()

    def send_nudge(self):
        now = time.time()
        if now - getattr(self, "_last_nudge", 0) < 5:
            return
        self._last_nudge = now
        self.app.bridge.call("send", {"channelId": self.channel_id, "text": NUDGE_TEXT})
        self.shake()
        self.app.sounds.play("nudge")

    def shake(self):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        start = self.pos()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(700)
        offsets = [(-10, -4), (8, 6), (-6, 4), (10, -6), (-8, 2), (6, -2), (-4, 4), (2, -2), (0, 0)]
        for i, (dx, dy) in enumerate(offsets):
            anim.setKeyValueAt((i + 1) / len(offsets), start + QPoint(dx, dy))
        anim.setStartValue(start)
        anim.start()

    def closeEvent(self, e):
        self.app.convos.pop(self.channel_id, None)
        if self.guild:
            self.app.bridge.call("watch", {"channelId": self.channel_id, "on": False})
        super().closeEvent(e)


# ------------------------------------------------------------------ toast ---
class Toast(QWidget):
    """Messenger's sign-in / new message popup in the screen corner."""
    live = []

    def __init__(self, app, picture, title, text, on_click=None):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.on_click = on_click
        self.setFixedSize(240, 108)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 22, 12, 10)
        lay.setSpacing(8)
        pic = QLabel()
        pic.setPixmap(framed(picture, 64))
        lay.addWidget(pic, 0, Qt.AlignTop)
        lbl = QLabel(f"<b>{html.escape(title)}</b><br><span style='color:#1e395b'>{html.escape(text)}</span>",
                     wordWrap=True, objectName="toasttext")
        lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(lbl, 1)
        scr = QApplication.primaryScreen().availableGeometry()
        n = len(Toast.live)
        self.target = QPoint(scr.right() - self.width() - 8, scr.bottom() - (self.height() + 6) * (n + 1))
        self.move(self.target.x(), scr.bottom())
        Toast.live.append(self)
        self.show()
        self.anim = QPropertyAnimation(self, b"pos", self)
        self.anim.setDuration(350)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(self.target)
        self.anim.start()
        QTimer.singleShot(5500, self.close)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#dff0fc"))
        g.setColorAt(0.25, QColor("#ffffff"))
        g.setColorAt(1, QColor("#e6f1fa"))
        p.setPen(QPen(QColor("#7fa9d3")))
        p.setBrush(g)
        p.drawRoundedRect(r, 5, 5)
        p.setFont(font(11))
        p.setPen(GRAY)
        p.drawText(QRect(10, 3, 220, 16), Qt.AlignVCenter, APP)

    def mousePressEvent(self, _e):
        if self.on_click:
            self.on_click()
        self.close()

    def closeEvent(self, e):
        if self in Toast.live:
            Toast.live.remove(self)
        super().closeEvent(e)


# ------------------------------------------------------------- the app ---
class Messenger(QObject):
    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.cfg = load_config()
        self.quitting = False
        self.me = {}
        self.contacts = {}
        self.groups = []
        self.guilds = []
        self.recent = []            # recent 1:1 DMs, newest first (friends or not)
        self.guild_channels = {}
        self.convos = {}
        self.signed_in = False
        self.sounds = Sounds()
        self.avatars = Avatars()
        self.avatars.changed.connect(self._avatar_ready)
        self.bridge = Bridge()
        self.bridge.ready.connect(self.on_ready)
        self.bridge.snapshot.connect(self.on_snapshot)
        self.bridge.message.connect(self.on_message)
        self.bridge.typing.connect(self.on_typing)
        self.bridge.presence.connect(self.on_presence)
        self.bridge.login.connect(self.on_login)
        self.list = ContactList(self)
        self.list.name.setText("Signing in...")
        self.list.pic.setPixmap(framed(self.avatars.default, 76))
        self.list.show()
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(icon("app.ico") if not icon("app.ico").isNull() else qapp.windowIcon())
            m = QMenu()
            m.addAction("Open " + APP, self.show_list)
            m.addSeparator()
            for st in ("online", "dnd", "idle", "invisible"):
                m.addAction(status_icon(st), STATUS[st][0], lambda st=st: self.set_status(st))
            m.addSeparator()
            m.addAction("Exit", self.quit)
            self.tray.setContextMenu(m)
            self.tray.activated.connect(lambda r: self.show_list() if r == QSystemTrayIcon.Trigger else None)
            self.tray.setToolTip(APP)
            self.tray.show()
        self._login_shown = False

    # --- bridge events
    def on_login(self):
        if not self._login_shown:
            self._login_shown = True
            self.list.name.setText("Sign in to Discord")
            self.bridge.show_login()

    def on_ready(self, d):
        self.signed_in = True
        self.on_snapshot(d, initial=True)

    def on_snapshot(self, d, initial=False):
        self.me = d.get("me") or self.me
        self.contacts = {c["id"]: c for c in d.get("friends", [])}
        self.groups = d.get("groups", [])
        self.guilds = d.get("guilds", self.guilds)
        self.recent = d.get("recent", self.recent)
        # People you've talked to but aren't friends with still need names and pictures.
        for r in self.recent:
            self.contacts.setdefault(r["id"], dict(r, stranger=True))
        if initial and self.cfg.get("view") not in (None, "contacts") and any(
                g["id"] == self.cfg["view"] for g in self.guilds):
            self.list.show_view(self.cfg["view"])
        self.list.update_me()
        self.list.refill()
        for w in self.convos.values():
            w.refresh()

    def on_presence(self, c):
        if c["id"] == self.me.get("id"):
            self.me.update(c)
            self.list.update_me()
            return
        old = self.contacts.get(c["id"])
        if not old:
            return
        was = old["status"]
        self.contacts[c["id"]] = c
        if was == "offline" and c["status"] != "offline":
            self.sounds.play("online")
            Toast(self, self.avatars.get(c.get("avatar")), c["name"], "has just signed in.",
                  lambda cid=c["id"]: self.open_contact(cid))
        self.list.refill()
        for w in self.convos.values():
            if w.peer == c["id"]:
                w.refresh()

    def on_typing(self, t):
        w = self.convos.get(t["channelId"])
        if w:
            w.show_typing(t["name"])

    def on_message(self, m):
        w = self.convos.get(m["channelId"])
        if m.get("guild") and w is None:
            # Not open: only mentions get a popup.
            if m.get("mentioned"):
                self.sounds.play("newalert")
                c = {"id": m["channelId"], "name": m.get("channelName", ""), "guild": m["guild"],
                     "guildName": m.get("guildName", ""),
                     "icon": next((g.get("icon") for g in self.guilds if g["id"] == m["guild"]), None)}
                Toast(self, self.avatars.get(self.contacts.get(m.get("author"), {}).get("avatar")),
                      m.get("authorName", ""), f"mentioned you in #{c['name']}: " + self._preview(m),
                      lambda c=c: self.open_channel(c))
            return
        if w is None and not m.get("mine"):
            if m.get("peer"):
                w = self._convo(m["channelId"], peer=m["peer"])
                w.show()
                w.add(m)
            else:
                g = next((g for g in self.groups if g["id"] == m["channelId"]), None)
                w = self._convo(m["channelId"], title=g["name"] if g else "Group conversation")
                w.show()
                w.add(m)
            self.sounds.play("type")
            if not w.isActiveWindow():
                Toast(self, self.avatars.get(self.contacts.get(m.get("author"), {}).get("avatar")),
                      m.get("authorName", ""), "says: " + self._preview(m), lambda w=w: (w.show(), w.raise_(),
                                                                                         w.activateWindow()))
            return
        if w is None:
            return
        w.add(m)
        if not m.get("mine"):
            if NUDGE_MARK not in m.get("content", "") and (not m.get("guild") or m.get("mentioned")):
                self.sounds.play("type")
            if not w.isActiveWindow():
                QApplication.alert(w)

    def _preview(self, m):
        t = m.get("content", "")
        if NUDGE_MARK in t:
            return "a nudge!"
        return (t[:80] + "…") if len(t) > 80 else (t or "(attachment)")

    def _avatar_ready(self, _url):
        self.list.update_me()
        self.list.tree.viewport().update()
        self.list.sidebar.update()
        for w in self.convos.values():
            w.refresh()

    # --- opening conversations
    def _convo(self, channel_id, peer=None, title=None):
        w = Conversation(self, channel_id, peer, title)
        self.convos[channel_id] = w
        return w

    def open_contact(self, user_id):
        for w in self.convos.values():
            if w.peer == user_id:
                w.show()
                w.raise_()
                w.activateWindow()
                return

        def opened(r):
            if not r:
                QMessageBox.warning(self.list, APP, "Couldn't open the conversation.")
                return
            w = self.convos.get(r["channelId"]) or self._convo(r["channelId"], peer=user_id)
            for m in r["messages"]:
                w.add(m, sound=False)
            w.show()
            w.activateWindow()
            w.input.setFocus()
        self.bridge.call("open", {"userId": user_id}, opened)

    def open_group(self, g):
        if g["id"] in self.convos:
            w = self.convos[g["id"]]
            w.show()
            w.raise_()
            return

        def opened(r):
            if not r:
                return
            w = self._convo(g["id"], title=g["name"])
            for m in r["messages"]:
                w.add(m, sound=False)
            w.show()
        self.bridge.call("openGroup", {"channelId": g["id"]}, opened)

    # --- servers
    def load_channels(self, guild_id):
        def got(chans):
            self.guild_channels[guild_id] = chans or []
            self.list.refill()
        self.bridge.call("channels", {"guildId": guild_id}, got)

    def open_channel(self, c):
        if c["id"] in self.convos:
            w = self.convos[c["id"]]
            w.show()
            w.raise_()
            w.activateWindow()
            return

        def opened(r):
            if not r:
                return
            w = self._convo(c["id"], title="#" + c["name"])
            w.guild = c
            self.bridge.call("watch", {"channelId": c["id"], "on": True})
            for m in r["messages"]:
                w.add(m, sound=False)
            w.refresh()
            w.show()
            w.activateWindow()
            w.input.setFocus()
        self.bridge.call("openGroup", {"channelId": c["id"]}, opened)

    # --- my status
    def set_status(self, st):
        self.me["status"] = st
        self.list.update_me()
        self.bridge.call("status", {"status": st},
                         lambda ok: None if ok else QMessageBox.warning(self.list, APP, "Couldn't change your status."))

    def set_psm(self, text):
        if text == self.me.get("psm", ""):
            return
        self.me["psm"] = text
        self.bridge.call("psm", {"text": text})

    def show_list(self):
        self.list.show()
        self.list.raise_()
        self.list.activateWindow()

    def quit(self):
        self.quitting = True
        self.qapp.quit()

    # --- message text -> HTML (Discord markdown, lightly)
    def format(self, text):
        t = html.escape(text)
        t = re.sub(r"(https?://[^\s<]+)", r"<a href='\1' style='color:#2e64a8'>\1</a>", t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
        t = re.sub(r"__(.+?)__", r"<u>\1</u>", t)
        t = re.sub(r"~~(.+?)~~", r"<s>\1</s>", t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)

        def mention(m):
            c = self.contacts.get(m.group(1))
            name = c["name"] if c else ("you" if m.group(1) == self.me.get("id") else "someone")
            return f"<b style='color:#2e64a8'>@{html.escape(name)}</b>"
        t = re.sub(r"&lt;@!?(\d+)&gt;", mention, t)
        t = re.sub(r"&lt;a?:(\w+):\d+&gt;", r":\1:", t)
        return t.replace("\n", "<br>")


QSS = """
* { font-family: "Segoe UI"; font-size: 9pt; color: #1e395b; }
QWidget#me { }
QToolButton#me { border: 1px solid transparent; border-radius: 3px; padding: 1px 4px; font-size: 11pt;
                 background: transparent; }
QToolButton#me:hover { border-color: rgba(120,160,200,160); background: rgba(255,255,255,120); }
QToolButton#me::menu-indicator { image: none; }
QLineEdit#psm { border: 1px solid transparent; background: transparent; padding: 1px 4px; color: #3b5778; }
QLineEdit#psm:hover { border-color: rgba(120,160,200,160); background: rgba(255,255,255,140); }
QLineEdit#psm:focus { border-color: #7fa9d3; background: white; }
QLineEdit#search { border: 1px solid #a9c3de; border-radius: 2px; padding: 3px 6px; background: white;
                   color: #1e395b; }
QLineEdit#search:focus { border-color: #5e9bd6; }
QTreeWidget#contacts { border: none; background: transparent; outline: 0; }
QLabel#who { font-size: 11pt; }
QLabel#whopsm { color: #3b5778; }
QTextBrowser#log { border: 1px solid #a9c3de; background: white; padding: 4px; }
QTextEdit#input { border: 1px solid #a9c3de; background: white; padding: 2px; font-size: 10pt; color: #000; }
QTextEdit#input:focus { border-color: #5e9bd6; }
QPushButton#send { border: 1px solid #7fa9d3; border-radius: 3px; padding: 0 14px;
                   background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #fdfeff, stop:0.5 #e6f1fb,
                                               stop:0.51 #d3e6f8, stop:1 #e8f3fc); }
QPushButton#send:hover { border-color: #4f8fd0; }
QPushButton#send:pressed { background: #cfe3f6; }
QToolButton#tool { border: 1px solid transparent; border-radius: 3px; padding: 2px 6px; background: transparent; }
QToolButton#tool:hover { border-color: #a9c3de; background: #eef6fd; }
QLabel#convstatus { color: #6d7f95; font-size: 8pt; }
QFrame#gifpicker { background: white; border: 1px solid #97b3d1; }
QLabel#toasttext { color: #1e395b; background: transparent; }
QMenu { background: white; border: 1px solid #97b3d1; padding: 2px; }
QMenu::item { padding: 4px 22px 4px 26px; }
QMenu::item:selected { background: #dcebf9; border: 1px solid #a8cbeb; color: #1e395b; }
QScrollBar:vertical { width: 12px; background: transparent; }
QScrollBar::handle:vertical { background: #c9dcef; border: 1px solid #a9c3de; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


def ensure_skin(parent=None):
    """First run: build the Messenger look from an installer the user has, or download it with permission."""
    if skin.installed():
        return
    inst = skin.find_installer()
    if not inst:
        ans = QMessageBox.question(
            parent, APP,
            "Halcyon Messenger takes its look and sounds from Windows Live Messenger 2009.\n\n"
            "No Messenger installer was found in your Downloads. Download the 2009 installer "
            "(141 MB, from the Internet Archive) and import it?\n\nWithout it, Messenger runs with a plain look.",
            QMessageBox.Yes | QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        dlg = QProgressDialog("Downloading Messenger 2009…", None, 0, 100, parent)
        dlg.setWindowTitle(APP)
        dlg.show()
        try:
            inst = skin.download_installer(lambda pct: (dlg.setValue(pct), QApplication.processEvents()))
        except OSError as e:
            dlg.close()
            QMessageBox.warning(parent, APP, f"Download failed: {e}")
            return
        dlg.close()
    dlg = QProgressDialog("Importing Messenger's look…", None, 0, 0, parent)
    dlg.setWindowTitle(APP)
    dlg.show()
    QApplication.processEvents()
    try:
        skin.import_from(inst, step=lambda msg: (dlg.setLabelText(msg), QApplication.processEvents()))
    except Exception as e:
        QMessageBox.warning(parent, APP, f"Couldn't import Messenger's look:\n{e}")
    dlg.close()


def main():
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-compositing")
    # Wayland doesn't let apps place their windows; sign-in popups need the screen corner.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("halcyon-messenger")
    qapp.setDesktopFileName("halcyon-messenger")
    qapp.setQuitOnLastWindowClosed(False)
    ensure_skin()
    for f in sorted((skin.SKIN / "fonts").glob("*.ttf")) if (skin.SKIN / "fonts").exists() else []:
        QFontDatabase.addApplicationFont(str(f))
    qapp.setStyleSheet(QSS)
    if skin.path("app.ico"):
        qapp.setWindowIcon(QIcon(skin.path("app.ico")))
    m = Messenger(qapp)
    m.list.destroyed.connect(qapp.quit)
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
