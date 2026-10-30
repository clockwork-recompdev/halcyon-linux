"""Halcyon Welcome Center: the first thing you see after logging in, the way
Windows Vista did it. The top shows this computer; below, "Get started" tasks
(each opens the right Halcyon tool, a few can be done right here) and the
Halcyon extras. Clicking a task describes it up top, double-click opens it."""
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

APP = "Welcome Center"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "halcyon" / "welcome.json"
GAMES_CONFIG = CONFIG.parent / "games.json"
LIVE = os.path.isdir("/run/archiso")
DEV_LOGO = Path(__file__).resolve().parent.parent.parent / "branding" / "assets" / "logo.png"
TITLE = "#1e3287"           # Vista's dark blue headings
LINK = "#0066cc"


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def spawn(cmd):
    try:
        subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# ------------------------------------------------------------ this computer ---
def os_release():
    d = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            k, _, v = line.partition("=")
            d[k] = v.strip('"')
    except OSError:
        pass
    return d


def halcyon_version():
    m = re.search(r"halcyon-branding (\S+)", run("pacman", "-Q", "halcyon-branding"))
    return m.group(1).rsplit("-", 1)[0] if m else None


def cpu():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                name = line.split(":", 1)[1].strip()
                return re.sub(r"\s+", " ", re.sub(r"\((R|TM|tm|r)\)|CPU|Processor|\d+-Core", "", name)).strip()
    except OSError:
        pass
    return platform.processor() or "Unknown"


def cores():
    return os.cpu_count() or 1


def memory():
    try:
        kb = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1))
        return f"{kb / 1024 / 1024:.1f} GB" if kb > 1024 * 1024 else f"{kb // 1024} MB"
    except (OSError, AttributeError):
        return "Unknown"


def graphics():
    """Graphics adapters by name, e.g. ['NVIDIA GeForce RTX 3070']."""
    out = []
    for line in run("lspci", "-mm").splitlines():
        if re.search(r'"(VGA compatible controller|3D controller|Display controller)"', line):
            parts = re.findall(r'"([^"]*)"', line)
            if len(parts) >= 3:
                vendor, dev = parts[1], parts[2]
                vendor = {"Advanced Micro Devices, Inc. [AMD/ATI]": "AMD", "NVIDIA Corporation": "NVIDIA",
                          "Intel Corporation": "Intel"}.get(vendor, vendor.split(",")[0])
                m = re.search(r"\[([^\]]+)\]", dev)
                out.append(f"{vendor} {m.group(1) if m else dev}")
    return out or ["Unknown"]


def has_nvidia():
    return any(g.startswith("NVIDIA") for g in graphics())


def nvidia_driver_loaded():
    return os.path.exists("/proc/driver/nvidia/version")


def disk():
    try:
        st = os.statvfs(str(Path.home()))
        free, total = st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize
        return f"{free / 1e9:.0f} GB free of {total / 1e9:.0f} GB"
    except OSError:
        return "Unknown"


def plasma():
    m = re.search(r"([\d.]+)", run("plasmashell", "--version"))
    return f"KDE Plasma {m.group(1)}" if m else "KDE Plasma"


def user_picture(size):
    for p in (Path.home() / ".face.icon", Path.home() / ".face",
              Path("/var/lib/AccountsService/icons") / os.environ.get("USER", "")):
        if p.exists():
            pm = QPixmap(str(p))
            if not pm.isNull():
                return pm
    return QIcon.fromTheme("user-identity", QIcon.fromTheme("system-users")).pixmap(size)


def logo(size):
    ic = QIcon.fromTheme("halcyon-logo")
    if not ic.isNull():
        return ic.pixmap(size)
    return QPixmap(str(DEV_LOGO)) if DEV_LOGO.exists() else QPixmap()


def tiling_now():
    out = run("qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.isScriptLoaded", "krohnkite")
    return out.strip() == "true"


# ------------------------------------------------------------------ tasks ---
def sgdb_key():
    return load(GAMES_CONFIG, {}).get("sgdb_key", "")


def check_sgdb(key):
    req = urllib.request.Request("https://www.steamgriddb.com/api/v2/search/autocomplete/halo",
                                 headers={"Authorization": f"Bearer {key}", "User-Agent": "Halcyon"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def tasks():
    """(id, icon, title, description, [(button label, command or callable name)], section)."""
    nv = has_nvidia()
    gpu_note = ""
    if nv and not nvidia_driver_loaded():
        gpu_note = (" <b>This computer has an NVIDIA graphics card without its driver yet</b> - tick "
                    "\"NVIDIA driver\" in Gaming Setup for full speed in games.")
    t = []
    if LIVE:
        t.append(("install", "system-software-install", "Install Halcyon",
                  "You're running Halcyon from the disc or USB stick. Install it to this computer to keep your "
                  "files and settings, and to make it start faster.", [("Install Halcyon", ["halcyon-install"])],
                  "start"))
    t += [
        ("details", "computer", "View computer details",
         "See this computer's processor, memory, graphics and storage, and which version of Halcyon it runs.",
         [("Show details", "details")], "start"),
        ("mode", "preferences-system-windows-actions", "Choose how windows work",
         "Halcyon has two desktops. <b>Classic</b> is the floating Aero desktop with the taskbar. "
         "<b>Tiling</b> lays windows out side by side with gaps and a top bar, keyboard first. "
         "Switch any time with Super+Ctrl+Alt+Space.",
         [("Classic Aero", ["halcyon-mode", "classic"]), ("Tiling", ["halcyon-mode", "tiling"])], "start"),
        ("personalize", "preferences-desktop-wallpaper", "Personalize Halcyon",
         "Change the desktop background, colors, and the Windows 7 or Vista look of the windows.",
         [("Desktop background", ["systemsettings", "kcm_wallpaper"]), ("Window look", ["halcyon-wintheme"])],
         "start"),
        ("gaming", "input-gaming", "Set up gaming",
         "Install Steam, Wine for Windows games, Lutris, Heroic, MangoHud, GameMode, the NVIDIA driver and "
         "more, all at once." + gpu_note, [("Open Gaming Setup", ["halcyon-gaming"])], "start"),
        ("artwork", "folder-pictures", "Get game artwork",
         "Halcyon Games and its full-screen Big Picture view can fetch box art, banners and logos for every "
         "game from SteamGridDB. Make a free account at steamgriddb.com, open <i>Preferences → API</i>, and "
         "paste the key here.", [("Save key", "sgdb")], "start"),
        ("discs", "media-optical", "Install games and programs from discs",
         "Put in a PC game or program disc and AutoPlay offers to install it - it runs in Halcyon's shared "
         "Windows environment, with the disc as drive D:. Old DOS games run in DOSBox, and movie discs play "
         "in Media Center.", [("Reset AutoPlay choices", ["halcyon-autoplay-dialog", "--reset"])], "start"),
        ("update", "system-software-update", "Keep Halcyon up to date",
         "Halcyon Update installs fixes and new features for Halcyon, your apps and the system. It checks "
         "every day and tells you when updates are ready.", [("Open Halcyon Update", ["halcyon-update"])],
         "start"),
        ("users", "system-users", "Add user accounts",
         "Give everyone who uses this computer their own account, with their own files and settings.",
         [("User accounts", ["systemsettings", "kcm_users"])], "start"),
        ("network", "network-wireless", "Connect to the Internet",
         "Join a wireless network, or set up a wired, mobile or VPN connection.",
         [("Network connections", ["systemsettings", "kcm_networkmanagement"])], "start"),
        ("keys", "input-keyboard", "Learn the keyboard shortcuts",
         "Super+K shows every Halcyon shortcut: launching apps, moving windows, switching desktops and more.",
         [("Show shortcuts", ["halcyon-keys"])], "start"),
        # Halcyon extras
        ("games", "applications-games", "Halcyon Games",
         "All your games in one place - Steam, Windows games and Linux games - with a full-screen view for "
         "the TV that a gamepad drives. Games without controller support get one automatically.",
         [("Open Halcyon Games", ["halcyon-games"]), ("Big Picture", ["halcyon-games", "--big"])], "extras"),
        ("mediacenter", "video-display", "Media Center",
         "Movies, TV, music and pictures on the big screen, from your Jellyfin or Plex server, your Pictures "
         "folder, or a DVD or Blu-ray in the drive.", [("Open Media Center", ["halcyon-mediacenter"])],
         "extras"),
        ("messenger", "im-user", "Halcyon Messenger",
         "Chat with your Discord friends in a classic Messenger window.",
         [("Open Messenger", ["halcyon-messenger"])], "extras"),
        ("retro", "internet-web-browser", "Retro Browser",
         "Browse the restored web of the 1990s and 2000s through Protoweb, with Flash and Java applets that "
         "work again.", [("Open Retro Browser", ["halcyon-retro-browser"])], "extras"),
        ("player", "multimedia-player", "Halcyon Player",
         "A music player with the classic skins.", [("Open Player", ["halcyon-player"])], "extras"),
        ("gadgets", "preferences-desktop-gadgets", "Desktop gadgets",
         "Put a clock, the weather, a slide show and more on your desktop.",
         [("Show gadgets", ["halcyon-gadgets"])], "extras"),
    ]
    # Hide what isn't installed (dev checkouts, trimmed installs).
    out = []
    for tid, icon, title, desc, actions, section in t:
        actions = [(lbl, c) for lbl, c in actions if not isinstance(c, list) or shutil.which(c[0])]
        if actions:
            out.append((tid, icon, title, desc, actions, section))
    return out


# --------------------------------------------------------------- widgets ---
class Bus(QObject):
    sgdb_checked = Signal(bool, str)


class Item(QWidget):
    """One task in the grid: icon and label, highlighted the Vista way."""
    clicked = Signal(str)
    activated = Signal(str)

    def __init__(self, tid, icon, title):
        super().__init__()
        self.tid, self.selected, self.hover = tid, False, False
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(46)
        self.setMinimumWidth(200)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(QIcon.fromTheme(icon, QIcon.fromTheme("applications-other")).pixmap(32))
        ic.setFixedSize(32, 32)
        lay.addWidget(ic)
        self.label = QLabel(title, wordWrap=True)
        self.label.setStyleSheet("color: #1e1e1e;")
        lay.addWidget(self.label, 1)
        self.setToolTip(title)

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.tid)

    def mouseDoubleClickEvent(self, e):
        self.activated.emit(self.tid)

    def paintEvent(self, e):
        if not (self.hover or self.selected):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(0, 0, 0, self.height())
        if self.selected:
            g.setColorAt(0, QColor("#dcebfc"))
            g.setColorAt(1, QColor("#c1dbfc"))
            pen = QColor("#7da2ce")
        else:
            g.setColorAt(0, QColor("#f8fbfe"))
            g.setColorAt(1, QColor("#e8f1fc"))
            pen = QColor("#b8d6fb")
        p.setPen(QPen(pen, 1))
        p.setBrush(g)
        p.drawRoundedRect(r, 3, 3)
        p.setPen(QPen(QColor(255, 255, 255, 170), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 2, 2)


class Header(QWidget):
    """The glass address bar across the top: Halcyon ▸ Welcome Center."""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(38)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#c9dcf3"))
        g.setColorAt(0.5, QColor("#b6cdec"))
        g.setColorAt(0.51, QColor("#a8c1e5"))
        g.setColorAt(1, QColor("#bed3ef"))
        p.fillRect(self.rect(), g)
        p.setPen(QColor("#8aa5c9"))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        box = QRectF(10, 7, self.width() - 20, self.height() - 14)
        p.setPen(QColor("#7f9db9"))
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawRect(box)
        p.drawPixmap(int(box.x()) + 5, int(box.y()) + 3, QIcon.fromTheme("computer").pixmap(16))
        f = QFont(self.font())
        f.setPointSizeF(9)
        p.setFont(f)
        p.setPen(QColor("#1e1e1e"))
        p.drawText(box.adjusted(28, 0, 0, 0), Qt.AlignVCenter, "Halcyon  ▸  Welcome Center")


class Top(QFrame):
    """The upper pane: this computer, or the selected task's description."""
    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#ffffff"))
        g.setColorAt(1, QColor("#eef4fb"))
        p.fillRect(self.rect(), g)
        # a soft aurora sweep in the corner, the Vista welcome look
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        w, h = self.width(), self.height()
        path.moveTo(w * 0.45, h)
        path.cubicTo(w * 0.62, h * 0.55, w * 0.8, h * 0.75, w, h * 0.25)
        path.lineTo(w, h)
        path.closeSubpath()
        sweep = QLinearGradient(w * 0.45, 0, w, 0)
        sweep.setColorAt(0, QColor(120, 180, 240, 0))
        sweep.setColorAt(1, QColor(120, 180, 240, 60))
        p.fillPath(path, sweep)
        p.setPen(QColor("#d5e1ef"))
        p.drawLine(0, h - 1, w, h - 1)


def link_button(text):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFlat(True)
    b.setStyleSheet(f"QPushButton {{ color: {LINK}; border: none; padding: 2px 0; text-align: left; }}"
                    f"QPushButton:hover {{ color: #3399ff; text-decoration: underline; }}")
    return b


class Welcome(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon.fromTheme("halcyon-logo", QIcon.fromTheme("help-about")))
        self.resize(840, 700)
        self.cfg = load(CONFIG, {"startup": True})
        self.bus = Bus()
        self.bus.sgdb_checked.connect(self._sgdb_result)
        self.tasks = {t[0]: t for t in tasks()}
        self.items = {}
        self.more = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(Header())

        self.top = Top()
        self.top.setMinimumHeight(190)
        self.top_lay = QHBoxLayout(self.top)
        self.top_lay.setContentsMargins(24, 18, 24, 16)
        self.top_lay.setSpacing(18)
        outer.addWidget(self.top)

        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("body")
        body.setStyleSheet("#body { background: white; }")
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(20, 12, 20, 12)
        self.body_lay.setSpacing(6)
        self.section("Get started with Halcyon", [t for t in self.tasks.values() if t[5] == "start"])
        self.section("Halcyon extras", [t for t in self.tasks.values() if t[5] == "extras"])
        self.body_lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        foot = QFrame()
        foot.setObjectName("foot")
        foot.setStyleSheet("#foot { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f4f7fb, "
                           "stop:1 #e3eaf3); border-top: 1px solid #c5d2e3; }")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 8, 20, 8)
        self.startup = QCheckBox("Run at startup (opens this window when you log on)")
        self.startup.setChecked(bool(self.cfg.get("startup", True)))
        self.startup.toggled.connect(self.set_startup)
        fl.addWidget(self.startup)
        fl.addStretch(1)
        outer.addWidget(foot)
        self.show_details()

    # --- layout
    def section(self, title, items):
        if not items:
            return
        head = QHBoxLayout()
        lbl = QLabel(title)
        f = lbl.font()
        f.setPointSizeF(11.5)
        lbl.setFont(f)
        lbl.setStyleSheet(f"color: {TITLE};")
        head.addWidget(lbl)
        line = QFrame(frameShape=QFrame.HLine)
        line.setStyleSheet("color: #c9d6e6;")
        head.addWidget(line, 1)
        self.body_lay.addSpacing(6)
        self.body_lay.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        for i, (tid, icon, name, *_rest) in enumerate(items):
            it = Item(tid, icon, name)
            it.clicked.connect(self.select)
            it.activated.connect(self.activate)
            self.items[tid] = it
            grid.addWidget(it, i // 3, i % 3)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        self.body_lay.addLayout(grid)

    def clear_top(self):
        while self.top_lay.count():
            it = self.top_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            elif it.layout():
                self._drop(it.layout())

    def _drop(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._drop(it.layout())

    def text(self, s, size=9.0, color="#1e1e1e", bold=False, wrap=True):
        lbl = QLabel(s, wordWrap=wrap, textFormat=Qt.RichText)
        f = lbl.font()
        f.setPointSizeF(size)
        f.setBold(bold)
        lbl.setFont(f)
        lbl.setStyleSheet(f"color: {color};")
        lbl.setOpenExternalLinks(True)
        return lbl

    # --- the top pane
    def show_details(self):
        self.clear_top()
        for it in self.items.values():
            it.selected = it.tid == "details"
            it.update()
        pic = QLabel()
        pm = user_picture(96).scaled(96, 96, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        frame = QPixmap(108, 108)
        frame.fill(Qt.transparent)
        p = QPainter(frame)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#9fb6d3"), 1))
        p.setBrush(QColor("white"))
        p.drawRoundedRect(QRectF(0.5, 0.5, 107, 107), 6, 6)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(6, 6, 96, 96), 4, 4)
        p.setClipPath(clip)
        p.drawPixmap(6, 6, pm)
        p.end()
        pic.setPixmap(frame)
        pic.setAlignment(Qt.AlignTop)
        self.top_lay.addWidget(pic, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(4)
        rel = os_release()
        ver = halcyon_version()
        name = rel.get("NAME", "Halcyon Linux")
        if ver:
            name = "Halcyon Linux"
        elif "halcyon" not in name.lower():
            name = f"Halcyon on {rel.get('PRETTY_NAME', name)}"
        col.addWidget(self.text(name + (f"  <span style='color:#5a6b80'>{ver}</span>" if ver else ""),
                                14, TITLE))
        rows = [("Processor", f"{cpu()} ({cores()} threads)"), ("Memory (RAM)", memory()),
                ("Graphics", ", ".join(graphics())), ("Computer name", socket.gethostname())]
        if self.more:
            rows += [("Desktop", plasma()), ("Kernel", platform.release()), ("Home folder", disk()),
                     ("User", os.environ.get("USER", ""))]
            if LIVE:
                rows.append(("Session", "Live (running from the disc or USB stick)"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(3)
        for i, (k, v) in enumerate(rows):
            grid.addWidget(self.text(k, color="#5a6b80", wrap=False), i, 0, Qt.AlignTop)
            grid.addWidget(self.text(v), i, 1)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)
        more = link_button("Show fewer details" if self.more else "Show more details")
        more.clicked.connect(self.toggle_more)
        col.addWidget(more, 0, Qt.AlignLeft)
        col.addStretch(1)
        self.top_lay.addLayout(col, 1)
        lg = QLabel()
        pm = logo(96)
        if not pm.isNull():
            lg.setPixmap(pm.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.top_lay.addWidget(lg, 0, Qt.AlignTop | Qt.AlignRight)

    def toggle_more(self):
        self.more = not self.more
        self.show_details()

    def select(self, tid):
        if tid == "details":
            return self.show_details()
        for it in self.items.values():
            it.selected = it.tid == tid
            it.update()
        if tid not in self.tasks:
            return
        tid, icon, title, desc, actions, _ = self.tasks[tid]
        self.clear_top()
        ic = QLabel()
        ic.setPixmap(QIcon.fromTheme(icon, QIcon.fromTheme("applications-other")).pixmap(64))
        self.top_lay.addWidget(ic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(8)
        col.addWidget(self.text(title, 14, TITLE))
        col.addWidget(self.text(desc, 9.5))
        row = QHBoxLayout()
        row.setSpacing(18)
        for label, cmd in actions:
            if cmd == "sgdb":
                row.addLayout(self.sgdb_row())
                continue
            if cmd == "details":
                b = link_button(label)
                b.clicked.connect(self.show_details)
            else:
                b = link_button(label)
                b.clicked.connect(lambda _=False, c=cmd, t=tid: self.launch(c, t))
            row.addWidget(b)
        if tid == "mode":
            row.addWidget(self.text(f"<i>Now: {'Tiling' if tiling_now() else 'Classic Aero'}</i>",
                                    color="#5a6b80", wrap=False))
        row.addStretch(1)
        col.addLayout(row)
        self.status = self.text("", color="#5a6b80")
        col.addWidget(self.status)
        col.addStretch(1)
        self.top_lay.addLayout(col, 1)

    def sgdb_row(self):
        row = QHBoxLayout()
        self.key = QLineEdit(sgdb_key(), placeholderText="Paste your SteamGridDB API key")
        self.key.setMinimumWidth(280)
        self.key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        save_b = QPushButton("Save key")
        save_b.clicked.connect(self.save_sgdb)
        self.key.returnPressed.connect(self.save_sgdb)
        site = link_button("Get a key at steamgriddb.com")
        site.clicked.connect(lambda: spawn(["xdg-open", "https://www.steamgriddb.com/profile/preferences/api"]))
        row.addWidget(self.key)
        row.addWidget(save_b)
        row.addWidget(site)
        return row

    def save_sgdb(self):
        key = self.key.text().strip()
        if not key:
            return
        self.status.setText("Checking the key…")

        def work():
            ok = check_sgdb(key)
            self.bus.sgdb_checked.emit(ok, key)
        threading.Thread(target=work, daemon=True).start()

    def _sgdb_result(self, ok, key):
        if not ok:
            self.status.setText("<span style='color:#c6282d'>SteamGridDB didn't accept that key - check "
                                "it was copied whole.</span>")
            return
        cfg = load(GAMES_CONFIG, {})
        cfg["sgdb_key"] = key
        save(GAMES_CONFIG, cfg)
        self.status.setText("<span style='color:#3a8a1e'>✔ Saved. Halcyon Games fetches the artwork the next "
                            "time it opens.</span>")

    def activate(self, tid):
        _, _, _, _, actions, _ = self.tasks[tid]
        self.select(tid)
        label, cmd = actions[0]
        if isinstance(cmd, list):
            self.launch(cmd, tid)

    def launch(self, cmd, tid):
        if not spawn(cmd):
            self.status.setText(f"Couldn't start {cmd[0]}.")
            return
        self.status.setText("Opening…")
        if tid == "mode":
            self.status.setText(f"Switching to {'tiling' if cmd[-1] == 'tiling' else 'the classic desktop'}…")

    def set_startup(self, on):
        self.cfg["startup"] = on
        save(CONFIG, self.cfg)


def light_palette():
    pal = QPalette()
    for role, color in ((QPalette.Window, "#f0f0f0"), (QPalette.WindowText, "#000000"),
                        (QPalette.Base, "#ffffff"), (QPalette.Text, "#000000"),
                        (QPalette.Button, "#ececec"), (QPalette.ButtonText, "#000000"),
                        (QPalette.PlaceholderText, "#6d6d6d"), (QPalette.Highlight, "#3399ff"),
                        (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#ffffe1"),
                        (QPalette.ToolTipText, "#000000")):
        pal.setColor(role, QColor(color))
    return pal


def main():
    if "--startup" in sys.argv and not load(CONFIG, {"startup": True}).get("startup", True):
        return 0
    app = QApplication(sys.argv[:1])
    app.setApplicationName("halcyon-welcome")
    app.setDesktopFileName("halcyon-welcome")
    app.setPalette(light_palette())        # the Welcome Center is light, like Vista's
    w = Welcome()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
