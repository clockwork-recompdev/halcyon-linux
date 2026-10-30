"""Halcyon Retro Browser: the old web, the way it looked.

Plain http:// goes through Protoweb (a proxy of restored 1990s/2000s sites),
https:// stays direct. Flash runs through Ruffle and Java applets through
CheerpJ, both injected into every page, so old embeds just work.

Settings live in ~/.config/halcyon/retro-browser.json (proxy address, home)."""
import json
import os
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "halcyon" / "retro-browser.json"
DEFAULTS = {
    # Protoweb's current server list: https://protoweb.org/wiki/servers
    "proxy": "wayback.protoweb.org:7851",
    "home": "http://www.yahoo.com/",
    "protoweb": True,
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

from PySide6.QtCore import QUrl, Qt  # noqa: E402
from PySide6.QtGui import QAction, QIcon, QKeySequence  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import (QApplication, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,  # noqa: E402
                               QProgressBar, QStatusBar, QToolBar, QToolButton)

APP = "Halcyon Retro Browser"

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

QSS = """
QMainWindow { background: #dfe9f5; }
QToolBar { border: none; padding: 4px; spacing: 4px;
           background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f4f8fd, stop:0.5 #dde8f6, stop:0.51 #cfdff2, stop:1 #e2ecf8); }
QToolButton { border: 1px solid transparent; border-radius: 13px; padding: 3px 6px; font-weight: bold; color: #1b3b63; }
QToolButton:hover { border-color: #7da2ce; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #cfe2f8); }
QToolButton:pressed { background: #b7d1ef; }
QToolButton:checked { border-color: #5f8fc9; background: #c9def6; }
QLineEdit { border: 1px solid #8fb0d6; border-radius: 3px; padding: 3px 6px; background: white; selection-background-color: #3d8ee0; }
QStatusBar { background: #e7eef8; color: #2a4a70; }
QProgressBar { border: 1px solid #9db7d6; border-radius: 2px; background: white; max-height: 12px; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #7fe07a, stop:1 #2fae2a); }
"""


class Window(QMainWindow):
    def __init__(self, url=None):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon.fromTheme("internet-web-browser"))
        self.resize(1100, 800)
        self.profile = QWebEngineProfile("halcyon-retro", self)
        store = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "retro-browser"
        self.profile.setPersistentStoragePath(str(store))
        self.profile.setCachePath(str(store / "cache"))
        script = QWebEngineScript()
        script.setName("halcyon-retro-plugins")
        script.setSourceCode(plugins_js(serve_ruffle()))
        script.setInjectionPoint(QWebEngineScript.DocumentReady)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        self.profile.scripts().insert(script)
        self.view = QWebEngineView()
        self.view.setPage(QWebEnginePage(self.profile, self.view))
        self.setCentralWidget(self.view)

        tb = QToolBar(movable=False)
        self.addToolBar(tb)
        for text, tip, slot, key in (("◀", "Back", self.view.back, QKeySequence.Back),
                                     ("▶", "Forward", self.view.forward, QKeySequence.Forward),
                                     ("⟳", "Refresh", self.view.reload, QKeySequence.Refresh),
                                     ("⌂", "Home", self.home, None)):
            a = QAction(text, self, toolTip=tip)
            a.triggered.connect(slot)
            if key is not None:
                a.setShortcut(key)
            tb.addAction(a)
        self.address = QLineEdit()
        self.address.returnPressed.connect(self.go_address)
        tb.addWidget(self.address)
        self.proto = QToolButton(text="Protoweb", checkable=True, checked=bool(CFG.get("protoweb")),
                                 toolTip="Browse the restored old web through Protoweb (restarts the browser)")
        self.proto.toggled.connect(self.toggle_protoweb)
        tb.addWidget(self.proto)
        settings = QAction("⚙", self, toolTip="Protoweb server")
        settings.triggered.connect(self.set_proxy)
        tb.addAction(settings)

        sb = QStatusBar()
        self.setStatusBar(sb)
        self.mode = QLabel()
        sb.addPermanentWidget(self.mode)
        self.progress = QProgressBar(maximumWidth=140, textVisible=False)
        sb.addPermanentWidget(self.progress)
        self.view.urlChanged.connect(lambda u: self.address.setText(u.toString()))
        self.view.titleChanged.connect(lambda t: self.setWindowTitle(f"{t} - {APP}" if t else APP))
        self.view.loadProgress.connect(self.progress.setValue)
        self.view.loadFinished.connect(lambda ok: self.progress.setValue(100 if ok else 0))
        self.view.page().linkHovered.connect(lambda l: sb.showMessage(l))
        self.mode.setText(f"Protoweb: {CFG['proxy']}" if CFG.get("protoweb") else "Live web")
        self.view.load(QUrl(url or CFG["home"]))

    def go_address(self):
        text = self.address.text().strip()
        if not text:
            return
        if "://" not in text:
            # The old web was http://; Protoweb only serves http.
            text = ("http://" if "." in text and " " not in text else
                    "http://www.altavista.com/cgi-bin/query?q=") + text
        self.view.load(QUrl(text))

    def home(self):
        self.view.load(QUrl(CFG["home"]))

    def toggle_protoweb(self, on):
        CFG["protoweb"] = on
        save_config(CFG)
        QMessageBox.information(self, APP, "Restart the browser to switch between Protoweb and the live web.")

    def set_proxy(self):
        text, ok = QInputDialog.getText(self, "Protoweb server",
                                        "Protoweb's proxy address (see protoweb.org/wiki/servers):",
                                        QLineEdit.Normal, CFG["proxy"])
        if ok and text.strip():
            CFG["proxy"] = text.strip()
            save_config(CFG)
            QMessageBox.information(self, APP, "Restart the browser to use the new server.")


def main():
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("halcyon-retro-browser")
    app.setDesktopFileName("halcyon-retro-browser")
    app.setStyleSheet(QSS)
    w = Window(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
