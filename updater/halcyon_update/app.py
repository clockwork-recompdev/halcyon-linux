"""Halcyon Update: check for and install updates the way Windows 7 did.
System and Halcyon packages are "important" (Arch can't half-upgrade, so they
go together); AUR and Flatpak apps are "optional" and can be picked."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox,
                               QProgressBar, QPushButton, QSplitter, QStackedWidget, QTextBrowser, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

APP = "Halcyon Update"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "halcyon" / "update.json"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "halcyon" / "update.json"
DB = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "halcyon" / "update-db"
PACMAN_LOG = Path("/var/log/pacman.log")
# Updating these only takes full effect after a restart.
RESTART = re.compile(r"^(linux(-\w+)?|linux-firmware.*|systemd|glibc|nvidia.*|mesa|plasma-workspace|kwin|"
                     r"intel-ucode|amd-ucode)$")
TITLE = QColor("#1e3287")
BAND = {"ok": QColor("#5aa832"), "warn": QColor("#e8b80c"), "error": QColor("#c6282d"), "busy": QColor("#4a90d9")}
SHIELD = {"ok": "security-high", "warn": "security-medium", "error": "security-low", "busy": "system-software-update"}


def load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))


def when(ts):
    if not ts:
        return "Never"
    t = time.localtime(ts)
    day = time.strftime("%Y-%m-%d", t)
    if day == time.strftime("%Y-%m-%d"):
        d = "Today"
    elif day == time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)):
        d = "Yesterday"
    else:
        d = time.strftime("%-m/%-d/%Y", t)
    return f"{d} at {time.strftime('%-I:%M %p', t)}"


def human(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def parse_size(text):
    m = re.match(r"([\d.,]+)\s*(B|KiB|MiB|GiB|kB|MB|GB)", text.strip())
    if not m:
        return 0
    k = {"B": 1, "KiB": 1024, "kB": 1000, "MiB": 1024 ** 2, "MB": 1000 ** 2, "GiB": 1024 ** 3, "GB": 1000 ** 3}
    return float(m.group(1).replace(",", "")) * k[m.group(2)]


# ------------------------------------------------------------- checking ---
def check_repo():
    """[(name, old, new, size, description)] from a private copy of the sync databases."""
    if not shutil.which("checkupdates"):
        return []
    DB.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CHECKUPDATES_DB=str(DB) + "/", LANG="C")
    r = subprocess.run(["checkupdates"], capture_output=True, text=True, env=env)
    if r.returncode not in (0, 2):              # 2 = no updates
        raise RuntimeError(r.stderr.strip() or "Couldn't check for updates.")
    ups = []
    for line in r.stdout.splitlines():
        m = re.match(r"(\S+) (\S+) -> (\S+)", line)
        if m:
            ups.append([m.group(1), m.group(2), m.group(3), 0, ""])
    if ups:
        info = subprocess.run(["pacman", "-Si", "--dbpath", str(DB)] + [u[0] for u in ups],
                              capture_output=True, text=True, env=env).stdout
        details, cur = {}, None
        for line in info.splitlines():
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if k == "Name":
                cur = details.setdefault(v, {})
            elif cur is not None and k in ("Download Size", "Description"):
                cur[k] = v
        for u in ups:
            d = details.get(u[0], {})
            u[3] = parse_size(d.get("Download Size", ""))
            u[4] = d.get("Description", "")
    return ups


def check_aur():
    if not shutil.which("halcyon-aur") or not subprocess.run(["pacman", "-Qmq"], capture_output=True).stdout:
        return []
    r = subprocess.run(["halcyon-aur", "--list"], capture_output=True, text=True)
    return [line.split() + [0, "Built from source from the Arch User Repository (AUR)."]
            for line in r.stdout.splitlines() if len(line.split()) == 3]


def check_flatpak():
    if not shutil.which("flatpak"):
        return []
    r = subprocess.run(["flatpak", "remote-ls", "--updates", "--columns=application,name,version,download-size"],
                       capture_output=True, text=True)
    ups = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            ups.append([parts[0], "", parts[2] or "new version", parse_size(parts[3]),
                        f"{parts[1]} (Flatpak app)"])
    return ups


def history(limit=400):
    """Installed updates from pacman's log, newest first: (time, name, versions, result)."""
    out = []
    try:
        lines = PACMAN_LOG.read_text(errors="replace").splitlines()[-20000:]
    except OSError:
        return out
    for line in lines:
        m = re.match(r"\[([^\]]+)\] \[ALPM\] (upgraded|installed) (\S+) \((.+)\)", line)
        if m:
            try:
                ts = time.mktime(time.strptime(m.group(1)[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                continue
            out.append((ts, m.group(3), m.group(4), "Successful" if m.group(2) == "upgraded" else "Installed"))
    for e in load(STATE, {}).get("history", []):
        out.append((e["time"], e["name"], e.get("version", ""), e.get("result", "Successful")))
    out.sort(key=lambda e: -e[0])
    return out[:limit]


class Checker(QThread):
    """Runs the (slow) checks off the UI thread."""
    done = Signal(dict, str)

    def run(self):
        cfg = load(CONFIG, {})
        ups, err = {"repo": [], "aur": [], "flatpak": []}, ""
        try:
            ups["repo"] = check_repo()
            ups["aur"] = check_aur() if cfg.get("aur", True) else []
            ups["flatpak"] = check_flatpak() if cfg.get("flatpak", True) else []
        except Exception as e:      # network down, broken mirror, …
            err = str(e)
        self.done.emit(ups, err)


# ------------------------------------------------------------------- UI ---
class LeftPane(QWidget):
    """The Control Panel task pane: pale blue, with links."""
    picked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(210)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 12, 18)
        lay.setSpacing(8)
        self.links = {}
        for key, text in (("home", "Halcyon Update"), ("check", "Check for updates"),
                          ("settings", "Change settings"), ("history", "View update history")):
            b = QPushButton(text, objectName="link" if key != "home" else "linkhome", flat=True,
                            cursor=Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.picked.emit(k))
            lay.addWidget(b, 0, Qt.AlignLeft)
            self.links[key] = b
        lay.addStretch(1)
        lay.addWidget(QLabel("See also", objectName="seealso"))
        b = QPushButton("Installed updates", objectName="link", flat=True, cursor=Qt.PointingHandCursor)
        b.clicked.connect(lambda: self.picked.emit("history"))
        lay.addWidget(b, 0, Qt.AlignLeft)

    def paintEvent(self, _e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#f1f6fc"))
        g.setColorAt(1, QColor("#d9e6f5"))
        p.fillRect(self.rect(), g)
        p.fillRect(self.width() - 1, 0, 1, self.height(), QColor("#c3d4e8"))


class StatusBox(QFrame):
    """The status panel: colored band, shield, headline, details, action button."""

    def __init__(self):
        super().__init__(objectName="statusbox")
        self.kind = "busy"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 14, 16, 14)
        lay.setSpacing(14)
        self.shield = QLabel()
        self.shield.setFixedSize(48, 48)
        lay.addWidget(self.shield, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.head = QLabel(objectName="statushead", wordWrap=True)
        self.text = QLabel(objectName="statustext", wordWrap=True, textFormat=Qt.RichText)
        self.text.setOpenExternalLinks(False)
        self.bar = QProgressBar(textVisible=False)
        self.bar.setFixedHeight(14)
        self.bar.hide()
        col.addWidget(self.head)
        col.addWidget(self.text)
        col.addWidget(self.bar)
        col.addStretch(1)
        lay.addLayout(col, 1)
        self.button = QPushButton(objectName="action")
        self.button.hide()
        lay.addWidget(self.button, 0, Qt.AlignBottom)

    def set(self, kind, head, text="", button=None, progress=None):
        self.kind = kind
        self.shield.setPixmap(QIcon.fromTheme(SHIELD[kind], QIcon.fromTheme("system-software-update")).pixmap(48, 48))
        self.head.setText(head)
        self.text.setText(text)
        self.text.setVisible(bool(text))
        if progress is None:
            self.bar.hide()
        else:
            self.bar.show()
            if progress < 0:
                self.bar.setRange(0, 0)
            else:
                self.bar.setRange(0, 100)
                self.bar.setValue(progress)
        if button:
            self.button.setText(button)
            self.button.show()
        else:
            self.button.hide()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect().adjusted(0, 0, -1, -1)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#ffffff"))
        g.setColorAt(1, QColor("#f3f5f8"))
        p.fillRect(r, g)
        p.fillRect(0, 0, 8, self.height(), BAND[self.kind])
        p.setPen(QPen(QColor("#b9c3cf")))
        p.drawRect(r)


class Home(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 20, 26, 20)
        lay.setSpacing(14)
        lay.addWidget(QLabel(APP, objectName="title"))
        self.box = StatusBox()
        self.box.button.clicked.connect(win.primary_action)
        self.box.text.linkActivated.connect(win.open_link)
        lay.addWidget(self.box)
        grid = QVBoxLayout()
        grid.setSpacing(4)
        self.info = {}
        for key, label in (("checked", "Most recent check for updates:"), ("installed", "Updates were installed:"),
                           ("source", "You receive updates:")):
            row = QHBoxLayout()
            l = QLabel(label, objectName="infolabel")
            l.setFixedWidth(220)
            v = QLabel(objectName="infovalue")
            row.addWidget(l)
            row.addWidget(v, 1)
            grid.addLayout(row)
            self.info[key] = v
        lay.addLayout(grid)
        lay.addStretch(1)

    def refresh_info(self):
        st = load(STATE, {})
        self.info["checked"].setText(when(st.get("checked")))
        self.info["installed"].setText(when(st.get("installed")))
        self.info["source"].setText("For Halcyon, Arch Linux packages, AUR packages and Flatpak apps.")


class Select(QWidget):
    """'Select the updates you want to install': list on the left, details on the right."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 20, 26, 16)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Select the updates you want to install", objectName="title"))
        split = QSplitter()
        self.tree = QTreeWidget(objectName="updates")
        self.tree.setHeaderLabels(["Name", "Version", "Size"])
        self.tree.setRootIsDecorated(True)
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        self.tree.setColumnWidth(1, 190)
        self.tree.setColumnWidth(2, 70)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.currentItemChanged.connect(self.show_details)
        self.tree.itemChanged.connect(lambda *_: self.update_total())
        split.addWidget(self.tree)
        self.details = QTextBrowser(objectName="details")
        split.addWidget(self.details)
        split.setSizes([560, 240])
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        self.total = QLabel(objectName="infolabel")
        row.addWidget(self.total, 1)
        ok = QPushButton("OK", default=True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(lambda: win.show_page("home"))
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def fill(self, ups):
        self.tree.blockSignals(True)
        self.tree.clear()
        groups = (("Important", "repo", ups["repo"], False), ("Optional", "aur", ups["aur"], True),
                  ("Optional", "flatpak", ups["flatpak"], True))
        heads = {}
        for title, kind, items, optional in groups:
            if not items:
                continue
            h = heads.get(title)
            if h is None:
                h = heads[title] = QTreeWidgetItem([title])
                f = h.font(0)
                f.setBold(True)
                h.setFont(0, f)
                h.setFlags(Qt.ItemIsEnabled)
                self.tree.addTopLevelItem(h)
                h.setExpanded(True)
            for name, old, new, size, desc in items:
                it = QTreeWidgetItem(h, [name, f"{old} → {new}" if old else new, human(size) if size else ""])
                it.setData(0, Qt.UserRole, {"kind": kind, "name": name, "old": old, "new": new, "size": size,
                                            "desc": desc})
                it.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
                if optional:
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(0, Qt.Checked if self.win.wanted(kind, name) else Qt.Unchecked)
                else:
                    # Arch only supports upgrading the whole system at once.
                    it.setCheckState(0, Qt.Checked)
                    it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
                    it.setToolTip(0, "System updates are installed together.")
        for h in heads.values():
            n = h.childCount()
            h.setText(0, f"{h.text(0)} ({n})")
        self.tree.blockSignals(False)
        self.update_total()
        self.details.setHtml("<p style='color:#555'>Select an update to see its details.</p>")

    def chosen(self):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            h = self.tree.topLevelItem(i)
            for j in range(h.childCount()):
                it = h.child(j)
                if it.checkState(0) == Qt.Checked:
                    out.append(it.data(0, Qt.UserRole))
        return out

    def update_total(self):
        c = self.chosen()
        size = sum(x["size"] for x in c)
        self.total.setText(f"Total selected: {len(c)} update{'s' if len(c) != 1 else ''}"
                           + (f", {human(size)}" if size else ""))

    def show_details(self, it, _prev=None):
        d = it.data(0, Qt.UserRole) if it else None
        if not d:
            return
        src = {"repo": "Arch Linux / Halcyon", "aur": "AUR (built on your computer)", "flatpak": "Flathub"}[d["kind"]]
        self.details.setHtml(
            f"<p style='font-size:12pt; color:#1e3287'><b>{d['name']}</b></p>"
            f"<p>{d['desc']}</p>"
            f"<p><b>Version:</b> {d['old'] + ' → ' if d['old'] else ''}{d['new']}<br>"
            f"<b>Download size:</b> {human(d['size']) if d['size'] else 'Unknown'}<br>"
            f"<b>From:</b> {src}</p>")

    def accept(self):
        skip = set()
        for i in range(self.tree.topLevelItemCount()):
            h = self.tree.topLevelItem(i)
            for j in range(h.childCount()):
                d = h.child(j).data(0, Qt.UserRole)
                if d["kind"] != "repo" and h.child(j).checkState(0) != Qt.Checked:
                    skip.add(f"{d['kind']}:{d['name']}")
        self.win.skipped = skip
        self.win.show_page("home")
        self.win.show_status()


class History(QWidget):
    def __init__(self, win):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 20, 26, 16)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Review your update history", objectName="title"))
        lay.addWidget(QLabel("Updates that were installed on this computer, newest first.", objectName="infolabel"))
        self.tree = QTreeWidget(rootIsDecorated=False, alternatingRowColors=True)
        self.tree.setHeaderLabels(["Name", "Status", "Version", "Date installed"])
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("OK")
        ok.clicked.connect(lambda: win.show_page("home"))
        row.addWidget(ok)
        lay.addLayout(row)

    def refresh(self):
        self.tree.clear()
        for ts, name, ver, result in history():
            it = QTreeWidgetItem([name, result, ver, when(ts)])
            if result == "Failed":
                it.setForeground(1, QColor("#c6282d"))
            self.tree.addTopLevelItem(it)


class Settings(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 20, 26, 16)
        lay.setSpacing(12)
        lay.addWidget(QLabel("Choose how Halcyon can install updates", objectName="title"))
        lay.addWidget(QLabel("Halcyon checks for updates once a day and lets you know when some are ready. "
                             "Updates are never installed without asking you.", wordWrap=True, objectName="infolabel"))
        lay.addWidget(QLabel("<b>Important updates</b>"))
        self.mode = QComboBox()
        self.mode.addItems(["Check for updates daily and notify me", "Never check for updates (not recommended)"])
        lay.addWidget(self.mode, 0, Qt.AlignLeft)
        lay.addWidget(QLabel("<b>Optional updates</b>"))
        self.aur = QCheckBox("Include updates for AUR packages")
        self.flatpak = QCheckBox("Include updates for Flatpak apps")
        lay.addWidget(self.aur)
        lay.addWidget(self.flatpak)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("OK", default=True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(lambda: win.show_page("home"))
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def load(self):
        c = load(CONFIG, {})
        self.mode.setCurrentIndex(0 if c.get("notify", True) else 1)
        self.aur.setChecked(c.get("aur", True))
        self.flatpak.setChecked(c.get("flatpak", True))

    def accept(self):
        save(CONFIG, {"notify": self.mode.currentIndex() == 0, "aur": self.aur.isChecked(),
                      "flatpak": self.flatpak.isChecked()})
        self.win.show_page("home")
        self.win.check()


class Window(QWidget):
    def __init__(self, auto_check=True):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon.fromTheme("system-software-update"))
        self.resize(900, 600)
        self.ups = {"repo": [], "aur": [], "flatpak": []}
        self.skipped = set()
        self.state = "idle"
        self.error = ""
        self.restart_needed = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.left = LeftPane()
        self.left.picked.connect(self.nav)
        lay.addWidget(self.left)
        self.pages = QStackedWidget(objectName="main")
        self.home = Home(self)
        self.select = Select(self)
        self.hist = History(self)
        self.settings = Settings(self)
        for w in (self.home, self.select, self.hist, self.settings):
            self.pages.addWidget(w)
        lay.addWidget(self.pages, 1)
        self.proc = None
        self.home.refresh_info()
        if auto_check:
            QTimer.singleShot(200, self.check)
        else:
            self.show_status()

    # --- navigation
    def nav(self, key):
        if key == "check":
            self.show_page("home")
            self.check()
        else:
            self.show_page(key)

    def show_page(self, key):
        page = {"home": self.home, "select": self.select, "history": self.hist, "settings": self.settings}[key]
        if key == "history":
            self.hist.refresh()
        elif key == "settings":
            self.settings.load()
        elif key == "home":
            self.home.refresh_info()
        self.pages.setCurrentWidget(page)

    def open_link(self, href):
        if href == "select":
            self.select.fill(self.ups)
            self.show_page("select")

    def wanted(self, kind, name):
        return f"{kind}:{name}" not in self.skipped

    # --- checking
    def check(self):
        if self.state in ("checking", "installing"):
            return
        self.state = "checking"
        self.home.box.set("busy", "Checking for updates…", progress=-1)
        self.checker = Checker(self)
        self.checker.done.connect(self.checked)
        self.checker.start()

    def checked(self, ups, err):
        self.ups, self.error = ups, err
        st = load(STATE, {})
        st["checked"] = time.time()
        save(STATE, st)
        self.state = "idle"
        self.show_status()

    def todo(self):
        return [("repo", u) for u in self.ups["repo"]] + [
            (k, u) for k in ("aur", "flatpak") for u in self.ups[k] if self.wanted(k, u[0])]

    def show_status(self):
        self.home.refresh_info()
        box = self.home.box
        if self.error:
            box.set("error", "Halcyon could not search for new updates",
                    f"An error occurred while checking for updates:<br><span style='color:#555'>{self.error[:300]}"
                    "</span>", "Try again")
            return
        if self.restart_needed:
            box.set("warn", "Restart your computer to finish installing updates",
                    "Some updates (like a new kernel or graphics driver) take effect after a restart.", "Restart now")
            return
        important = len(self.ups["repo"])
        optional = sum(1 for k in ("aur", "flatpak") for u in self.ups[k])
        chosen = self.todo()
        if not important and not optional:
            box.set("ok", "Halcyon is up to date", "There are no updates available for your computer.")
            return
        size = sum(u[3] for _k, u in chosen)
        lines = []
        if important:
            lines.append(f"<a href='select' style='color:#1e3287'>{important} important update"
                         f"{'s are' if important != 1 else ' is'} available</a>")
        if optional:
            picked = sum(1 for k, _u in chosen if k != "repo")
            lines.append(f"<a href='select' style='color:#1e3287'>{optional} optional update"
                         f"{'s are' if optional != 1 else ' is'} available</a>"
                         + (f" <span style='color:#555'>({picked} selected)</span>" if picked != optional else ""))
        if size:
            lines.append(f"<span style='color:#555'>{len(chosen)} update{'s' if len(chosen) != 1 else ''} "
                         f"selected, {human(size)}</span>")
        box.set("warn", "Download and install updates for your computer", "<br>".join(lines),
                "Install updates" if chosen else None)

    def primary_action(self):
        if self.error:
            self.check()
        elif self.restart_needed:
            subprocess.Popen(["qdbus6", "org.kde.LogoutPrompt", "/LogoutPrompt", "promptReboot"])
        else:
            self.install()

    # --- installing
    def install(self):
        chosen = self.todo()
        if not chosen or self.state != "idle":
            return
        self.state = "installing"
        self.plan = []
        repo = [u for k, u in chosen if k == "repo"]
        if repo:
            self.plan.append(("repo", repo))
        flat = [u for k, u in chosen if k == "flatpak"]
        if flat:
            self.plan.append(("flatpak", flat))
        aur = [u for k, u in chosen if k == "aur"]
        if aur:
            self.plan.append(("aur", aur))
        self.total = len(chosen)
        self.done = 0
        self.installed_names = []
        self.failed = []
        self.next_step()

    def next_step(self):
        if not self.plan:
            return self.finished()
        kind, items = self.plan.pop(0)
        self.step_kind, self.step_items = kind, items
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("LANG", "C")
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(self.on_output)
        p.finished.connect(self.on_step_done)
        self.proc, self.out = p, ""
        if kind == "repo":
            self.home.box.set("busy", "Downloading updates…", "Waiting for your permission…", progress=-1)
            # pkexec shows the password dialog; pacman runs as root.
            p.start("pkexec", ["env", "LANG=C", "pacman", "-Syu", "--noconfirm", "--noprogressbar"])
        elif kind == "flatpak":
            self.home.box.set("busy", "Installing updates…", "Updating Flatpak apps…", progress=self._pct())
            p.start("flatpak", ["update", "-y", "--noninteractive"] + [u[0] for u in items])
        else:
            # AUR builds can ask questions and need sudo: they get a terminal.
            self.home.box.set("busy", "Installing updates…",
                              "AUR packages are being built in a terminal window.", progress=-1)
            p.start("konsole", ["--hide-menubar", "-e", "halcyon-aur", "--yes", "install"] + [u[0] for u in items])

    def _pct(self):
        return int(self.done * 100 / max(1, self.total))

    def on_output(self):
        chunk = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        self.out += chunk
        for line in chunk.splitlines():
            m = re.search(r"\((\d+)/(\d+)\) (upgrading|installing|reinstalling|downgrading) (\S+)", line)
            if m and self.step_kind == "repo":
                i, n, name = int(m.group(1)), int(m.group(2)), m.group(4)
                self.home.box.set("busy", "Installing updates…", f"Installing update {i} of {n}: {name}",
                                  progress=int(i * 100 / max(1, n)))
                if RESTART.match(name):
                    self.restart_needed = True
            elif self.step_kind == "repo" and re.search(r"downloading|Retrieving packages|Synchronizing", line):
                self.home.box.set("busy", "Downloading updates…", line.strip()[:120], progress=-1)

    def on_step_done(self, code, _status):
        names = [u[0] for u in self.step_items]
        if code == 0:
            self.done += len(names)
            self.installed_names += names
            if self.step_kind != "repo":
                st = load(STATE, {})
                hist = st.setdefault("history", [])
                for u in self.step_items:
                    hist.append({"time": time.time(), "name": u[0], "version": u[2], "result": "Successful"})
                st["history"] = hist[-500:]
                save(STATE, st)
        else:
            self.failed.append((self.step_kind, self.out.strip().splitlines()[-6:] if self.out.strip() else []))
            if self.step_kind == "repo" and code in (126, 127):
                self.failed[-1] = (self.step_kind, ["You didn't give permission to install updates."])
        self.next_step()

    def finished(self):
        self.state = "idle"
        st = load(STATE, {})
        if self.installed_names:
            st["installed"] = time.time()
        save(STATE, st)
        if self.failed:
            kind, lines = self.failed[0]
            self.error = ""
            self.home.box.set("error", "Some updates were not installed",
                              "<span style='color:#555'>" + "<br>".join(l.replace("<", "&lt;") for l in lines)
                              + "</span>", "Try again")
            self.ups = {"repo": [], "aur": [], "flatpak": []}
            self.home.box.button.clicked.disconnect()
            self.home.box.button.clicked.connect(self._retry)
            return
        self.ups = {"repo": [], "aur": [], "flatpak": []}
        if self.restart_needed:
            self.show_status()
        else:
            self.home.box.set("ok", "The updates were successfully installed",
                              f"Succeeded: {len(self.installed_names)} update"
                              f"{'s' if len(self.installed_names) != 1 else ''}.")
        self.home.refresh_info()

    def _retry(self):
        self.home.box.button.clicked.disconnect()
        self.home.box.button.clicked.connect(self.primary_action)
        self.check()

    def closeEvent(self, e):
        if self.state == "installing":
            QMessageBox.information(self, APP, "Updates are being installed. Please wait until they finish.")
            e.ignore()


QSS = """
QWidget#main { background: white; }
QLabel#title { font-size: 15pt; color: #1e3287; }
QPushButton#link, QPushButton#linkhome { border: none; background: transparent; color: #1e3287; text-align: left;
                                        padding: 0; font-size: 9.5pt; }
QPushButton#linkhome { font-weight: bold; }
QPushButton#link:hover, QPushButton#linkhome:hover { color: #3a6ee0; text-decoration: underline; }
QLabel#seealso { color: #555; font-size: 8.5pt; }
QLabel#statushead { font-size: 12pt; color: #1b1b1b; }
QLabel#statustext { color: #1b1b1b; }
QLabel#infolabel { color: #555; }
QLabel#infovalue { color: #1b1b1b; }
QTreeWidget#updates, QTextBrowser#details { border: 1px solid #b9c3cf; background: white; }
QPushButton#action { padding: 5px 16px; }
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("halcyon-update")
    app.setDesktopFileName("halcyon-update")
    app.setStyleSheet(QSS)
    w = Window()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
