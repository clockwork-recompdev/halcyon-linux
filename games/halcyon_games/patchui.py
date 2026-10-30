"""'Find patches' window for a Windows game (see patches.py for how they're found)."""
import html
import os
import time

from PySide6.QtCore import QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
                               QPushButton, QVBoxLayout)

from . import patches


class Worker(QThread):
    """Runs a blocking function off the UI thread; reports progress and the result."""
    progress = Signal(int)
    done = Signal(object, str)

    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.done.emit(self.fn(*self.args, **({"progress": self.progress.emit} if self.fn is patches.download
                                                  else {})), "")
        except Exception as e:          # offline, wiki down, file gone, …
            self.done.emit(None, str(e))


def describe(p):
    bits = [b for b in (p.get("column"), p.get("row")) if b and b.lower() not in ("windows", "mac")]
    name = (os.path.basename(QUrl(p["url"]).path()) if p.get("direct", True) else "") or p["text"]
    info = " · ".join(bits) or p.get("text", "")
    return name, "" if info == name else info


class PatchDialog(QDialog):
    def __init__(self, launcher, game, auto=False):
        super().__init__(launcher)
        self.l, self.g, self.auto = launcher, game, auto
        self.result = None
        self.proc = None
        self.setWindowTitle(f"Patches for {game.name}")
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        head = QLabel(f"<b style='font-size:12pt'>{html.escape(game.name)}</b><br>"
                      "Looking for the best patch on PCGamingWiki…", objectName="patchhead")
        head.setWordWrap(True)
        self.head = head
        lay.addWidget(head)
        self.bar = QProgressBar(textVisible=False)
        self.bar.setRange(0, 0)
        self.bar.setFixedHeight(12)
        lay.addWidget(self.bar)

        self.best = QFrame(objectName="panel")
        bl = QVBoxLayout(self.best)
        self.best_title = QLabel(wordWrap=True)
        self.best_info = QLabel(wordWrap=True, objectName="gameinfo")
        bl.addWidget(self.best_title)
        bl.addWidget(self.best_info)
        row = QHBoxLayout()
        self.install = QPushButton("Download and install", objectName="play")
        self.install.clicked.connect(lambda: self.get(self.result["best"][0]))
        row.addWidget(self.install)
        row.addStretch(1)
        bl.addLayout(row)
        self.best.hide()
        lay.addWidget(self.best)

        self.others_label = QLabel("Other downloads listed on PCGamingWiki:")
        self.others = QListWidget()
        self.others.setStyleSheet("QListWidget { background: #3E4637; }")
        self.others.itemActivated.connect(lambda it: self.get(it.data(Qt.UserRole)))
        self.others_label.hide()
        self.others.hide()
        lay.addWidget(self.others_label)
        lay.addWidget(self.others, 1)
        self.notes = QLabel(wordWrap=True, objectName="gameinfo", openExternalLinks=True)
        self.notes.setTextFormat(Qt.RichText)
        lay.addWidget(self.notes)
        foot = QHBoxLayout()
        self.wiki = QPushButton("Open PCGamingWiki page")
        self.wiki.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.result["url"])))
        self.wiki.hide()
        foot.addWidget(self.wiki)
        foot.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        foot.addWidget(close)
        lay.addLayout(foot)

        folder = game.folder or game.cwd
        others = [x.name for x in launcher.games if x.source == "Windows" and x is not game]
        self.worker = Worker(patches.lookup, game.name, folder, others, parent=self)
        self.worker.done.connect(self.found)
        self.worker.start()

    # --- lookup
    def found(self, r, err):
        self.bar.hide()
        self.result = r
        name = html.escape(self.g.name)
        if err or not r or not r.get("page"):
            self.head.setText(f"<b style='font-size:12pt'>{name}</b><br>"
                              + (f"Couldn't reach PCGamingWiki: {html.escape(err)}" if err else
                                 "PCGamingWiki doesn't have a page for this game."))
            if self.auto:
                QTimer.singleShot(0, self.close)
            return
        self.wiki.show()
        if not r["best"]:
            self.head.setText(f"<b style='font-size:12pt'>{name}</b><br>No patches are listed for "
                              f"{html.escape(r['page'])}. It's probably fine as it is.")
            if self.auto:
                QTimer.singleShot(0, self.close)
            return
        b = r["best"][0]
        fname, info = describe(b)
        done = self.l.cfg.get("patched", {}).get(self.g.id)
        self.head.setText(f"<b style='font-size:12pt'>{name}</b><br>Recommended for this computer "
                          f"(from <i>{html.escape(r['page'])}</i> on PCGamingWiki):")
        badge = " <span style='color:#C4B550'>· official</span>" if b.get("official") else ""
        self.best_title.setText(f"<b>{html.escape(fname)}</b>{badge}")
        self.best_info.setText(html.escape(info) + (f"<br>Already installed {done}." if done else ""))
        self.install.setText("Download and install" if b["direct"] else "Open download page")
        self.best.show()
        alts = r["alternatives"] + [dict(l, direct=False) for l in r["links"]
                                    if l["text"].strip().lower() not in ("mac", "os x", "macos")]
        for a in alts:
            if not a.get("direct") and a["text"].strip().lower() == "windows":
                a["text"] = "Download page (Windows)"
        for a in alts:
            fn, inf = describe(a)
            it = QListWidgetItem(f"{fn}   —   {inf}" if inf else fn)
            it.setData(Qt.UserRole, a)
            it.setToolTip(a["url"])
            self.others.addItem(it)
        self.others_label.setVisible(bool(alts))
        self.others.setVisible(bool(alts))
        if r["notes"]:
            self.notes.setText("<br>".join("• " + html.escape(n) for n in r["notes"][:4]))

    # --- download + install
    def get(self, p):
        if not p.get("direct"):
            QDesktopServices.openUrl(QUrl(p["url"]))
            return
        self.install.setEnabled(False)
        self.others.setEnabled(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.show()
        self.best_info.setText("Downloading…")
        self.dl = Worker(patches.download, p["url"], parent=self)
        self.dl.progress.connect(self.bar.setValue)
        self.dl.done.connect(lambda path, err, p=p: self.downloaded(p, path, err))
        self.dl.start()

    def downloaded(self, p, path, err):
        if err or not path:
            self.bar.hide()
            self.best_info.setText(f"Download failed: {html.escape(err)}")
            self.install.setEnabled(True)
            self.others.setEnabled(True)
            return
        self.bar.setRange(0, 0)
        self.best_info.setText("The patch installer is running. Follow its steps; "
                               "it should find the game by itself.")
        self.proc = patches.apply(path, self.g.folder or self.g.cwd, self.g.env)
        self.patch_name = path.name
        self.poll = QTimer(self, interval=1000, timeout=self.check_done)
        self.poll.start()

    def check_done(self):
        if self.proc.poll() is None:
            return
        self.poll.stop()
        # Patch installers often start a second process; let Wine finish everything.
        os.system(f"WINEPREFIX={patches.wine_env(self.g.env)['WINEPREFIX']!r} wineserver -w")
        self.bar.hide()
        stamp = time.strftime("%b %-d, %Y")
        self.l.cfg.setdefault("patched", {})[self.g.id] = f"{self.patch_name} ({stamp})"
        self.l.save()
        self.best_info.setText(f"Done: {html.escape(self.patch_name)} was installed. "
                               "Start the game to check its new version.")
        self.install.setText("Install again")
        self.install.setEnabled(True)
        self.others.setEnabled(True)
