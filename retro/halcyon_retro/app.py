"""Halcyon Navigator: the old web, the way it looked.

Plain http:// goes through Protoweb (a proxy of restored 1990s/2000s sites),
https:// stays direct. Flash runs through Ruffle and Java applets through
CheerpJ, both injected into every page, so old embeds just work.

Settings live in ~/.config/halcyon/retro-browser.json (proxy address, home)."""
import json
import math
import os
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "halcyon" / "retro-browser.json"
DEFAULTS = {
    # Protoweb's current server list: https://protoweb.org/wiki/servers
    "proxy": "wayback.protoweb.org:7851",
    "home": "http://www.inode.com/",      # the iNode Gateway, Protoweb's start page
    "protoweb": True,
    "favorites": [["iNode Gateway", "http://www.inode.com/"], ["Yahoo!", "http://www.yahoo.com/"]],
    "zoom": 1.0,
}
RUFFLE_DIRS = ("/usr/share/halcyon-retro/ruffle", str(Path(__file__).resolve().parent.parent / "ruffle"))
CHEERPJ = "https://cjrtnc.leaningtech.com/3.0/cj3loader.js"


def load_config():
    try:
        return dict(DEFAULTS, **json.loads(CONFIG.read_text()))
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1))


def serve_ruffle():
    """Ruffle (Flash emulator, MIT/Apache) ships with Halcyon; serve it on 127.0.0.1 so
    pages can load it (loopback skips the Protoweb proxy). Returns its URL or None."""
    import functools
    import http.server
    import threading
    root = next((d for d in RUFFLE_DIRS if os.path.exists(os.path.join(d, "ruffle.js"))), None)
    if not root:
        return None

    class Handler(http.server.SimpleHTTPRequestHandler):
        extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map,
                              **{".wasm": "application/wasm", ".js": "text/javascript"})

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=root))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/ruffle.js"


# Chromium's proxy has to be set before Qt WebEngine starts.
CFG = load_config()
if CFG.get("protoweb"):
    p = CFG["proxy"]
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") +
                                                f" --proxy-server=http={p};ftp={p}"
                                                " --proxy-bypass-list=127.0.0.1;localhost").strip()

from PySide6.QtCore import QPointF, QRectF, QSize, QUrl, Qt  # noqa: E402
from PySide6.QtGui import (QAction, QBrush, QColor, QFontDatabase, QIcon, QKeySequence, QLinearGradient, QPalette,  # noqa: E402
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,  # noqa: E402
                               QMainWindow, QMenu, QMessageBox, QProgressBar, QStackedWidget, QStatusBar, QTabBar,
                               QToolButton, QVBoxLayout, QWidget)

APP = "Halcyon Navigator"

# Flash (Ruffle) and Java applets (CheerpJ), added to every page that needs them.
PLUGINS_JS = r"""
(function () {
  if (window.__halcyonRetro) return;
  window.__halcyonRetro = true;
  function add(src, onload) {
    var s = document.createElement("script");
    s.src = src; s.async = true;
    s.onload = function () { console.log("halcyon-retro: loaded " + src); if (onload) onload(); };
    s.onerror = function () { console.log("halcyon-retro: couldn't load " + src); };
    (document.head || document.documentElement).appendChild(s);
  }
  var html = document.documentElement ? document.documentElement.innerHTML.toLowerCase() : "";
  // Flash: Ruffle replaces <embed>/<object> SWFs by itself once loaded.
  if ("%RUFFLE%" && (html.indexOf(".swf") !== -1 || html.indexOf("shockwave-flash") !== -1 || html.indexOf("x-shockwave") !== -1)) {
    window.RufflePlayer = window.RufflePlayer || {};
    window.RufflePlayer.config = {autoplay: "on", unmuteOverlay: "hidden", letterbox: "on",
                                  warnOnUnsupportedContent: false, allowScriptAccess: true};
    add("%RUFFLE%");
  }
  // Java applets: CheerpJ runs <applet> and Java <object> tags.
  if (html.indexOf("<applet") !== -1 || html.indexOf("java-applet") !== -1 || html.indexOf("x-java") !== -1) {
    add("%CHEERPJ%", function () {
      if (window.cheerpjInit) window.cheerpjInit({ enableDebug: false });
    });
  }
})();
"""


def plugins_js(ruffle_url):
    return PLUGINS_JS.replace("%RUFFLE%", ruffle_url or "").replace("%CHEERPJ%", CHEERPJ)



# --- Looks: Internet Explorer 7 on Vista. Everything is drawn here, no borrowed art.

def _pix(size, paint):
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    paint(p, size)
    p.end()
    return pm


def _arrow(p, cx, cy, r, left, color):
    """The fat white arrow on the Back/Forward orbs."""
    d = -1 if left else 1
    path = QPainterPath()
    path.moveTo(cx + d * r * 0.62, cy)
    path.lineTo(cx + d * r * 0.02, cy - r * 0.55)
    path.lineTo(cx + d * r * 0.02, cy - r * 0.22)
    path.lineTo(cx - d * r * 0.58, cy - r * 0.22)
    path.lineTo(cx - d * r * 0.58, cy + r * 0.22)
    path.lineTo(cx + d * r * 0.02, cy + r * 0.22)
    path.lineTo(cx + d * r * 0.02, cy + r * 0.55)
    path.closeSubpath()
    p.setPen(QPen(QColor(0, 40, 90, 110), 1.0))
    p.setBrush(color)
    p.drawPath(path)


def draw_icon(kind, size=16):
    def paint(p, s):
        c = s / 2
        if kind == "refresh":                     # two green chasing arrows
            pen = QPen(QColor("#2f9a2a"), s * 0.14, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            r = QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64)
            p.drawArc(r, 30 * 16, 130 * 16)
            p.drawArc(r, 210 * 16, 130 * 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2f9a2a"))
            for a in (QPolygonF([QPointF(s * 0.86, s * 0.08), QPointF(s * 0.9, s * 0.46), QPointF(s * 0.55, s * 0.32)]),
                      QPolygonF([QPointF(s * 0.14, s * 0.92), QPointF(s * 0.1, s * 0.54), QPointF(s * 0.45, s * 0.68)])):
                p.drawPolygon(a)
        elif kind == "stop":                      # red X
            g = QLinearGradient(0, 0, 0, s)
            g.setColorAt(0, QColor("#f2775e"))
            g.setColorAt(1, QColor("#c0261a"))
            p.setPen(QPen(QBrush(g), s * 0.2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(s * 0.25, s * 0.25), QPointF(s * 0.75, s * 0.75))
            p.drawLine(QPointF(s * 0.75, s * 0.25), QPointF(s * 0.25, s * 0.75))
        elif kind in ("star", "addstar"):          # Favorites: a golden star
            path = QPainterPath()
            for i in range(10):
                r = s * (0.46 if i % 2 == 0 else 0.2)
                a = math.pi / 2 + i * math.pi / 5
                pt = QPointF(c + r * math.cos(a) * (1 if i else 1), c + 0.04 * s - r * math.sin(a))
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            path.closeSubpath()
            g = QLinearGradient(0, 0, 0, s)
            g.setColorAt(0, QColor("#fff3a0"))
            g.setColorAt(0.5, QColor("#f8c933"))
            g.setColorAt(1, QColor("#d48a0c"))
            p.setPen(QPen(QColor("#a8690a"), max(1.0, s / 18)))
            p.setBrush(g)
            p.drawPath(path)
            if kind == "addstar":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#2f9a2a"))
                p.drawRect(QRectF(s * 0.62, s * 0.72, s * 0.36, s * 0.12))
                p.drawRect(QRectF(s * 0.74, s * 0.6, s * 0.12, s * 0.36))
        elif kind == "home":                       # little house
            p.setPen(QPen(QColor("#7a4a1c"), max(1.0, s / 16)))
            g = QLinearGradient(0, 0, 0, s)
            g.setColorAt(0, QColor("#f7f7f2"))
            g.setColorAt(1, QColor("#d8d2bf"))
            p.setBrush(g)
            p.drawRect(QRectF(s * 0.22, s * 0.48, s * 0.56, s * 0.42))
            roof = QPolygonF([QPointF(s * 0.06, s * 0.54), QPointF(c, s * 0.12), QPointF(s * 0.94, s * 0.54)])
            rg = QLinearGradient(0, 0, 0, s * 0.6)
            rg.setColorAt(0, QColor("#f0794a"))
            rg.setColorAt(1, QColor("#b8401c"))
            p.setBrush(rg)
            p.drawPolygon(roof)
            p.setBrush(QColor("#4c7fc2"))
            p.drawRect(QRectF(s * 0.42, s * 0.64, s * 0.16, s * 0.26))
        elif kind == "search":                     # magnifying glass
            p.setPen(QPen(QColor("#2a4f80"), s * 0.12))
            p.setBrush(QColor(200, 225, 255, 160))
            p.drawEllipse(QRectF(s * 0.12, s * 0.12, s * 0.5, s * 0.5))
            p.setPen(QPen(QColor("#2a4f80"), s * 0.18, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(s * 0.58, s * 0.58), QPointF(s * 0.86, s * 0.86))
        elif kind in ("globe", "page"):            # the Internet zone globe / a web page
            if kind == "page":
                p.setPen(QPen(QColor("#7c8ea8"), 1))
                p.setBrush(QColor("white"))
                p.drawRect(QRectF(s * 0.16, s * 0.06, s * 0.6, s * 0.84))
            g = QRadialGradient(QPointF(c * 0.8, c * 0.7), s * 0.5)
            g.setColorAt(0, QColor("#bfe6ff"))
            g.setColorAt(0.6, QColor("#3d8ee0"))
            g.setColorAt(1, QColor("#1b5aa8"))
            r = QRectF(s * 0.1, s * 0.1, s * 0.8, s * 0.8) if kind == "globe" else QRectF(s * 0.38, s * 0.38, s * 0.56, s * 0.56)
            p.setPen(QPen(QColor("#174a8c"), max(1.0, s / 18)))
            p.setBrush(g)
            p.drawEllipse(r)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#4fae3c"))
            p.drawEllipse(QRectF(r.x() + r.width() * 0.18, r.y() + r.height() * 0.22, r.width() * 0.32, r.height() * 0.3))
            p.drawEllipse(QRectF(r.x() + r.width() * 0.5, r.y() + r.height() * 0.5, r.width() * 0.32, r.height() * 0.32))
        elif kind == "newtab":
            p.setPen(QPen(QColor("#8aa3c4"), 1))
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawRoundedRect(QRectF(s * 0.1, s * 0.2, s * 0.72, s * 0.62), 2, 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#d9a400"))
            p.drawEllipse(QRectF(s * 0.6, s * 0.08, s * 0.34, s * 0.34))
        elif kind == "page_menu":                  # Page: a sheet with lines
            p.setPen(QPen(QColor("#7c8ea8"), 1))
            p.setBrush(QColor("white"))
            p.drawRect(QRectF(s * 0.2, s * 0.08, s * 0.6, s * 0.84))
            p.setPen(QPen(QColor("#5a8ad0"), 1))
            for i in range(4):
                y = s * (0.3 + i * 0.15)
                p.drawLine(QPointF(s * 0.32, y), QPointF(s * 0.68, y))
        elif kind == "tools":                      # Tools: a gear
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#6b7f99"))
            for i in range(8):
                a = i * math.pi / 4
                p.drawEllipse(QPointF(c + math.cos(a) * s * 0.34, c + math.sin(a) * s * 0.34), s * 0.1, s * 0.1)
            p.drawEllipse(QPointF(c, c), s * 0.32, s * 0.32)
            p.setBrush(QColor("#eef3fa"))
            p.drawEllipse(QPointF(c, c), s * 0.13, s * 0.13)
        elif kind == "close":
            p.setPen(QPen(QColor("#4b5d75"), s * 0.13, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(s * 0.3, s * 0.3), QPointF(s * 0.7, s * 0.7))
            p.drawLine(QPointF(s * 0.7, s * 0.3), QPointF(s * 0.3, s * 0.7))
        elif kind == "zoom":
            p.setPen(QPen(QColor("#2a4f80"), s * 0.1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(s * 0.12, s * 0.12, s * 0.52, s * 0.52))
            p.drawLine(QPointF(s * 0.6, s * 0.6), QPointF(s * 0.86, s * 0.86))
            p.drawLine(QPointF(s * 0.26, s * 0.38), QPointF(s * 0.5, s * 0.38))
            p.drawLine(QPointF(s * 0.38, s * 0.26), QPointF(s * 0.38, s * 0.5))
    return QIcon(_pix(size, paint))


class Orb(QToolButton):
    """IE7's round glass Back / Forward buttons."""
    def __init__(self, left, tip, parent=None):
        super().__init__(parent)
        self.left = left
        self.setToolTip(tip)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        on = self.isEnabled()
        hover = on and self.underMouse()
        down = on and self.isDown()
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        if not on:
            g.setColorAt(0, QColor("#e9edf2"))
            g.setColorAt(1, QColor("#b5bfcc"))
        elif down:
            g.setColorAt(0, QColor("#1d4f99"))
            g.setColorAt(1, QColor("#3c86de"))
        else:
            g.setColorAt(0, QColor("#7ab8f5" if hover else "#5fa0e8"))
            g.setColorAt(0.5, QColor("#2f72cc" if hover else "#2563bb"))
            g.setColorAt(1, QColor("#5ab0ff" if hover else "#3a8ae0"))
        p.setPen(QPen(QColor("#1b4a8a" if on else "#8f9bab"), 1.2))
        p.setBrush(g)
        p.drawEllipse(r)
        # glass highlight on the upper half
        hl = QLinearGradient(0, r.top(), 0, r.center().y())
        hl.setColorAt(0, QColor(255, 255, 255, 200 if on else 160))
        hl.setColorAt(1, QColor(255, 255, 255, 30))
        p.setPen(Qt.NoPen)
        p.setBrush(hl)
        p.drawEllipse(QRectF(r.left() + 3, r.top() + 1.5, r.width() - 6, r.height() * 0.5))
        if hover and not down:
            glow = QRadialGradient(r.center().x(), r.bottom() - 2, r.width() * 0.5)
            glow.setColorAt(0, QColor(140, 230, 255, 170))
            glow.setColorAt(1, QColor(140, 230, 255, 0))
            p.setBrush(glow)
            p.drawEllipse(r)
        _arrow(p, r.center().x(), r.center().y(), r.width() / 2, self.left,
               QColor("white") if on else QColor("#f4f6f9"))


class Tab(QWebEngineView):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setPage(Page(win.profile, self))
        self.setZoomFactor(CFG.get("zoom", 1.0))


class Page(QWebEnginePage):
    def createWindow(self, kind):
        # Links opening a new window (target=_blank, window.open) get a tab, as IE7 did.
        view = self.parent()
        return view.win.new_tab(background=kind == QWebEnginePage.WebBrowserBackgroundTab).page()


QSS = """
QMainWindow { background: #dbe6f4; }
#chrome { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
          stop:0 #c4d7ef, stop:0.48 #b4cbe9, stop:0.52 #a9c2e4, stop:1 #bcd1ec);
          border-bottom: 1px solid #8ea8cb; }
#tabrow { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f6f9fd, stop:1 #dbe5f3);
          border-bottom: 1px solid #a3b6d0; }
QLineEdit#address, QLineEdit#search { border: 1px solid #7f9db9; background: white; padding: 2px 4px;
          selection-background-color: #3399ff; font-size: 9pt; }
QLineEdit#search { color: #333; }
QToolButton { border: 1px solid transparent; border-radius: 2px; padding: 2px 4px; color: #1e3f66; }
QToolButton:hover { border-color: #95b4d8; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
          stop:0 #fbfdff, stop:0.5 #e8f2fd, stop:0.51 #d6e8fb, stop:1 #e4effc); }
QToolButton:pressed { background: #c4dcf7; border-color: #6f97c6; }
QToolButton::menu-indicator { image: none; }
QToolButton#cmd { padding: 2px 6px; }
QToolButton#small { padding: 1px; border-radius: 1px; }
QTabBar { background: transparent; }
QTabBar::tab { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f2f6fb, stop:1 #d5e1f1);
          border: 1px solid #9fb3cf; border-bottom: none; border-top-left-radius: 3px; border-top-right-radius: 3px;
          min-width: 120px; max-width: 200px; padding: 3px 8px 3px 6px; margin-right: -1px; margin-top: 3px;
          color: #1e1e1e; font-size: 9pt; }
QTabBar::tab:selected { background: white; margin-top: 0px; border-color: #8aa2c2; }
QTabBar::tab:hover:!selected { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e2edfa); }
QStatusBar { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f3f6fa, stop:1 #dde5ef);
          border-top: 1px solid #a7b6c9; color: #222; font-size: 9pt; }
QStatusBar::item { border: none; }
QLabel#zone, QToolButton#zoom { border-left: 1px solid #b4c2d4; padding: 0 8px; color: #222; font-size: 9pt; }
QProgressBar { border: 1px solid #8fa3bd; border-radius: 1px; background: white; max-height: 11px; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #9ef08f, stop:0.5 #4fc93d,
          stop:1 #2fa221); }
QMenu { background: #f5f7fb; border: 1px solid #97a8c0; padding: 2px; }
QMenu::item { padding: 4px 24px 4px 26px; }
QMenu::item:selected { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e8f2fd, stop:1 #c9e0fa);
          border: 1px solid #a8c8ec; border-radius: 2px; color: black; }
QMenu::separator { height: 1px; background: #d0d8e4; margin: 3px 4px 3px 26px; }
"""


class Window(QMainWindow):
    def __init__(self, url=None):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(draw_icon("globe", 32))
        self.resize(1100, 800)
        # Protoweb and the live web keep separate profiles: a live site's "always use https"
        # (HSTS) memory would otherwise send http:// past Protoweb to the modern site.
        mode = "protoweb" if CFG.get("protoweb") else "live"
        self.profile = QWebEngineProfile(f"halcyon-retro-{mode}", self)
        store = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "retro-browser" / mode
        self.profile.setPersistentStoragePath(str(store))
        self.profile.setCachePath(str(store / "cache"))
        script = QWebEngineScript()
        script.setName("halcyon-retro-plugins")
        script.setSourceCode(plugins_js(serve_ruffle()))
        script.setInjectionPoint(QWebEngineScript.DocumentReady)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        self.profile.scripts().insert(script)

        central = QWidget()
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.setCentralWidget(central)

        # Row 1: Back/Forward orbs, address bar with Refresh/Stop, search box.
        chrome = QWidget(objectName="chrome")
        row = QHBoxLayout(chrome)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(2)
        self.back_btn = Orb(True, "Back (Alt+Left)")
        self.fwd_btn = Orb(False, "Forward (Alt+Right)")
        self.back_btn.clicked.connect(lambda: self.view.back())
        self.fwd_btn.clicked.connect(lambda: self.view.forward())
        self.recent = QToolButton(objectName="small", text="▾", toolTip="Recent Pages",
                                  popupMode=QToolButton.InstantPopup)
        self.recent.setMenu(QMenu(self.recent, aboutToShow=self.fill_recent))
        self.recent.setStyleSheet("font-size: 7pt; color: #2b4a73;")
        row.addWidget(self.back_btn)
        row.addWidget(self.fwd_btn)
        row.addWidget(self.recent)
        row.addSpacing(4)
        self.address = QLineEdit(objectName="address")
        self.address.addAction(draw_icon("page"), QLineEdit.LeadingPosition)
        self.address.returnPressed.connect(self.go_address)
        self.address.setMinimumHeight(24)
        row.addWidget(self.address, 1)
        self.refresh_btn = QToolButton(objectName="small", icon=draw_icon("refresh"), toolTip="Refresh (F5)")
        self.refresh_btn.clicked.connect(lambda: self.view.reload())
        self.stop_btn = QToolButton(objectName="small", icon=draw_icon("stop"), toolTip="Stop (Esc)")
        self.stop_btn.clicked.connect(lambda: self.view.stop())
        row.addWidget(self.refresh_btn)
        row.addWidget(self.stop_btn)
        row.addSpacing(6)
        self.search = QLineEdit(objectName="search", placeholderText="AltaVista Search")
        self.search.setFixedWidth(220)
        self.search.setMinimumHeight(24)
        go = self.search.addAction(draw_icon("search"), QLineEdit.TrailingPosition)
        go.triggered.connect(self.do_search)
        self.search.returnPressed.connect(self.do_search)
        row.addWidget(self.search)
        col.addWidget(chrome)

        # Row 2: Favorites, tabs, then Home / Page / Tools.
        tabrow = QWidget(objectName="tabrow")
        trow = QHBoxLayout(tabrow)
        trow.setContentsMargins(6, 2, 6, 0)
        trow.setSpacing(2)
        self.fav_btn = QToolButton(icon=draw_icon("star", 18), toolTip="Favorites Center (Alt+C)",
                                   popupMode=QToolButton.InstantPopup)
        self.fav_btn.setIconSize(QSize(18, 18))
        self.fav_btn.setMenu(QMenu(self.fav_btn, aboutToShow=self.fill_favorites))
        self.addfav_btn = QToolButton(icon=draw_icon("addstar", 18), toolTip="Add to Favorites (Ctrl+D)")
        self.addfav_btn.setIconSize(QSize(18, 18))
        self.addfav_btn.clicked.connect(self.add_favorite)
        trow.addWidget(self.fav_btn, 0, Qt.AlignBottom)
        trow.addWidget(self.addfav_btn, 0, Qt.AlignBottom)
        trow.addSpacing(6)
        self.tabs = QTabBar(tabsClosable=False, movable=True, expanding=False, documentMode=True,
                            elideMode=Qt.ElideRight, drawBase=False)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabMoved.connect(self.tab_moved)
        trow.addWidget(self.tabs, 0, Qt.AlignBottom)
        self.newtab_btn = QToolButton(objectName="small", icon=draw_icon("newtab"), toolTip="New Tab (Ctrl+T)")
        self.newtab_btn.clicked.connect(lambda: self.new_tab(focus_address=True))
        trow.addWidget(self.newtab_btn, 0, Qt.AlignBottom)
        trow.addStretch(1)
        home = QToolButton(objectName="cmd", icon=draw_icon("home"), toolTip="Home (Alt+Home)")
        home.clicked.connect(self.home)
        trow.addWidget(home)
        page = QToolButton(objectName="cmd", text="Page ▾", icon=draw_icon("page_menu"),
                           toolButtonStyle=Qt.ToolButtonTextBesideIcon, popupMode=QToolButton.InstantPopup)
        pm = QMenu(page)
        pm.addAction("New Tab", lambda: self.new_tab(focus_address=True), QKeySequence("Ctrl+T"))
        pm.addAction("Close Tab", lambda: self.close_tab(self.tabs.currentIndex()), QKeySequence("Ctrl+W"))
        pm.addSeparator()
        pm.addAction("Save As…", self.save_page, QKeySequence.Save)
        pm.addAction("Print…", lambda: self.view.printToPdf(str(Path.home() / "page.pdf")))
        pm.addSeparator()
        pm.addAction("Zoom In", lambda: self.zoom(+0.1), QKeySequence.ZoomIn)
        pm.addAction("Zoom Out", lambda: self.zoom(-0.1), QKeySequence.ZoomOut)
        pm.addAction("Zoom 100%", lambda: self.zoom(None), QKeySequence("Ctrl+0"))
        pm.addSeparator()
        pm.addAction("View Source", self.view_source, QKeySequence("Ctrl+U"))
        page.setMenu(pm)
        trow.addWidget(page)
        tools = QToolButton(objectName="cmd", text="Tools ▾", icon=draw_icon("tools"),
                            toolButtonStyle=Qt.ToolButtonTextBesideIcon, popupMode=QToolButton.InstantPopup)
        tm = QMenu(tools)
        self.proto_act = tm.addAction("Browse the Old Web (Protoweb)")
        self.proto_act.setCheckable(True)
        self.proto_act.setChecked(bool(CFG.get("protoweb")))
        self.proto_act.toggled.connect(self.toggle_protoweb)
        tm.addAction("Protoweb Server…", self.set_proxy)
        tm.addSeparator()
        tm.addAction("Delete Browsing History…", self.clear_history)
        tm.addAction("Set This Page as Home", self.set_home)
        tm.addSeparator()
        tm.addAction("Full Screen", self.toggle_full, QKeySequence("F11"))
        tm.addAction(f"About {APP}", self.about)
        tools.setMenu(tm)
        trow.addWidget(tools)
        col.addWidget(tabrow)

        self.pages = QStackedWidget()
        col.addWidget(self.pages, 1)

        # Status bar: message, progress, security zone, zoom.
        sb = QStatusBar()
        sb.setSizeGripEnabled(True)
        self.setStatusBar(sb)
        self.state = "Done"
        self.status = QLabel(self.state)
        sb.addWidget(self.status, 1)
        self.progress = QProgressBar(maximumWidth=120, textVisible=False)
        self.progress.hide()
        sb.addPermanentWidget(self.progress)
        self.zone = QLabel(objectName="zone")
        sb.addPermanentWidget(self.zone)
        self.zoom_btn = QToolButton(objectName="zoom", icon=draw_icon("zoom"),
                                    toolButtonStyle=Qt.ToolButtonTextBesideIcon, popupMode=QToolButton.InstantPopup)
        zm = QMenu(self.zoom_btn)
        for z in (4.0, 2.0, 1.5, 1.25, 1.0, 0.75, 0.5):
            zm.addAction(f"{int(z * 100)}%", lambda z=z: self.zoom(z, absolute=True))
        self.zoom_btn.setMenu(zm)
        sb.addPermanentWidget(self.zoom_btn)
        self.update_zone()

        for key, slot in (("Alt+Left", lambda: self.view.back()), ("Alt+Right", lambda: self.view.forward()),
                          ("F5", lambda: self.view.reload()), ("Esc", lambda: self.view.stop()),
                          ("Alt+Home", self.home), ("Ctrl+D", self.add_favorite),
                          ("Alt+C", self.fav_btn.showMenu), ("Ctrl+L", self.focus_address),
                          ("Alt+D", self.focus_address), ("Ctrl+E", self.search.setFocus),
                          ("Ctrl+Tab", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % self.tabs.count())),
                          ("Ctrl+Shift+Tab", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % self.tabs.count()))):
            a = QAction(self, shortcut=QKeySequence(key))
            a.triggered.connect(slot)
            self.addAction(a)
        self.new_tab(url or CFG["home"])

    # --- tabs
    @property
    def view(self):
        return self.pages.currentWidget()

    def new_tab(self, url=None, background=False, focus_address=False):
        v = Tab(self)
        self.pages.addWidget(v)
        i = self.tabs.addTab(draw_icon("page"), "Blank Page" if not url else "Connecting…")
        self.tabs.setTabData(i, v)
        v.titleChanged.connect(lambda t, v=v: self.tab_title(v, t))
        v.iconChanged.connect(lambda ic, v=v: self.tab_icon(v, ic))
        v.urlChanged.connect(lambda u, v=v: v is self.view and self.address.setText(u.toString()))
        v.loadStarted.connect(lambda v=v: v is self.view and self.loading(True))
        v.loadProgress.connect(lambda n, v=v: v is self.view and self.progress.setValue(n))
        v.loadFinished.connect(lambda ok, v=v: v is self.view and self.loading(False, ok))
        v.page().linkHovered.connect(lambda l, v=v: v is self.view and self.status.setText(l or self.state))
        v.page().urlChanged.connect(lambda *_: self.update_nav())
        if url:
            v.load(QUrl(url))
        if not background:
            self.tabs.setCurrentIndex(i)
            self.tab_changed(i)          # the first tab was current before it had its page
        if focus_address:
            self.focus_address()
        return v

    def index_of(self, v):
        return next((i for i in range(self.tabs.count()) if self.tabs.tabData(i) is v), -1)

    def tab_title(self, v, t):
        i = self.index_of(v)
        if i >= 0:
            self.tabs.setTabText(i, t or v.url().toString())
            self.tabs.setTabToolTip(i, t)
        if v is self.view:
            self.setWindowTitle(f"{t} - {APP}" if t else APP)

    def tab_icon(self, v, ic):
        i = self.index_of(v)
        if i >= 0:
            self.tabs.setTabIcon(i, ic if not ic.isNull() else draw_icon("page"))

    def tab_changed(self, i):
        v = self.tabs.tabData(i)
        if not v:
            return
        # Like IE7, only the current tab shows its small close button.
        for j in range(self.tabs.count()):
            b = self.tabs.tabButton(j, QTabBar.RightSide)
            if j == i and not b:
                b = QToolButton(objectName="small", icon=draw_icon("close", 12), toolTip="Close Tab (Ctrl+W)",
                                autoRaise=True)
                b.setFixedSize(16, 16)
                b.clicked.connect(lambda _=False, v=v: self.close_tab(self.index_of(v)))
                self.tabs.setTabButton(j, QTabBar.RightSide, b)
            elif j != i and b:
                self.tabs.setTabButton(j, QTabBar.RightSide, None)
                b.deleteLater()
        self.pages.setCurrentWidget(v)
        self.address.setText(v.url().toString())
        t = v.title()
        self.setWindowTitle(f"{t} - {APP}" if t else APP)
        self.update_nav()
        self.zoom_btn.setText(f"{int(round(v.zoomFactor() * 100))}%")

    def tab_moved(self, *_):
        pass                                      # tabData moves with the tab

    def close_tab(self, i):
        if self.tabs.count() == 1:
            self.close()
            return
        v = self.tabs.tabData(i)
        self.tabs.removeTab(i)
        self.pages.removeWidget(v)
        v.deleteLater()

    # --- navigation
    def update_nav(self):
        v = self.view
        if v:
            self.back_btn.setEnabled(v.history().canGoBack())
            self.fwd_btn.setEnabled(v.history().canGoForward())

    def loading(self, on, ok=True):
        self.progress.setVisible(on)
        self.stop_btn.setEnabled(on)
        self.state = "Opening page…" if on else ("Done" if ok else "The page couldn't be opened")
        self.status.setText(self.state)
        self.update_nav()

    def fill_recent(self):
        m = self.recent.menu()
        m.clear()
        h = self.view.history()
        items = h.backItems(8)[::-1] + [h.currentItem()] + h.forwardItems(8)
        for it in items:
            a = m.addAction(it.title() or it.url().toString(), lambda it=it: h.goToItem(it))
            a.setCheckable(True)
            a.setChecked(it.url() == h.currentItem().url())

    def focus_address(self):
        self.address.setFocus()
        self.address.selectAll()

    def go_address(self):
        text = self.address.text().strip()
        if not text:
            return
        if "://" not in text:
            # The old web was http://; Protoweb only serves http.
            if "." in text and " " not in text:
                text = "http://" + text
            else:
                self.search.setText(text)
                return self.do_search()
        self.view.load(QUrl(text))
        self.view.setFocus()

    def do_search(self):
        q = self.search.text().strip()
        if q:
            self.view.load(QUrl("http://www.altavista.com/cgi-bin/query?q=" + QUrl.toPercentEncoding(q).data().decode()))

    def home(self):
        self.view.load(QUrl(CFG["home"]))

    def set_home(self):
        CFG["home"] = self.view.url().toString()
        save_config(CFG)

    # --- favorites
    def fill_favorites(self):
        m = self.fav_btn.menu()
        m.clear()
        m.addAction(draw_icon("addstar"), "Add to Favorites…", self.add_favorite)
        m.addSeparator()
        for i, (name, url) in enumerate(CFG.get("favorites", [])):
            m.addAction(draw_icon("page"), name, lambda url=url: self.view.load(QUrl(url)))
        if CFG.get("favorites"):
            m.addSeparator()
            rm = m.addMenu("Remove")
            for i, (name, url) in enumerate(CFG["favorites"]):
                rm.addAction(name, lambda i=i: self.remove_favorite(i))

    def add_favorite(self):
        v = self.view
        name, ok = QInputDialog.getText(self, "Add a Favorite", "Name:", QLineEdit.Normal,
                                        v.title() or v.url().host())
        if ok and name.strip():
            CFG.setdefault("favorites", []).append([name.strip(), v.url().toString()])
            save_config(CFG)

    def remove_favorite(self, i):
        del CFG["favorites"][i]
        save_config(CFG)

    # --- page and tools
    def zoom(self, z, absolute=False):
        v = self.view
        f = 1.0 if z is None else (z if absolute else max(0.25, min(5.0, v.zoomFactor() + z)))
        v.setZoomFactor(f)
        CFG["zoom"] = f
        save_config(CFG)
        self.zoom_btn.setText(f"{int(round(f * 100))}%")

    def view_source(self):
        self.new_tab("view-source:" + self.view.url().toString())

    def save_page(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Webpage", str(Path.home() / "page.html"),
                                              "Webpage, complete (*.html)")
        if path:
            self.view.page().save(path)

    def clear_history(self):
        if QMessageBox.question(self, "Delete Browsing History",
                                "Delete temporary files, cookies and history?") == QMessageBox.Yes:
            self.profile.clearHttpCache()
            self.profile.cookieStore().deleteAllCookies()
            self.profile.clearAllVisitedLinks()
            for i in range(self.tabs.count()):
                self.tabs.tabData(i).history().clear()
            self.update_nav()

    def toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def update_zone(self):
        on = CFG.get("protoweb")
        self.zone.setPixmap(QPixmap())
        self.zone.setText(("Internet  |  Protoweb: on" if on else "Internet  |  Protoweb: off"))
        self.zone.setToolTip(f"Old web through {CFG['proxy']}" if on else "The live web")
        self.zoom_btn.setText(f"{int(round(CFG.get('zoom', 1.0) * 100))}%")

    def toggle_protoweb(self, on):
        CFG["protoweb"] = on
        save_config(CFG)
        self.update_zone()
        self.zone.setText(self.zone.text() + " (after restart)")
        QMessageBox.information(self, APP, "Restart the browser to switch between Protoweb and the live web.")

    def set_proxy(self):
        text, ok = QInputDialog.getText(self, "Protoweb server",
                                        "Protoweb's proxy address (see protoweb.org/wiki/servers):",
                                        QLineEdit.Normal, CFG["proxy"])
        if ok and text.strip():
            CFG["proxy"] = text.strip()
            save_config(CFG)
            QMessageBox.information(self, APP, "Restart the browser to use the new server.")

    def about(self):
        QMessageBox.about(self, APP, f"<b>{APP}</b><p>The old web, the way it looked: plain http:// goes "
                          "through Protoweb, Flash plays through Ruffle and Java applets through CheerpJ.</p>")


def light_palette():
    pal = QPalette()
    for role, color in ((QPalette.Window, "#f0f0f0"), (QPalette.WindowText, "#000000"),
                        (QPalette.Base, "#ffffff"), (QPalette.AlternateBase, "#f4f7fb"),
                        (QPalette.Text, "#000000"), (QPalette.PlaceholderText, "#6d6d6d"),
                        (QPalette.Button, "#ececec"), (QPalette.ButtonText, "#000000"),
                        (QPalette.ToolTipBase, "#ffffe1"), (QPalette.ToolTipText, "#000000"),
                        (QPalette.Highlight, "#3399ff"), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.Link, "#0000ee"), (QPalette.BrightText, "#ffffff")):
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#8a8a8a"))
    return pal


def main():
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("halcyon-navigator")
    app.setDesktopFileName("halcyon-navigator")
    app.setStyle("Fusion")
    app.setPalette(light_palette())        # IE was always light, whatever the desktop scheme
    if hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)   # pages too (prefers-color-scheme)
    app.setStyleSheet(QSS)
    f = app.font()
    for fam in ("Segoe UI", "Selawik", "Noto Sans"):
        if fam in QFontDatabase.families():
            f.setFamily(fam)
            break
    f.setPointSizeF(9)
    app.setFont(f)
    w = Window(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
