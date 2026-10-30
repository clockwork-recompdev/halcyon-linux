"""Halcyon Search, after Vista's Search window: one box for programs,
files, e-mail, contacts, appointments and sticky notes, with "Show only"
buttons (All, E-mail, Document, Picture, Music, Other) and results that
open in the right Halcyon app.

    halcyon-search [TEXT]

Files come from Baloo when file indexing is on, otherwise from a quick walk
of your home folders."""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QFileInfo, QMimeDatabase, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QLinearGradient, QPainter, QPalette
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileIconProvider, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMainWindow, QMenu, QPushButton, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

APP = "Search Results"
TEXT = "#1e395b"
GRAY = "#6d7f95"
HOME = Path.home()
DATA = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share"))
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
DOC = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".ods", ".csv", ".ppt", ".pptx",
       ".odp", ".epub", ".html", ".htm", ".tex", ".pages", ".key", ".numbers", ".wpd", ".wps", ".xps"}
PIC = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".avif", ".svg", ".raw", ".cr2",
       ".nef", ".ico", ".psd", ".xcf", ".kra", ".jxl"}
MUSIC = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma", ".wav", ".aiff", ".mid", ".midi"}
FILTERS = [("all", "All"), ("mail", "E-mail"), ("doc", "Document"), ("pic", "Picture"), ("music", "Music"),
           ("other", "Other")]
SKIP_DIRS = {".cache", ".local", ".config", ".var", ".git", "node_modules", ".steam", ".mozilla", ".wine",
             "__pycache__", ".npm", ".cargo", ".rustup", ".gradle", "go", ".venv", "venv", "Trash"}
WALK_ROOTS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "Public", "Templates")


def kind_of(path):
    ext = Path(path).suffix.lower()
    return "doc" if ext in DOC else "pic" if ext in PIC else "music" if ext in MUSIC else "other"


def score(name, q):
    n = name.lower()
    if n == q:
        return 0
    if n.startswith(q):
        return 1
    if re.search(r"\b" + re.escape(q), n):
        return 2
    return 3


def spawn(cmd):
    try:
        subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def halcyon(cmd, *args):
    """A Halcyon app, from the system or this checkout."""
    if shutil.which(cmd):
        return [cmd, *args]
    dev = Path(__file__).resolve().parents[2] / "branding" / "bin" / cmd
    return [str(dev), *args]


# ------------------------------------------------------------------ sources ---
def apps():
    out, seen = [], set()
    dirs = [DATA / "applications"] + [Path(d) / "applications" for d in
                                       os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")]
    for d in dirs:
        try:
            files = sorted(d.glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            if f.name in seen:
                continue
            seen.add(f.name)
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            sec = text.split("[Desktop Action")[0]
            get = lambda k: (re.search(rf"^{k}=(.*)$", sec, re.M) or [None, ""])[1].strip()
            if get("NoDisplay").lower() == "true" or get("Hidden").lower() == "true" or get("Type") != "Application":
                continue
            out.append({"name": get("Name"), "generic": get("GenericName"), "comment": get("Comment"),
                        "keywords": get("Keywords"), "icon": get("Icon"), "file": str(f), "id": f.stem})
    return out


def baloo(q, limit=300):
    if not shutil.which("baloosearch6"):
        return None
    try:
        st = subprocess.run(["balooctl6", "status"], capture_output=True, text=True, timeout=3).stdout
        if "disabled" in st.lower() or "not running" in st.lower():
            return None
        r = subprocess.run(["baloosearch6", "-l", str(limit), q], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [line for line in r.stdout.splitlines() if line.startswith("/")]


class FileIndex:
    """Without Baloo: the names of everything in the usual home folders, gathered once."""
    def __init__(self):
        self.paths = []
        self.ready = False
        threading.Thread(target=self.build, daemon=True).start()

    def build(self):
        out, n = [], 0
        roots = [HOME / r for r in WALK_ROOTS if (HOME / r).is_dir()] or [HOME]
        for root in roots:
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
                for f in files:
                    if not f.startswith("."):
                        out.append(os.path.join(dirpath, f))
                n += 1
                if len(out) > 200000:
                    break
        self.paths = out
        self.ready = True

    def find(self, q, limit=300):
        q = q.lower()
        hits = [p for p in self.paths if q in os.path.basename(p).lower()]
        hits.sort(key=lambda p: score(os.path.basename(p), q))
        return hits[:limit]


def contacts(q):
    try:
        people = json.loads((DATA / "halcyon" / "contacts.json").read_text())
    except (OSError, ValueError):
        return []
    return [p for p in people if q in " ".join(str(v) for v in p.values()).lower()]


def appointments(q):
    try:
        d = json.loads((DATA / "halcyon" / "calendar.json").read_text())
    except (OSError, ValueError):
        return []
    out = []
    for e in d.get("events", []):
        if q in f"{e.get('title', '')} {e.get('location', '')} {e.get('notes', '')}".lower():
            out.append(e)
    for t in d.get("tasks", []):
        if q in t.get("title", "").lower():
            out.append(dict(t, task=True))
    return out


def notes(q):
    try:
        d = json.loads((DATA / "halcyon" / "sticky-notes.json").read_text())
    except (OSError, ValueError):
        return []
    from html.parser import HTMLParser

    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts, self.skip = [], False

        def handle_starttag(self, tag, attrs):
            self.skip = tag in ("style", "head")
            if tag in ("p", "br", "li"):
                self.parts.append("\n")

        def handle_endtag(self, tag):
            self.skip = False

        def handle_data(self, data):
            if not self.skip:
                self.parts.append(data)
    out = []
    for n in d.get("notes", []):
        t = Text()
        t.feed(n.get("html", ""))
        text = re.sub(r"\n\s*\n+", "\n", "".join(t.parts)).strip()
        if q in text.lower():
            out.append({"text": text, "color": n.get("color", "Yellow")})
    return out


def mail(q):
    """Server-side subject/sender search in each account's Inbox and Sent items."""
    roots = [Path("/usr/share/halcyon-mail"), Path(__file__).resolve().parents[2] / "mail"]
    for r in roots:
        if (r / "halcyon_mail").is_dir() and str(r) not in sys.path:
            sys.path.insert(0, str(r))
    try:
        from halcyon_mail import backend as B
    except ImportError:
        return []
    out = []
    for a in B.load_config().get("accounts", []):
        pw = B.get_password(a["id"])
        if not pw:
            continue
        try:
            i = B.Imap(a, pw)
            folders = [f for f in i.folders() if f["kind"] in ("inbox", "sent")]
            for f in folders:
                for m in i.search(f["name"], q):
                    out.append(dict(m, account=a["id"], folder=f["name"], folder_label=f["display"],
                                    email=a["email"]))
            i.close()
        except Exception:
            continue
    return out


# --------------------------------------------------------------- the window ---
class Bus(QObject):
    results = Signal(int, str, object)       # generation, source, results


class FilterBar(QWidget):
    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#f8fbfe"))
        g.setColorAt(1, QColor("#e3ecf7"))
        p.fillRect(self.rect(), g)
        p.setPen(QColor("#b7c9df"))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class Header(QWidget):
    """The glass address bar with the search box on the right, as in Vista's Explorer."""
    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#c9dcf3"))
        g.setColorAt(0.5, QColor("#b6cdec"))
        g.setColorAt(0.51, QColor("#a8c1e5"))
        g.setColorAt(1, QColor("#bed3ef"))
        p.fillRect(self.rect(), g)
        p.setPen(QColor("#8aa5c9"))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


QSS = f"""
QMainWindow {{ background: white; }}
QLineEdit#crumb, QLineEdit#box {{ border: 1px solid #7f9db9; background: white; padding: 3px 6px; color: black; }}
QLineEdit#box {{ font-style: normal; }}
QPushButton#filter {{ border: 1px solid transparent; border-radius: 3px; padding: 3px 10px; color: {TEXT}; background: transparent; }}
QPushButton#filter:hover {{ border-color: #a8c8ec; background: #eaf3fd; }}
QPushButton#filter:checked {{ border-color: #7da2ce; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 #e3f0fd, stop:1 #c4ddf7); font-weight: bold; }}
QTreeWidget {{ border: none; background: white; color: #1e1e1e; outline: 0; }}
QTreeWidget::item {{ padding: 2px 0; border: 1px solid transparent; }}
QTreeWidget::item:hover {{ background: #eef5fd; border-color: #d4e5f8; }}
QTreeWidget::item:selected {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e3f0fd, stop:1 #c9e0f9);
    border-color: #84acd8; color: black; }}
QHeaderView::section {{ background: white; border: none; border-right: 1px solid #e3e9f1; padding: 4px 8px;
    color: #4c607a; }}
QHeaderView::section:hover {{ background: #eef5fd; }}
QLabel#status {{ color: {TEXT}; padding: 4px 10px; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 #f1f5fa, stop:1 #dfe8f2); border-top: 1px solid #c5d6e8; }}
QMenu {{ background: #f7f9fc; border: 1px solid #97a8c0; padding: 2px; }}
QMenu::item {{ padding: 4px 24px 4px 26px; }}
QMenu::item:selected {{ background: #d9e9fb; border: 1px solid #a8c8ec; border-radius: 2px; color: black; }}
"""


class Search(QMainWindow):
    def __init__(self, text=""):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon.fromTheme("system-search", QIcon.fromTheme("edit-find")))
        self.resize(980, 640)
        self.apps = apps()
        self.files = FileIndex()
        self.icons = QFileIconProvider()
        self.mimes = QMimeDatabase()
        self.bus = Bus()
        self.bus.results.connect(self.arrived)
        self.gen = 0
        self.got = {}
        self.pending = set()
        self.filter = "all"

        w = QWidget()
        self.setCentralWidget(w)
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        head = Header()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(8, 6, 8, 6)
        crumb = QLineEdit("Search Results", objectName="crumb", readOnly=True)
        crumb.addAction(QIcon.fromTheme("system-search"), QLineEdit.LeadingPosition)
        hl.addWidget(crumb, 1)
        self.box = QLineEdit(text, objectName="box", placeholderText="Search")
        self.box.setFixedWidth(280)
        self.box.setClearButtonEnabled(True)
        self.box.addAction(QIcon.fromTheme("edit-find"), QLineEdit.TrailingPosition)
        self.box.textChanged.connect(lambda _: self.debounce.start())
        self.box.returnPressed.connect(self.open_first)
        hl.addWidget(self.box)
        col.addWidget(head)

        bar = FilterBar()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 4, 10, 4)
        bl.setSpacing(2)
        lbl = QLabel("Show only:")
        lbl.setStyleSheet(f"color: {GRAY};")
        bl.addWidget(lbl)
        self.group = QButtonGroup(self)
        for key, label in FILTERS:
            b = QPushButton(label, objectName="filter", checkable=True)
            b.setChecked(key == "all")
            b.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            self.group.addButton(b)
            bl.addWidget(b)
        bl.addStretch(1)
        col.addWidget(bar)

        self.tree = QTreeWidget(rootIsDecorated=False, uniformRowHeights=True, sortingEnabled=False)
        self.tree.setHeaderLabels(["Name", "Date modified", "Type", "Folder"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 140)
        self.tree.setColumnWidth(3, 260)
        self.tree.setIconSize(QSize(20, 20))
        self.tree.itemActivated.connect(self.activate)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.menu)
        col.addWidget(self.tree, 1)
        self.status = QLabel(objectName="status")
        col.addWidget(self.status)

        self.debounce = QTimer(self, singleShot=True, interval=250, timeout=self.run)
        for key, fn in (("Escape", self.close), ("Ctrl+E", self.box.setFocus), ("Ctrl+F", self.box.setFocus),
                        ("Down", lambda: self.tree.setFocus() if self.box.hasFocus() else None)):
            a = QAction(self, shortcut=QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
        self.box.setFocus()
        if text:
            self.run()
        else:
            self.status.setText("Type to search programs, files, e-mail, contacts, appointments and notes.")

    # --- searching
    def run(self):
        q = self.box.text().strip().lower()
        self.gen += 1
        self.got = {}
        self.tree.clear()
        if len(q) < 2:
            self.status.setText("Type to search programs, files, e-mail, contacts, appointments and notes."
                                if not q else "Keep typing…")
            return
        g = self.gen
        self.pending = {"apps", "files", "contacts", "calendar", "notes", "mail"}
        self.got["apps"] = sorted((a for a in self.apps if q in f"{a['name']} {a['generic']} {a['keywords']} "
                                                              f"{a['comment']}".lower()),
                                  key=lambda a: score(a["name"], q))[:12]
        self.pending.discard("apps")
        for name, fn in (("files", self.find_files), ("contacts", contacts), ("calendar", appointments),
                         ("notes", notes), ("mail", mail)):
            threading.Thread(target=self.work, args=(g, name, fn, q), daemon=True).start()
        self.status.setText("Searching…")
        self.render()

    def find_files(self, q):
        found = baloo(q)
        if found is None:
            for _ in range(100):                 # the first search may wait for the walk
                if self.files.ready:
                    break
                time.sleep(0.05)
            found = self.files.find(q)
        return found

    def work(self, g, name, fn, q):
        try:
            r = fn(q)
        except Exception:
            r = []
        self.bus.results.emit(g, name, r)

    def arrived(self, g, name, r):
        if g != self.gen:
            return
        self.got[name] = r
        self.pending.discard(name)
        self.render()

    def set_filter(self, key):
        self.filter = key
        self.render()

    # --- results
    def rows(self):
        f = self.filter
        out = []
        if f in ("all", "other"):
            for a in self.got.get("apps", []):
                out.append(("app", a["name"], "", "Program", a.get("comment", ""),
                            QIcon.fromTheme(a["icon"]) if a["icon"] and not a["icon"].startswith("/")
                            else QIcon(a["icon"]) if a["icon"] else QIcon.fromTheme("application-x-executable"), a))
        if f in ("all", "mail"):
            for m in self.got.get("mail", []):
                when = datetime.fromtimestamp(m["date"]).strftime("%-m/%-d/%Y %-I:%M %p") if m.get("date") else ""
                out.append(("mail", m["subject"], when, "E-mail message", f"{m['from']} · {m['folder_label']}",
                            QIcon.fromTheme("mail-message" if m["seen"] else "mail-unread"), m))
        for p in self.got.get("files", []):
            k = kind_of(p)
            if f not in ("all", k) and not (f == "other" and k == "other"):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            info = QFileInfo(p)
            kind = {"doc": "Document", "pic": "Picture", "music": "Music"}.get(k, "File")
            ext = Path(p).suffix.lstrip(".").upper()
            out.append(("file", os.path.basename(p), datetime.fromtimestamp(st.st_mtime).strftime("%-m/%-d/%Y %-I:%M %p"),
                        f"{ext} {kind}" if ext else kind, str(Path(p).parent).replace(str(HOME), "~"),
                        self.file_icon(p, info), p))
        if f in ("all", "other"):
            for c in self.got.get("contacts", []):
                out.append(("contact", c.get("name") or c.get("email", ""), "", "Contact", c.get("email", ""),
                            QIcon.fromTheme("x-office-contact", QIcon.fromTheme("user-identity")), c))
            for e in self.got.get("calendar", []):
                if e.get("task"):
                    out.append(("task", e["title"], "", "Task", "Done" if e.get("done") else "",
                                QIcon.fromTheme("view-task"), e))
                else:
                    s = datetime.fromisoformat(e["start"])
                    out.append(("event", e["title"], s.strftime("%-m/%-d/%Y" if e.get("allday") else "%-m/%-d/%Y %-I:%M %p"),
                                "Appointment", e.get("location", ""), QIcon.fromTheme("view-calendar-day",
                                                                                      QIcon.fromTheme("office-calendar")), e))
            for n in self.got.get("notes", []):
                first = n["text"].splitlines()[0][:80]
                out.append(("note", first, "", f"Sticky note ({n['color'].lower()})", "", QIcon.fromTheme("knotes"), n))
        return out

    def file_icon(self, path, info):
        mt = self.mimes.mimeTypeForFile(path)
        ic = QIcon.fromTheme(mt.iconName(), QIcon.fromTheme(mt.genericIconName()))
        return ic if not ic.isNull() else self.icons.icon(info)

    def render(self):
        q = self.box.text().strip()
        self.tree.clear()
        rows = self.rows()
        for kind, name, date_, typ, where, icon, data in rows:
            it = QTreeWidgetItem([name, date_, typ, where])
            it.setIcon(0, icon)
            it.setData(0, Qt.UserRole, (kind, data))
            it.setToolTip(0, data if isinstance(data, str) else name)
            self.tree.addTopLevelItem(it)
        busy = bool(self.pending)
        if rows:
            self.status.setText(f"{len(rows)} item{'s' if len(rows) != 1 else ''}" + ("   (still searching…)" if busy else ""))
        else:
            self.status.setText("Searching…" if busy else f"No items match your search for “{q}”.")

    def open_first(self):
        if self.tree.topLevelItemCount():
            self.activate(self.tree.topLevelItem(0))

    def activate(self, it, *_):
        kind, d = it.data(0, Qt.UserRole)
        if kind == "app":
            if shutil.which("kioclient"):
                spawn(["kioclient", "exec", d["file"]])
            elif shutil.which("gtk-launch"):
                spawn(["gtk-launch", d["id"]])
            else:
                exe = re.sub(r"%[a-zA-Z]", "", (re.search(r"^Exec=(.*)$", Path(d["file"]).read_text(), re.M) or [0, ""])[1])
                spawn(["sh", "-c", exe])
        elif kind == "file":
            spawn(["xdg-open", d])
        elif kind == "mail":
            spawn(halcyon("halcyon-mail", "--open", d["account"], d["folder"], str(d["uid"])))
        elif kind == "contact" and d.get("email"):
            spawn(halcyon("halcyon-mail", f"mailto:{d['email']}"))
        elif kind == "event":
            spawn(halcyon("halcyon-calendar", "--date", d["start"][:10]))
        elif kind == "task":
            spawn(halcyon("halcyon-calendar"))
        elif kind == "note":
            spawn(halcyon("halcyon-notes"))

    def menu(self, pos):
        it = self.tree.itemAt(pos)
        if not it:
            return
        kind, d = it.data(0, Qt.UserRole)
        m = QMenu(self)
        m.addAction("Open", lambda: self.activate(it))
        if kind == "file":
            m.addAction("Open file location", lambda: spawn(["dolphin", "--select", d] if shutil.which("dolphin")
                                                            else ["xdg-open", str(Path(d).parent)]))
            m.addAction("Copy path", lambda: QApplication.clipboard().setText(d))
        m.exec(self.tree.viewport().mapToGlobal(pos))


def light_palette():
    pal = QPalette()
    for role, color in ((QPalette.Window, "#f3f7fc"), (QPalette.WindowText, "#000000"), (QPalette.Base, "#ffffff"),
                        (QPalette.Text, "#000000"), (QPalette.Button, "#eef3f9"), (QPalette.ButtonText, "#000000"),
                        (QPalette.Highlight, "#3399ff"), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.PlaceholderText, "#7a8899"), (QPalette.ToolTipBase, "#ffffe1"),
                        (QPalette.ToolTipText, "#000000")):
        pal.setColor(role, QColor(color))
    return pal


def main():
    app = QApplication(sys.argv[:1])
    app.setApplicationName("halcyon-search")
    app.setDesktopFileName("halcyon-search")
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
    w = Search(" ".join(a for a in sys.argv[1:] if not a.startswith("-")))
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
