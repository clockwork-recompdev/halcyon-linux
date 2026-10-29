"""Halcyon Desktop Gadgets: runs Windows Vista/7 Sidebar gadgets (and Halcyon's
own, which are written the same way) on the desktop.

    halcyon-gadgets               open the gadget gallery
    halcyon-gadgets --daemon      restore the desktop's gadgets (session autostart)
    halcyon-gadgets FILE.gadget   install a gadget and open the gallery
"""
import json
import os
import urllib.parse
import secrets
import sys
import threading
from pathlib import Path

if os.environ.get("DISPLAY"):
    os.environ["QT_QPA_PLATFORM"] = "xcb"        # gadgets need to sit below windows at fixed spots

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings,
                                     QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from . import package, x11
from .server import TOKEN, Server

NAME = f"halcyon-gadgets-{os.getuid()}"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "halcyon" / "gadgets.json"
PROFILE_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "gadget-profile"
TOOLBAR = 22          # the hover toolbar to the right of a gadget, as on Windows 7
FIRST_RUN = ("Clock", "Calendar", "CPU Meter")


class Bridge(QObject):
    """Requests from the server thread, delivered on the GUI thread."""
    resize = Signal(str, str, int, int)
    close = Signal(str)
    drag = Signal(str)
    save = Signal()
    choose = Signal(object)


class Instance:
    def __init__(self, gadget, data=None):
        d = data or {}
        self.id = d.get("id") or secrets.token_hex(4)
        self.gadget = gadget
        self.x, self.y = d.get("x"), d.get("y")
        self.docked = d.get("docked", True)
        self.opacity = d.get("opacity", 100)
        self.ontop = d.get("ontop", False)
        self.settings = d.get("settings", {})
        self.pending_popup = None
        self.window = None

    def state(self):
        return {"docked": self.docked, "name": self.gadget.name, "version": self.gadget.version}

    def to_json(self):
        return {"id": self.id, "gadget": str(self.gadget.root), "x": self.x, "y": self.y, "docked": self.docked,
                "opacity": self.opacity, "ontop": self.ontop, "settings": self.settings}


# ---------------------------------------------------------------- gimage: ---
class GImage(QWebEngineUrlSchemeHandler):
    """Windows' gimage:///<path>?width=W&height=H: a picture scaled to fit a box
    (Slide Show and photo gadgets use it for thumbnails)."""
    def __init__(self, host):
        super().__init__(host)
        self.host = host

    def requestStarted(self, job):
        url = job.requestUrl()
        raw = urllib.parse.unquote(url.toString(QUrl.FullyEncoded).split(":", 1)[1].lstrip("/").split("?")[0])
        q = urllib.parse.parse_qs(url.query())
        path = self.host.server.any_path(raw)
        img = QImage(path) if path else QImage()
        if img.isNull():
            job.fail(QWebEngineUrlRequestJob.UrlNotFound)
            return
        def dim(k):
            try:
                v = int(float((q.get(k) or ["0"])[0]))
                return v if 0 < v < 8192 else 0
            except ValueError:                           # e.g. width=NaN from a gadget's maths
                return 0
        w, h = dim("width"), dim("height")
        if w and h:
            img = img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        buf = QBuffer(job)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        buf.open(QIODevice.ReadOnly)
        job.reply(QByteArray(b"image/png"), buf)


# ------------------------------------------------------------------ pages ---
class Page(QWebEnginePage):
    def __init__(self, host, inst, kind, parent=None):
        super().__init__(host.profile, parent)
        self.host, self.inst, self.kind = host, inst, kind
        self.setBackgroundColor(Qt.white if kind == "settings" else Qt.transparent)

    def acceptNavigationRequest(self, url, nav_type, is_main):
        # Links to websites open in the browser, as they did on Windows.
        if url.scheme() in ("http", "https", "mailto") and not url.toString().startswith(self.host.server.origin):
            if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked or not is_main:
                if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                    QDesktopServices.openUrl(url)
                    return False
            elif is_main:
                QDesktopServices.openUrl(url)
                return False
        return super().acceptNavigationRequest(url, nav_type, is_main)

    def createWindow(self, _type):
        kind, self.inst.pending_popup = self.inst.pending_popup, None
        if kind == "settings":
            return self.host.open_settings_window(self.inst)
        if kind == "flyout":
            return self.host.open_flyout(self.inst)
        return External(self.host.profile, self.host)

    def javaScriptConsoleMessage(self, level, message, line, source):
        if os.environ.get("HALCYON_GADGETS_DEBUG"):
            print(f"[{self.inst.gadget.name}:{self.kind}] {source.rsplit('/', 1)[-1]}:{line}: {message}",
                  file=sys.stderr, flush=True)


class External(QWebEnginePage):
    """window.open() to a website: hand it to the browser."""
    def __init__(self, profile, host):
        super().__init__(profile, host.app)
        self.urlChanged.connect(self._go)

    def _go(self, url):
        if url.isValid() and not url.isEmpty():
            QDesktopServices.openUrl(url)
            self.deleteLater()


def make_view(page, parent):
    v = QWebEngineView(parent)
    v.setPage(page)
    v.setAttribute(Qt.WA_TranslucentBackground)
    v.setStyleSheet("background: transparent")
    v.setContextMenuPolicy(Qt.NoContextMenu)
    return v


# ---------------------------------------------------------- gadget window ---
class GadgetWindow(QWidget):
    def __init__(self, host, inst):
        super().__init__(None, self._flags(inst))
        self.host, self.inst = host, inst
        self.setWindowTitle(inst.gadget.name)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.page = Page(host, inst, "gadget", self)
        self.page.windowCloseRequested.connect(lambda: host.remove(inst))
        self.view = make_view(self.page, self)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.menu)
        self.content = QSize(130, 150)
        self.hover = False
        self.info = {"settingsUI": "", "undockable": False}
        self._hover_timer = QTimer(self, interval=150, timeout=self._check_hover)
        self.buttons = {}
        self._layout()
        self.page.load(QUrl(host.server.url(inst.id, inst.gadget.base_url_path())))
        self.page.loadFinished.connect(lambda _ok: self.refresh_info())
        if os.environ.get("HALCYON_GADGETS_DEBUG"):
            self.page.loadFinished.connect(lambda ok: print(f"[{inst.gadget.name}] loaded ok={ok}", flush=True))
            self.page.renderProcessTerminated.connect(
                lambda status, code: print(f"[{inst.gadget.name}] renderer ended {status} {code}", flush=True))
        test = os.environ.get("HALCYON_GADGETS_TESTJS")          # development: poke at gadgets
        if test:
            self.page.loadFinished.connect(lambda _ok: QTimer.singleShot(1500, lambda: self.page.runJavaScript(test)))
        self.apply_opacity()

    @staticmethod
    def _flags(inst):
        f = Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        return f | (Qt.WindowStaysOnTopHint if inst.ontop else Qt.WindowStaysOnBottomHint)

    def _layout(self):
        w, h = self.content.width(), self.content.height()
        self.view.setGeometry(0, 0, w, h)
        self.resize(w + TOOLBAR, max(h, 4 * 20 + 8))
        x = w + 2
        self.buttons = {k: QRect(x, 4 + i * 20, 18, 18) for i, k in enumerate(("close", "size", "options", "grip"))}

    def set_content_size(self, w, h):
        w, h = max(16, min(w, 2000)), max(16, min(h, 2000))
        if (w, h) != (self.content.width(), self.content.height()):
            self.content = QSize(w, h)
            self._layout()
            self.update()

    def refresh_info(self):
        self.page.runJavaScript("window.__halcyon ? __halcyon.info() : null", 0, self._got_info)

    def _got_info(self, info):
        if isinstance(info, dict):
            self.info = info
            self.update()

    def apply_opacity(self):
        self.setWindowOpacity(1.0 if self.hover else max(0.2, self.inst.opacity / 100))

    # --- hover toolbar
    def enterEvent(self, e):
        if not self.hover:
            self.hover = True
            self.refresh_info()
            self.apply_opacity()
            self.update()
        self._hover_timer.start()

    def _check_hover(self):
        if not self.geometry().contains(QCursor.pos()):
            self._hover_timer.stop()
            self.hover = False
            self.apply_opacity()
            self.update()

    def visible_buttons(self):
        out = ["close"]
        if self.info.get("undockable"):
            out.append("size")
        if self.info.get("settingsUI"):
            out.append("options")
        out.append("grip")
        return out

    def paintEvent(self, _e):
        if not self.hover:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        names = self.visible_buttons()
        x = self.content.width() + 1
        strip = QRectF(x, 2, TOOLBAR - 2, 6 + 20 * len(names))
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.setBrush(QColor(20, 30, 42, 190))
        p.drawRoundedRect(strip, 5, 5)
        cur = self.mapFromGlobal(QCursor.pos())
        for i, name in enumerate(names):
            r = QRectF(x + 2, 5 + i * 20, 16, 16)
            if r.contains(cur):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(120, 190, 255, 90))
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 3, 3)
            p.setPen(QPen(QColor(235, 242, 250), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            c = r.center()
            if name == "close":
                p.drawLine(c + QPoint(-4, -4), c + QPoint(4, 4))
                p.drawLine(c + QPoint(-4, 4), c + QPoint(4, -4))
            elif name == "size":
                # Arrow pointing out = larger size, pointing in = smaller.
                p.drawLine(c + QPoint(-4, 4), c + QPoint(4, -4))
                if self.inst.docked:
                    tip = c + QPoint(4, -4)
                    p.drawLine(tip, tip + QPoint(-4, 0))
                    p.drawLine(tip, tip + QPoint(0, 4))
                else:
                    tip = c + QPoint(-4, 4)
                    p.drawLine(tip, tip + QPoint(4, 0))
                    p.drawLine(tip, tip + QPoint(0, -4))
            elif name == "options":
                # wrench
                path = QPainterPath()
                path.addEllipse(QRectF(c.x() + 0.5, c.y() - 5.5, 5, 5))
                p.drawPath(path)
                p.drawLine(c + QPoint(1, -1), c + QPoint(-5, 5))
            elif name == "grip":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(235, 242, 250, 200))
                for gy in (-4, 0, 4):
                    for gx in (-2, 2):
                        p.drawEllipse(c + QPoint(gx, gy), 1.2, 1.2)

    def mouseMoveEvent(self, e):
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position()
        names = self.visible_buttons()
        x = self.content.width() + 3
        for i, name in enumerate(names):
            if QRectF(x, 5 + i * 20, 16, 16).contains(pos):
                if name == "close":
                    self.host.remove(self.inst)
                elif name == "size":
                    self.host.toggle_docked(self.inst)
                elif name == "options":
                    self.host.show_settings(self.inst)
                elif name == "grip" and self.windowHandle():
                    self.windowHandle().startSystemMove()
                return
        if self.windowHandle():
            self.windowHandle().startSystemMove()

    def moveEvent(self, e):
        self.inst.x, self.inst.y = self.x(), self.y()
        self.host.save_later()

    def start_move(self):
        if self.windowHandle():
            self.windowHandle().startSystemMove()

    # --- right-click menu (Windows 7's gadget menu)
    def menu(self, pos):
        m = QMenu(self)
        m.addAction("Add gadgets…", self.host.show_gallery)
        m.addSeparator()
        top = m.addAction("Always on top")
        top.setCheckable(True)
        top.setChecked(self.inst.ontop)
        top.toggled.connect(lambda on: self.host.set_ontop(self.inst, on))
        if self.info.get("undockable"):
            m.addAction("Smaller size" if not self.inst.docked else "Larger size",
                        lambda: self.host.toggle_docked(self.inst))
        op = m.addMenu("Opacity")
        for v in (20, 40, 60, 80, 100):
            a = op.addAction(f"{v}%")
            a.setCheckable(True)
            a.setChecked(self.inst.opacity == v)
            a.triggered.connect(lambda _c=False, v=v: self.host.set_opacity(self.inst, v))
        if self.info.get("settingsUI"):
            m.addAction("Options", lambda: self.host.show_settings(self.inst))
        m.addSeparator()
        m.addAction("Close gadget", lambda: self.host.remove(self.inst))
        m.exec(self.view.mapToGlobal(pos))


# ------------------------------------------------------- flyout & settings ---
class FlyoutWindow(QWidget):
    def __init__(self, host, inst):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.inst = inst
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.page = Page(host, inst, "flyout", self)
        self.view = make_view(self.page, self)
        self.page.windowCloseRequested.connect(self._closed)
        self.resize(300, 200)
        self._closing = False

    def set_content_size(self, w, h):
        w, h = max(16, min(w, 1600)), max(16, min(h, 1600))
        self.view.setGeometry(0, 0, w, h)
        self.resize(w, h)
        self.place()

    def place(self):
        gw = self.inst.window
        if not gw:
            return
        g = gw.frameGeometry()
        scr = (QGuiApplication.screenAt(g.center()) or QGuiApplication.primaryScreen()).availableGeometry()
        x = g.x() - self.width() - 4
        if x < scr.left():
            x = g.x() + gw.content.width() + 4
        y = min(max(g.y(), scr.top()), scr.bottom() - self.height())
        self.move(x, y)
        if not self.isVisible():
            self.show()

    def hideEvent(self, e):
        # Clicking elsewhere hides a flyout; tell the page so onHide runs and Flyout.show reads false.
        if not self._closing:
            self._closing = True
            self.page.runJavaScript("window.close()")

    def _closed(self):
        self._closing = True
        self.hide()
        if self.inst.window and getattr(self.inst.window, "flyout", None) is self:
            self.inst.window.flyout = None
        self.deleteLater()


class SettingsDialog(QDialog):
    def __init__(self, host, inst):
        super().__init__(None)
        self.host, self.inst = host, inst
        self.setWindowTitle(f"{inst.gadget.name}")
        self.setWindowIcon(QIcon(str(inst.gadget.icon)) if inst.gadget.icon else QIcon.fromTheme("configure"))
        self.page = Page(host, inst, "settings", self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.view.setMinimumSize(200, 120)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 10)
        lay.addWidget(self.view, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.setContentsMargins(10, 0, 10, 0)
        bb.accepted.connect(lambda: self.finish(1))
        bb.rejected.connect(lambda: self.finish(0))
        lay.addWidget(bb)
        self.page.windowCloseRequested.connect(lambda: self.finish(0))
        self._done = False

    def set_content_size(self, w, h):
        self.view.setFixedSize(max(200, min(w, 1200)), max(80, min(h, 900)))
        self.adjustSize()
        if not self.isVisible():
            gw = self.inst.window
            if gw:
                c = gw.frameGeometry().center()
                self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)
            self.show()
            self.raise_()
            self.activateWindow()

    def finish(self, action):
        if self._done:
            return
        def closing(cancel):
            if cancel:
                return                                   # the gadget refused (e.g. invalid input)
            self._done = True
            gw = self.inst.window
            if gw:
                gw.page.runJavaScript(f"__halcyon.fire('onSettingsClosed', {action})")
                QTimer.singleShot(300, gw.refresh_info)
            self.host.save_later()
            self.hide()
            self.page.runJavaScript("1")
            QTimer.singleShot(0, self.deleteLater)
        self.page.runJavaScript(f"window.__halcyon ? __halcyon.settingsClosing({action}) : false", 0, closing)

    def closeEvent(self, e):
        if not self._done:
            e.ignore()
            self.finish(0)


# ---------------------------------------------------------------- gallery ---
class Gallery(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.setWindowTitle("Desktop Gadgets")
        self.setWindowIcon(QIcon.fromTheme("preferences-desktop-plasma", QIcon.fromTheme("view-grid")))
        self.resize(640, 470)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        head = QLabel("<span style='font-size:15pt'>Gadgets</span><br>"
                      "<span style='color:gray'>Double-click a gadget to put it on your desktop</span>")
        top.addWidget(head, 1)
        self.search = QLineEdit(placeholderText="Search gadgets", clearButtonEnabled=True)
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self.fill)
        top.addWidget(self.search, 0, Qt.AlignTop)
        lay.addLayout(top)
        self.list = QListWidget(viewMode=QListWidget.IconMode, iconSize=QSize(64, 64), gridSize=QSize(116, 112),
                                resizeMode=QListWidget.Adjust, movement=QListWidget.Static, wordWrap=True,
                                uniformItemSizes=True, textElideMode=Qt.ElideNone)
        self.list.itemActivated.connect(lambda it: self.add(it))
        self.list.currentItemChanged.connect(lambda *_: self.details())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context)
        lay.addWidget(self.list, 1)
        det = QHBoxLayout()
        self.icon = QLabel()
        self.icon.setFixedSize(48, 48)
        det.addWidget(self.icon, 0, Qt.AlignTop)
        self.text = QLabel(wordWrap=True, textFormat=Qt.RichText, openExternalLinks=True)
        self.text.setMinimumHeight(60)
        det.addWidget(self.text, 1)
        lay.addLayout(det)
        btns = QHBoxLayout()
        b_install = QPushButton(QIcon.fromTheme("document-open"), "Install from file…")
        b_install.clicked.connect(self.install_file)
        btns.addWidget(b_install)
        self.b_remove = QPushButton(QIcon.fromTheme("edit-delete"), "Uninstall")
        self.b_remove.clicked.connect(self.uninstall)
        btns.addWidget(self.b_remove)
        btns.addStretch(1)
        self.b_add = QPushButton(QIcon.fromTheme("list-add"), "Add to desktop")
        self.b_add.setDefault(True)
        self.b_add.clicked.connect(lambda: self.add(self.list.currentItem()))
        btns.addWidget(self.b_add)
        lay.addLayout(btns)
        self.fill()

    def fill(self, *_a, select=None):
        q = self.search.text().strip().casefold()
        self.list.clear()
        for g in package.all_gadgets():
            if q and q not in g.name.casefold() and q not in g.description.casefold():
                continue
            icon = QIcon(str(g.icon)) if g.icon else QIcon.fromTheme("view-grid")
            it = QListWidgetItem(icon, g.name)
            it.setData(Qt.UserRole, g)
            it.setToolTip(g.description)
            self.list.addItem(it)
            if select and str(g.root) == str(select):
                self.list.setCurrentItem(it)
        if not self.list.currentItem() and self.list.count():
            self.list.setCurrentRow(0)
        self.details()

    def details(self):
        it = self.list.currentItem()
        g = it.data(Qt.UserRole) if it else None
        self.b_add.setEnabled(bool(g))
        self.b_remove.setEnabled(bool(g and g.user))
        if not g:
            self.text.setText("")
            self.icon.clear()
            return
        self.icon.setPixmap((QIcon(str(g.icon)) if g.icon else QIcon.fromTheme("view-grid")).pixmap(48, 48))
        esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")
        meta = " · ".join(x for x in (f"Version {esc(g.version)}" if g.version else "",
                                      esc(g.author), esc(g.copyright)) if x)
        link = f" · <a href='{esc(g.website)}'>Website</a>" if g.website.startswith(("http://", "https://")) else ""
        self.text.setText(f"<b>{esc(g.name)}</b><br><span style='color:gray'>{meta}{link}</span><br>{esc(g.description)}")

    def add(self, it):
        g = it.data(Qt.UserRole) if it else None
        if g:
            self.host.add(g)

    def context(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        g = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("Add", lambda: self.host.add(g))
        if g.user:
            m.addAction("Uninstall", self.uninstall)
        m.exec(self.list.viewport().mapToGlobal(pos))

    def install_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Install gadgets", str(Path.home()),
                                              "Gadgets and gadget packs (*.gadget *.Gadget *.msi *.7z *.zip);;"
                                              "All files (*)")
        if path:
            self.host.install(path)

    def uninstall(self):
        it = self.list.currentItem()
        g = it.data(Qt.UserRole) if it else None
        if not g or not g.user:
            return
        if QMessageBox.question(self, "Uninstall gadget", f"Uninstall {g.name}?") != QMessageBox.Yes:
            return
        for inst in [i for i in self.host.instances.values() if i.gadget.root == g.root]:
            self.host.remove(inst)
        package.uninstall(g)
        self.fill()


# ------------------------------------------------------------------- host ---
class Host(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.bridge = Bridge()
        self.instances = {}
        self.gallery = None
        self.server = Server(self)
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self.profile = QWebEngineProfile("halcyon-gadgets", app)
        self.profile.setPersistentStoragePath(str(PROFILE_DIR))
        self.profile.setHttpUserAgent("Mozilla/5.0 (Windows NT 6.1; Trident/7.0; Halcyon Gadgets) like Gecko")
        s = self.profile.settings()
        s.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, True)
        s.setAttribute(QWebEngineSettings.ShowScrollBars, False)
        s.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        s.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
        shim = (Path(__file__).with_name("shim.js").read_text()
                .replace("%ORIGIN%", self.server.origin).replace("%TOKEN%", TOKEN))
        script = QWebEngineScript()
        script.setName("halcyon-gadget-runtime")
        script.setSourceCode(shim)
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        self.profile.scripts().insert(script)
        self.gimage = GImage(self)
        self.profile.installUrlSchemeHandler(QByteArray(b"gimage"), self.gimage)

        self.bridge.resize.connect(self._resize)
        self.bridge.close.connect(lambda i: self.remove(self.instances.get(i)))
        self.bridge.drag.connect(lambda i: self.instances.get(i) and self.instances[i].window and
                                 self.instances[i].window.start_move())
        self.bridge.save.connect(self.save_later)
        self.bridge.choose.connect(self._choose)
        self._save_timer = QTimer(self, singleShot=True, interval=800, timeout=self.save)

    # --- state
    def instance(self, i):
        return self.instances.get(i)

    def load(self):
        try:
            cfg = json.loads(CONFIG.read_text())
        except (OSError, ValueError):
            cfg = {}
        if not cfg.get("initialized"):
            byname = {g.name: g for g in package.all_gadgets()}
            for n in FIRST_RUN:
                if n in byname:
                    self.add(byname[n], save=False)
            self.save()
            return
        for d in cfg.get("instances", []):
            g = package.Gadget(d.get("gadget", ""))
            if g.ok:
                self.start(Instance(g, d))

    def save_later(self):
        self._save_timer.start()

    def save(self):
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps({"initialized": True,
                                   "instances": [i.to_json() for i in self.instances.values()]}, indent=1))
        tmp.replace(CONFIG)

    # --- gadgets
    def start(self, inst):
        self.instances[inst.id] = inst
        w = inst.window = GadgetWindow(self, inst)
        w.flyout = None
        if inst.x is None:
            # New gadgets stack down the right edge, like the old Sidebar.
            scr = QGuiApplication.primaryScreen().availableGeometry()
            y = scr.top() + 24
            for other in self.instances.values():
                if other is not inst and other.window and other.x is not None and other.x > scr.right() - 400:
                    y = max(y, other.window.y() + other.window.height() + 12)
            inst.x, inst.y = scr.right() - 130 - TOOLBAR - 24, y
        w.move(inst.x, inst.y)
        w.show()
        QTimer.singleShot(50, lambda: x11.skip_taskbar(w.winId()))

    def add(self, g, save=True):
        self.start(Instance(g))
        if save:
            self.save_later()

    def remove(self, inst):
        if not inst or inst.id not in self.instances:
            return
        del self.instances[inst.id]
        if inst.window:
            if inst.window.flyout:
                inst.window.flyout.close()
            inst.window.hide()
            inst.window.page.setParent(None)
            inst.window.deleteLater()
            inst.window = None
        self.save_later()

    def install(self, path):
        if Path(path).suffix.lower() in package.PACK_SUFFIXES:
            return self.import_pack(path)
        try:
            g = package.install(path)
        except Exception as e:
            QMessageBox.warning(None, "Install gadget", f"Couldn't install {Path(path).name}.\n\n{e}")
            return None
        self.show_gallery(select=g.root)
        return g

    def import_pack(self, path):
        """Every gadget in a gadget pack (8GadgetPack's .msi, or an archive)."""
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog(f"Reading {Path(path).name}…", None, 0, 0)
        dlg.setWindowTitle("Import gadgets")
        dlg.setMinimumDuration(0)
        dlg.show()
        QApplication.processEvents()

        def progress(i, n, name):
            dlg.setMaximum(n)
            dlg.setValue(i)
            dlg.setLabelText(f"Installing {name}…")
            QApplication.processEvents()
        try:
            installed, skipped = package.import_pack(path, progress)
        except Exception as e:
            dlg.close()
            QMessageBox.warning(None, "Import gadgets", f"Couldn't import {Path(path).name}.\n\n{e}")
            return None
        dlg.close()
        msg = f"Installed {len(installed)} gadget{'s' if len(installed) != 1 else ''} from {Path(path).name}."
        if skipped:
            msg += "\n\nSkipped (can't run outside Windows):\n" + "\n".join(f"  • {n}" for n, _r in skipped)
        self.show_gallery()
        QMessageBox.information(self.gallery, "Import gadgets", msg)
        return installed

    def toggle_docked(self, inst):
        inst.docked = not inst.docked
        inst.window.page.runJavaScript(f"__halcyon.fire('{'onDock' if inst.docked else 'onUndock'}', 0)")
        self.save_later()

    def set_opacity(self, inst, v):
        inst.opacity = v
        inst.window.apply_opacity()
        self.save_later()

    def set_ontop(self, inst, on):
        inst.ontop = on
        w = inst.window
        pos = w.pos()
        w.setWindowFlags(GadgetWindow._flags(inst))
        w.move(pos)
        w.show()
        QTimer.singleShot(50, lambda: x11.skip_taskbar(w.winId()))
        self.save_later()

    def show_settings(self, inst):
        if inst.window:
            inst.window.page.runJavaScript("__halcyon.openSettings()")

    def open_settings_window(self, inst):
        d = SettingsDialog(self, inst)
        inst.settings_dialog = d
        return d.page

    def open_flyout(self, inst):
        f = FlyoutWindow(self, inst)
        if inst.window:
            if inst.window.flyout:
                inst.window.flyout._closing = True
                inst.window.flyout.deleteLater()
            inst.window.flyout = f
        return f.page

    def _resize(self, i, kind, w, h):
        inst = self.instances.get(i)
        if not inst or not inst.window:
            return
        if kind == "gadget":
            inst.window.set_content_size(w, h)
        elif kind == "flyout" and inst.window.flyout:
            inst.window.flyout.set_content_size(w, h)
        elif kind == "settings" and getattr(inst, "settings_dialog", None):
            try:
                inst.settings_dialog.set_content_size(w, h)
            except RuntimeError:          # dialog already closed
                inst.settings_dialog = None

    # --- file choosers for System.Shell (asked from the server thread)
    def ask(self, inst, kind, a="", b=""):
        req = {"inst": inst, "kind": kind, "a": a, "b": b, "done": threading.Event(), "result": None}
        self.bridge.choose.emit(req)
        req["done"].wait(900)
        return req["result"]

    def _choose(self, req):
        kind = req["kind"]
        start = req["b"] if req["b"] and req["b"].startswith("/") else str(Path.home())
        if kind == "folder":
            r = QFileDialog.getExistingDirectory(None, req["a"] or "Choose a folder", start)
        else:
            # Windows filters look like "Images:*.jpg;*.png::" or "Images|*.jpg;*.png"
            pats = " ".join(p for p in req["a"].replace("|", ":").replace(";", " ").split(":") if "*" in p)
            filt = f"Files ({pats});;All files (*)" if pats else "All files (*)"
            fn = QFileDialog.getSaveFileName if kind == "save" else QFileDialog.getOpenFileName
            r = fn(None, "Choose a file", start, filt)[0]
        req["result"] = r or None
        req["done"].set()

    def show_gallery(self, select=None):
        if self.gallery is None:
            self.gallery = Gallery(self)
        self.gallery.fill(select=select)
        self.gallery.show()
        self.gallery.raise_()
        self.gallery.activateWindow()

    def command(self, line):
        cmd, _, arg = line.partition(" ")
        if cmd == "gallery":
            self.show_gallery()
        elif cmd == "install" and arg:
            self.install(arg)
        elif cmd == "quit":
            self.save()
            self.app.quit()


def send(line):
    s = QLocalSocket()
    s.connectToServer(NAME)
    if not s.waitForConnected(500):
        return False
    s.write(line.encode() + b"\n")
    s.flush()
    s.waitForBytesWritten(500)
    s.disconnectFromServer()
    return True


def main():
    args = sys.argv[1:]
    daemon = "--daemon" in args
    files = [a for a in args if not a.startswith("-")]
    if files and files[0] == "quit":
        send("quit")
        return 0
    lines = [f"install {os.path.abspath(f)}" for f in files] or ([] if daemon else ["gallery"])
    if send(lines[0] if lines else "ping"):
        for l in lines[1:]:
            send(l)
        return 0
    scheme = QWebEngineUrlScheme(QByteArray(b"gimage"))
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.LocalAccessAllowed |
                    QWebEngineUrlScheme.Flag.CorsEnabled)
    QWebEngineUrlScheme.registerScheme(scheme)
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("halcyon-gadgets")
    app.setDesktopFileName("halcyon-gadgets")
    app.setQuitOnLastWindowClosed(False)
    host = Host(app)
    server = QLocalServer()
    QLocalServer.removeServer(NAME)
    server.listen(NAME)

    def incoming():
        c = server.nextPendingConnection()
        def read():
            while c.canReadLine():
                host.command(bytes(c.readLine()).decode().strip())
        c.readyRead.connect(read)
        c.disconnected.connect(c.deleteLater)
    server.newConnection.connect(incoming)
    host.load()
    for l in lines:
        host.command(l)
    app.aboutToQuit.connect(host.save)
    shots = os.environ.get("HALCYON_GADGETS_SHOT")          # development: save window images, then quit
    if shots:
        def shoot():
            for inst in host.instances.values():
                if inst.window:
                    inst.window.grab().save(os.path.join(shots, f"{inst.gadget.name}.png"))
                    d = getattr(inst, "settings_dialog", None)
                    if d is not None:
                        try:
                            d.grab().save(os.path.join(shots, f"{inst.gadget.name}-settings.png"))
                        except RuntimeError:
                            pass
                    f = inst.window.flyout
                    if f:
                        f.grab().save(os.path.join(shots, f"{inst.gadget.name}-flyout.png"))
            if host.gallery:
                host.gallery.grab().save(os.path.join(shots, "gallery.png"))
            app.quit()
        QTimer.singleShot(int(os.environ.get("HALCYON_GADGETS_SHOT_DELAY", "4000")), shoot)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
