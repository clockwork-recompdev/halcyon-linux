"""Halcyon Mail: e-mail the way Windows Live Mail did it - folders on the
left, the message list, a reading pane, and a command bar across the top.
Works with any IMAP/SMTP account; pictures in messages stay blocked until
you ask for them."""
import html
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from email.utils import formataddr
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QObject, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QKeySequence,
                           QLinearGradient, QPainter, QPalette, QPen, QStandardItem, QStandardItemModel,
                           QTextCharFormat, QTextCursor, QTextDocument, QTextListFormat)
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
                                     QWebEngineUrlRequestInterceptor)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFontComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox,
                               QSplitter, QStackedWidget, QStatusBar, QStyle, QStyledItemDelegate, QTextEdit,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import backend as B
from . import contacts as C

APP = "Halcyon Mail"
TEXT = "#1e395b"
GRAY = "#6d7f95"
POLL_MS = 3 * 60 * 1000
FOLDER_ICONS = {"inbox": "mail-folder-inbox", "sent": "mail-folder-sent", "drafts": "document-edit",
                "trash": "user-trash", "junk": "mail-mark-junk", "outbox": "mail-folder-outbox",
                "archive": "folder-documents", "folder": "folder"}


# ------------------------------------------------------------- background ---
class Worker(QObject):
    """One per account: runs IMAP/SMTP jobs in order on its own thread, so the
    window never waits on the network. Callbacks come back on the UI thread."""
    done = Signal(object, object, object)

    def __init__(self, acct):
        super().__init__()
        self.acct = acct
        self.imap = None
        self.jobs = queue.Queue()
        self.done.connect(lambda cb, r, e: cb and cb(r, e))
        threading.Thread(target=self._loop, daemon=True).start()

    def password(self):
        return B.get_password(self.acct["id"])

    def run(self, fn, cb=None):
        self.jobs.put((fn, cb))

    def imap_job(self, fn, cb=None):
        def job():
            if self.imap is None:
                pw = self.password()
                if pw is None:
                    raise RuntimeError("No password saved for this account.")
                self.imap = B.Imap(self.acct, pw)
            return fn(self.imap)
        self.run(job, cb)

    def _loop(self):
        while True:
            fn, cb = self.jobs.get()
            try:
                r = fn()
                self.done.emit(cb, r, None)
            except Exception as e:      # noqa: BLE001 - shown to the user
                self.done.emit(cb, None, e)


def friendly(err):
    s = str(err)
    if "AUTHENTICATIONFAILED" in s or "Invalid credentials" in s or "authentication failed" in s.lower() \
            or "LOGIN failed" in s:
        return "The server didn't accept the user name or password."
    if isinstance(err, OSError) and not s:
        return "Couldn't reach the server."
    return s or err.__class__.__name__


def notify(title, body):
    try:
        subprocess.Popen(["notify-send", "--app-name", APP, "--icon", "mail-unread-new",
                          "--hint", "string:sound-name:message-new-email", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ------------------------------------------------------------ reading pane ---
class Blocker(QWebEngineUrlRequestInterceptor):
    """Pictures from the Internet stay off until asked for: they tell the sender you read it."""
    def __init__(self):
        super().__init__()
        self.allow = False

    def interceptRequest(self, info):
        if info.requestUrl().scheme() in ("http", "https", "ftp") and not self.allow:
            info.block(True)


class MailPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main):
        if kind == QWebEnginePage.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)          # links open in the web browser
            return False
        return url.scheme() in ("data", "file", "about") or not main

    def createWindow(self, kind):
        page = QWebEnginePage(self.profile(), self)
        page.urlChanged.connect(lambda u, p=page: (QDesktopServices.openUrl(u), p.deleteLater()))
        return page


class InfoBar(QFrame):
    """Windows Live Mail's yellow bar above a message with blocked pictures."""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet("InfoBar { background: #ffffe1; border-bottom: 1px solid #e5d98f; }"
                           "QLabel { color: #1e1e1e; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        ic = QLabel()
        ic.setPixmap(QIcon.fromTheme("dialog-information").pixmap(16))
        lay.addWidget(ic)
        self.label = QLabel("Some pictures have been blocked to help prevent the sender from identifying "
                            "your computer. <a href='show'>Show images</a>")
        self.label.setWordWrap(True)
        self.label.linkActivated.connect(lambda _: self.clicked.emit())
        lay.addWidget(self.label, 1)


class ReadingPane(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QWidget()
        self.head.setObjectName("readhead")
        self.head.setStyleSheet("#readhead { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff,"
                                " stop:1 #eaf1f9); border-bottom: 1px solid #c5d6e8; }")
        hl = QVBoxLayout(self.head)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(3)
        self.subject = QLabel(wordWrap=True, textInteractionFlags=Qt.TextSelectableByMouse)
        f = self.subject.font()
        f.setPointSizeF(13)
        self.subject.setFont(f)
        self.subject.setStyleSheet(f"color: {TEXT};")
        self.meta = QLabel(wordWrap=True, textInteractionFlags=Qt.TextSelectableByMouse)
        self.meta.setStyleSheet(f"color: {GRAY};")
        self.files = QLabel(wordWrap=True)
        self.files.linkActivated.connect(self.save_attachment)
        hl.addWidget(self.subject)
        hl.addWidget(self.meta)
        hl.addWidget(self.files)
        lay.addWidget(self.head)
        self.bar = InfoBar()
        self.bar.clicked.connect(self.show_pictures)
        lay.addWidget(self.bar)
        self.profile = QWebEngineProfile(self)               # off the record: no cookies kept
        self.blocker = Blocker()
        self.profile.setUrlRequestInterceptor(self.blocker)
        self.view = QWebEngineView()
        self.view.setPage(MailPage(self.profile, self.view))
        s = self.view.settings()
        s.setAttribute(QWebEngineSettings.JavascriptEnabled, False)
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.PluginsEnabled, False)
        lay.addWidget(self.view, 1)
        self.empty = QLabel("Select a message to read it here.", alignment=Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {GRAY}; background: white;")
        lay.addWidget(self.empty, 1)
        self.msg = None
        self.trusted = set()
        self.tmp = None
        self.clear()

    def clear(self, text="Select a message to read it here."):
        self.msg = None
        self.head.hide()
        self.bar.hide()
        self.view.hide()
        self.empty.setText(text)
        self.empty.show()

    def show_message(self, msg, trusted_senders=()):
        self.msg = msg
        self.empty.hide()
        self.head.show()
        self.view.show()
        self.subject.setText(html.escape(msg["subject"]))
        name, addr = msg["from"]
        who = f"<b style='color:{TEXT}'>{html.escape(name or addr)}</b>" + \
            (f" &lt;{html.escape(addr)}&gt;" if name and addr else "")
        to = ", ".join(html.escape(n or a) for n, a in msg["to"])
        cc = ", ".join(html.escape(n or a) for n, a in msg["cc"])
        lines = [f"{who}"]
        if msg.get("timestamp"):
            lines.append(B.long_date(msg["timestamp"]))
        lines.append(f"To: {to or '(undisclosed recipients)'}" + (f"&nbsp;&nbsp; Cc: {cc}" if cc else ""))
        self.meta.setText("<br>".join(lines))
        if msg["attachments"]:
            links = "&nbsp;&nbsp; ".join(f"📎 <a href='{i}'>{html.escape(a['name'])}</a> "
                                         f"<span style='color:{GRAY}'>({B.human_size(len(a['data']))})</span>"
                                         for i, a in enumerate(msg["attachments"]))
            self.files.setText(links)
            self.files.show()
        else:
            self.files.hide()
        allow = addr.lower() in trusted_senders
        self.render(allow)

    def render(self, allow):
        msg = self.msg
        self.blocker.allow = allow
        self.bar.setVisible(bool(msg["remote"]) and not allow)
        body = msg["html"] or B.text_to_html(msg["text"])
        if msg["html"] and "<base" not in body.lower():
            body = "<meta charset='utf-8'>" + body
        style = ("<style>body{font-family:'Segoe UI','Noto Sans',sans-serif;font-size:10pt;margin:14px;"
                 "color:#000;background:#fff;word-wrap:break-word}</style>")
        doc = style + body
        data = doc.encode("utf-8")
        if len(data) < 1_800_000:
            self.view.setContent(data, "text/html;charset=UTF-8", QUrl("about:blank"))
        else:                                  # big messages (inline pictures) go through a file
            if self.tmp:
                Path(self.tmp).unlink(missing_ok=True)
            fd, self.tmp = tempfile.mkstemp(suffix=".html", prefix="halcyon-mail-")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            self.view.load(QUrl.fromLocalFile(self.tmp))

    def show_pictures(self):
        if self.msg:
            self.render(True)

    def save_attachment(self, idx):
        a = self.msg["attachments"][int(idx)]
        path, _ = QFileDialog.getSaveFileName(self, "Save Attachment", str(Path.home() / "Downloads" / a["name"]))
        if path:
            Path(path).write_bytes(a["data"])


# -------------------------------------------------------------- message list ---
ROLE = Qt.UserRole + 1


class MessageDelegate(QStyledItemDelegate):
    """Two-line rows, as in Windows Live Mail: sender and date, then the subject."""
    def sizeHint(self, option, index):
        return QSize(200, 42)

    def paint(self, p, option, index):
        m = index.data(ROLE)
        if not m:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect
        sel = option.state & QStyle.State_Selected
        if sel:
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0, QColor("#dcebfc"))
            g.setColorAt(1, QColor("#c1dbfc"))
            p.fillRect(r, g)
            p.setPen(QColor("#7da2ce"))
            p.drawRect(r.adjusted(0, 0, -1, -1))
        elif option.state & QStyle.State_MouseOver:
            p.fillRect(r, QColor("#eef5fd"))
        p.setPen(QColor("#e3e9f1"))
        p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
        unread = not m["seen"]
        if unread:
            p.fillRect(QRect(r.left(), r.top() + 3, 3, r.height() - 6), QColor("#3d8ee0"))
        f = QFont(option.font)
        f.setBold(unread)
        p.setFont(f)
        x = r.left() + 22
        right = r.right() - 8
        date = B.short_date(m["date"])
        fm = p.fontMetrics()
        dw = fm.horizontalAdvance(date)
        p.setPen(QColor("#1e1e1e") if not unread else QColor(TEXT))
        p.drawText(QRect(x, r.top() + 4, right - x - dw - 8, 17), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(m["from"], Qt.ElideRight, right - x - dw - 8))
        f2 = QFont(option.font)
        f2.setBold(False)
        p.setFont(f2)
        p.setPen(QColor(GRAY))
        p.drawText(QRect(right - dw, r.top() + 4, dw, 17), Qt.AlignRight | Qt.AlignVCenter, date)
        p.setFont(f if unread else f2)
        p.setPen(QColor("#2b4a73") if unread else QColor("#444"))
        sw = right - x - 20
        p.drawText(QRect(x, r.top() + 21, sw, 17), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(m["subject"], Qt.ElideRight, sw))
        # icons: answered / attachment / flag
        if m.get("answered"):
            QIcon.fromTheme("mail-replied").paint(p, QRect(r.left() + 5, r.top() + 5, 14, 14))
        if m.get("attach"):
            QIcon.fromTheme("mail-attachment").paint(p, QRect(r.left() + 5, r.top() + 22, 14, 14))
        if m.get("flagged"):
            QIcon.fromTheme("flag-red", QIcon.fromTheme("mail-flag")).paint(p, QRect(right - 14, r.top() + 22, 14, 14))
        p.restore()


# ------------------------------------------------------------------- chrome ---
class CommandBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(38)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(6, 3, 6, 3)
        self.lay.setSpacing(2)

    def add(self, text, icon, slot=None, menu=None, tip=None):
        b = QToolButton(text=text, icon=QIcon.fromTheme(icon), toolButtonStyle=Qt.ToolButtonTextBesideIcon,
                        autoRaise=True, toolTip=tip or text)
        b.setIconSize(QSize(20, 20))
        if menu:
            b.setMenu(menu)
            b.setPopupMode(QToolButton.MenuButtonPopup if slot else QToolButton.InstantPopup)
        if slot:
            b.clicked.connect(slot)
        self.lay.addWidget(b)
        return b

    def sep(self):
        line = QFrame(frameShape=QFrame.VLine)
        line.setStyleSheet("color: #b7c9df;")
        self.lay.addSpacing(4)
        self.lay.addWidget(line)
        self.lay.addSpacing(4)

    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#fbfdff"))
        g.setColorAt(0.5, QColor("#e9f1fb"))
        g.setColorAt(0.51, QColor("#dbe8f7"))
        g.setColorAt(1, QColor("#e9f1fb"))
        p.fillRect(self.rect(), g)
        p.setPen(QColor("#a9bfd8"))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


QSS = f"""
QMainWindow, QDialog {{ background: #f3f7fc; }}
QToolButton {{ border: 1px solid transparent; border-radius: 3px; padding: 3px 6px; color: {TEXT}; }}
QToolButton:hover {{ border-color: #a8c8ec; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 #fdfeff, stop:0.5 #ebf4fd, stop:0.51 #d9eafb, stop:1 #e8f2fc); }}
QToolButton:pressed {{ background: #c8dff7; border-color: #7da2ce; }}
QToolButton:disabled {{ color: #9aa9bb; }}
QToolButton[popupMode="1"] {{ padding-right: 16px; }}
QToolButton::menu-button {{ border: none; border-left: 1px solid transparent; width: 14px; background: transparent; }}
QToolButton::menu-button:hover {{ border-left-color: #a8c8ec; }}
QTreeWidget#folders {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f6f9fd, stop:1 #e9f0f8);
    border: none; border-right: 1px solid #c5d6e8; color: {TEXT}; outline: 0; }}
QTreeWidget#folders::item {{ padding: 3px 2px; border: 1px solid transparent; }}
QTreeWidget#folders::item:hover {{ background: #e5effa; border-color: #c8dcf2; border-radius: 2px; }}
QTreeWidget#folders::item:selected {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dcebfc,
    stop:1 #c1dbfc); border-color: #7da2ce; border-radius: 2px; color: black; }}
QListView#messages {{ background: white; border: none; border-right: 1px solid #c5d6e8; outline: 0; }}
QLineEdit {{ border: 1px solid #a7bdd6; border-radius: 2px; padding: 3px 5px; background: white;
    selection-background-color: #3399ff; }}
QLineEdit#find {{ border-radius: 10px; padding-left: 8px; }}
QStatusBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f1f5fa, stop:1 #dfe8f2);
    color: {TEXT}; border-top: 1px solid #c5d6e8; }}
QSplitter::handle {{ background: #c5d6e8; }}
QMenu {{ background: #f7f9fc; border: 1px solid #97a8c0; padding: 2px; }}
QMenu::item {{ padding: 4px 24px 4px 26px; }}
QMenu::item:selected {{ background: #d9e9fb; border: 1px solid #a8c8ec; border-radius: 2px; color: black; }}
QPushButton {{ border: 1px solid #8ea6c4; border-radius: 3px; padding: 4px 14px; color: #1e1e1e;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:0.5 #eef3f9, stop:0.51 #dfe8f3,
    stop:1 #e9f0f8); }}
QPushButton:hover {{ border-color: #3c7fb1; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 #f4faff, stop:0.5 #dcefff, stop:0.51 #c6e3fc, stop:1 #d9ecfc); }}
QPushButton:default {{ border-color: #3c7fb1; }}
"""


# ---------------------------------------------------------------- accounts ---
class AccountDialog(QDialog):
    """'Add your e-mail account' - address and password; servers are found automatically."""
    def __init__(self, parent, acct=None):
        super().__init__(parent)
        self.setWindowTitle("Add an E-mail Account" if not acct else "Account Properties")
        self.setMinimumWidth(480)
        self.acct = dict(acct or {})
        lay = QVBoxLayout(self)
        title = QLabel("Add your e-mail account" if not acct else acct["email"])
        f = title.font()
        f.setPointSizeF(13)
        title.setFont(f)
        title.setStyleSheet(f"color: {TEXT};")
        lay.addWidget(title)
        lay.addWidget(QLabel("Works with Gmail, Outlook.com, Yahoo!, iCloud and any IMAP e-mail.",
                             styleSheet=f"color: {GRAY};"))
        form = QFormLayout()
        self.email = QLineEdit(self.acct.get("email", ""), placeholderText="someone@example.com")
        self.pw = QLineEdit(echoMode=QLineEdit.Password, placeholderText="Password" if not acct
                            else "(unchanged)")
        self.remember = QCheckBox("Remember this password", checked=True)
        self.name = QLineEdit(self.acct.get("name", ""), placeholderText="Shown to people you send mail to")
        form.addRow("E-mail address:", self.email)
        form.addRow("Password:", self.pw)
        form.addRow("", self.remember)
        form.addRow("Display name:", self.name)
        lay.addLayout(form)
        self.hint = QLabel(wordWrap=True, openExternalLinks=True)
        self.hint.setStyleSheet(f"color: {GRAY};")
        lay.addWidget(self.hint)
        self.manual = QCheckBox("Manually configure server settings")
        lay.addWidget(self.manual)
        self.servers = QWidget()
        sf = QFormLayout(self.servers)
        sf.setContentsMargins(18, 0, 0, 0)
        self.fields = {}
        for kind, label in (("imap", "Incoming (IMAP)"), ("smtp", "Outgoing (SMTP)")):
            row = QHBoxLayout()
            host = QLineEdit(self.acct.get(kind, {}).get("host", ""))
            port = QSpinBox(maximum=65535, value=int(self.acct.get(kind, {}).get("port", 993 if kind == "imap" else 587)))
            sec = QComboBox()
            sec.addItems(["SSL/TLS", "STARTTLS", "None"])
            sec.setCurrentIndex({"ssl": 0, "starttls": 1, "none": 2}[self.acct.get(kind, {}).get("security", "ssl")])
            user = QLineEdit(self.acct.get(kind, {}).get("user", ""), placeholderText="(e-mail address)")
            row.addWidget(host, 1)
            row.addWidget(port)
            row.addWidget(sec)
            sf.addRow(label + ":", row)
            sf.addRow("   Log on as:", user)
            self.fields[kind] = (host, port, sec, user)
        self.servers.setVisible(bool(acct))
        self.manual.setChecked(bool(acct))
        self.manual.toggled.connect(self.servers.setVisible)
        lay.addWidget(self.servers)
        self.status = QLabel(wordWrap=True)
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Next" if not acct else "OK")
        bb.accepted.connect(self.go)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.bb = bb
        self.email.editingFinished.connect(self.update_hint)
        self.bus = Worker({"id": "setup"})

    def update_hint(self):
        page = B.app_password_page(self.email.text())
        self.hint.setText(f"This service needs an <b>app password</b> instead of your usual one - "
                          f"<a href='{page}'>create one here</a> and paste it above." if page else "")

    def go(self):
        addr = self.email.text().strip()
        if "@" not in addr:
            self.status.setText("<span style='color:#c6282d'>Type your full e-mail address.</span>")
            return
        pw = self.pw.text() or (B.get_password(self.acct["id"]) if self.acct.get("id") else "")
        if not pw:
            self.status.setText("<span style='color:#c6282d'>Type your password.</span>")
            return
        self.bb.setEnabled(False)
        self.status.setText("Looking up your e-mail servers…" if not self.manual.isChecked() else "Signing in…")
        manual = self.manual.isChecked()
        current = {k: {"host": h.text().strip(), "port": p.value(), "security": ["ssl", "starttls", "none"][s.currentIndex()],
                       "user": u.text().strip()} for k, (h, p, s, u) in self.fields.items()}

        def work():
            found = current if manual else B.autoconfig(addr)
            acct = dict(self.acct, email=addr, name=self.name.text().strip(),
                        imap=dict(found["imap"]), smtp=dict(found["smtp"]))
            acct.setdefault("id", uuid.uuid4().hex[:12])
            B.test_login(acct, pw)
            return acct
        self.bus.run(work, lambda acct, err: self.finished_test(acct, err, pw))

    def finished_test(self, acct, err, pw):
        self.bb.setEnabled(True)
        if err:
            self.status.setText(f"<span style='color:#c6282d'>{html.escape(friendly(err))}</span>")
            self.manual.setChecked(True)
            if not self.fields["imap"][0].text():
                guess = B.autoconfig(self.email.text().strip())
                for kind, (h, p, s, u) in self.fields.items():
                    h.setText(guess[kind]["host"])
                    p.setValue(guess[kind]["port"])
                    s.setCurrentIndex({"ssl": 0, "starttls": 1, "none": 2}[guess[kind]["security"]])
            return
        self.acct = acct
        B.set_password(acct["id"], acct["email"], pw) if self.remember.isChecked() else None
        self.password = pw
        self.accept()


# ------------------------------------------------------------------ compose ---
class Compose(QMainWindow):
    """A new message window, with the Live Mail formatting bar."""
    def __init__(self, main, acct, to="", cc="", subject="", quoted_html="", reply_to=None):
        super().__init__(main)
        self.main, self.acct, self.reply_to = main, acct, reply_to
        self.setWindowTitle(subject or "New Message")
        self.setWindowIcon(QIcon.fromTheme("mail-message-new"))
        self.resize(760, 620)
        self.attachments = []
        self.sent = False
        w = QWidget()
        self.setCentralWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = CommandBar()
        self.send_btn = bar.add("Send", "mail-send", self.send)
        bar.sep()
        bar.add("Attach", "mail-attachment", self.attach)
        bar.add("Check names", "contact-new", self.check_names)
        bar.lay.addStretch(1)
        lay.addWidget(bar)
        form = QFormLayout()
        form.setContentsMargins(12, 8, 12, 6)
        self.from_box = QComboBox()
        for a in main.cfg["accounts"]:
            self.from_box.addItem(formataddr((a.get("name") or "", a["email"])), a["id"])
        self.from_box.setCurrentIndex(max(0, self.from_box.findData(acct["id"])))
        self.to = C.RecipientEdit(main.book, to)
        self.cc = C.RecipientEdit(main.book, cc)
        self.bcc = C.RecipientEdit(main.book)
        self.subject = QLineEdit(subject)
        self.subject.textChanged.connect(lambda t: self.setWindowTitle(t or "New Message"))
        if len(main.cfg["accounts"]) > 1:
            form.addRow("From:", self.from_box)
        # Live Mail's To / Cc / Bcc buttons open the contact picker.
        for label, box in (("To:", self.to), ("Cc:", self.cc), ("Bcc:", self.bcc)):
            b = QPushButton(label)
            b.setFixedWidth(52)
            b.setToolTip("Choose from your contacts")
            b.clicked.connect(self.pick)
            form.addRow(b, box)
        form.addRow("Subject:", self.subject)
        lay.addLayout(form)
        self.files = QLabel(wordWrap=True)
        self.files.setContentsMargins(12, 0, 12, 4)
        self.files.linkActivated.connect(self.remove_attachment)
        self.files.hide()
        lay.addWidget(self.files)
        fmt = QWidget()
        fmt.setStyleSheet("background: #eef3fa; border-top: 1px solid #c5d6e8; border-bottom: 1px solid #c5d6e8;")
        fl = QHBoxLayout(fmt)
        fl.setContentsMargins(8, 2, 8, 2)
        self.font_box = QFontComboBox()
        self.font_box.setMaximumWidth(180)
        self.size_box = QComboBox()
        self.size_box.addItems(["8", "9", "10", "11", "12", "14", "16", "18", "24", "36"])
        self.size_box.setCurrentText("10")
        fl.addWidget(self.font_box)
        fl.addWidget(self.size_box)
        self.edit = QTextEdit()
        self.edit.setAcceptRichText(True)
        df = QFont("Segoe UI" if "Segoe UI" in QFontDatabase.families() else "Noto Sans", 10)
        self.edit.document().setDefaultFont(df)
        self.font_box.setCurrentFont(df)
        self.font_box.currentFontChanged.connect(lambda f: self.merge(family=f.family()))
        self.size_box.currentTextChanged.connect(lambda s: self.merge(size=float(s)))
        for icon, tip, fn in (("format-text-bold", "Bold (Ctrl+B)", lambda: self.merge(bold=True)),
                              ("format-text-italic", "Italic (Ctrl+I)", lambda: self.merge(italic=True)),
                              ("format-text-underline", "Underline (Ctrl+U)", lambda: self.merge(underline=True)),
                              ("format-text-color", "Font color", self.pick_color),
                              ("format-list-unordered", "Bullets", self.bullets),
                              ("format-justify-left", "Align left", lambda: self.edit.setAlignment(Qt.AlignLeft)),
                              ("format-justify-center", "Center", lambda: self.edit.setAlignment(Qt.AlignHCenter)),
                              ("format-justify-right", "Align right", lambda: self.edit.setAlignment(Qt.AlignRight)),
                              ("insert-link", "Hyperlink", self.link)):
            b = QToolButton(icon=QIcon.fromTheme(icon), toolTip=tip, autoRaise=True)
            b.clicked.connect(fn)
            fl.addWidget(b)
        fl.addStretch(1)
        lay.addWidget(fmt)
        lay.addWidget(self.edit, 1)
        for key, fn in (("Ctrl+B", lambda: self.merge(bold=True)), ("Ctrl+I", lambda: self.merge(italic=True)),
                        ("Ctrl+U", lambda: self.merge(underline=True)), ("Ctrl+Return", self.send),
                        ("Alt+S", self.send)):
            a = QAction(self, shortcut=QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
        if quoted_html:
            self.edit.setHtml("<p><br></p>" + quoted_html)
            cur = self.edit.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.Start)
            self.edit.setTextCursor(cur)
        self.edit.document().setModified(False)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        (self.edit if to else self.to).setFocus()

    def merge(self, family=None, size=None, bold=None, italic=None, underline=None, color=None):
        f = QTextCharFormat()
        cur = self.edit.currentCharFormat()
        if family:
            f.setFontFamilies([family])
        if size:
            f.setFontPointSize(size)
        if bold:
            f.setFontWeight(QFont.Normal if cur.fontWeight() > QFont.Normal else QFont.Bold)
        if italic:
            f.setFontItalic(not cur.fontItalic())
        if underline:
            f.setFontUnderline(not cur.fontUnderline())
        if color:
            f.setForeground(color)
        self.edit.mergeCurrentCharFormat(f)
        self.edit.setFocus()

    def pick_color(self):
        c = QColorDialog.getColor(Qt.black, self, "Font Color")
        if c.isValid():
            self.merge(color=c)

    def bullets(self):
        self.edit.textCursor().createList(QTextListFormat.ListDisc)

    def link(self):
        from PySide6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(self, "Hyperlink", "URL:", text="http://")
        if ok and url.strip():
            cur = self.edit.textCursor()
            label = cur.selectedText() or url
            cur.insertHtml(f'<a href="{html.escape(url)}">{html.escape(label)}</a>')

    def attach(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach Files", str(Path.home()))
        self.attachments += [p for p in paths if p not in self.attachments]
        self.show_files()

    def show_files(self):
        if not self.attachments:
            self.files.hide()
            return
        self.files.setText("Attach: " + "&nbsp;&nbsp; ".join(
            f"📎 {html.escape(Path(p).name)} ({B.human_size(Path(p).stat().st_size)}) <a href='{i}'>remove</a>"
            for i, p in enumerate(self.attachments)))
        self.files.show()

    def remove_attachment(self, i):
        del self.attachments[int(i)]
        self.show_files()

    def check_names(self):
        bad = [w.objectName() for w in (self.to, self.cc, self.bcc) if w.text().strip() and not B.recipients_ok(w.text())]
        self.status.showMessage("All names look right." if not bad else "Check the addresses - one doesn't look right.",
                                5000)

    def account(self):
        aid = self.from_box.currentData()
        return next((a for a in self.main.cfg["accounts"] if a["id"] == aid), self.acct)

    def message(self):
        doc_html = self.edit.toHtml()
        return B.build(self.account(), self.to.text().strip(), self.cc.text().strip(), self.bcc.text().strip(),
                       self.subject.text().strip(), doc_html, self.edit.toPlainText(), self.attachments,
                       reply_to_msg=self.reply_to)

    def send(self):
        if not B.recipients_ok(self.to.text(), self.cc.text(), self.bcc.text()):
            QMessageBox.warning(self, APP, "Type at least one e-mail address in To, Cc or Bcc, separated by commas.")
            return
        if not self.subject.text().strip() and QMessageBox.question(
                self, APP, "This message has no subject. Send it anyway?") != QMessageBox.Yes:
            return
        try:
            msg = self.message()
        except OSError as e:
            QMessageBox.warning(self, APP, f"Couldn't attach a file: {e}")
            return
        self.send_btn.setEnabled(False)
        self.status.showMessage("Sending…")
        acct = self.account()
        self.main.send(acct, msg, self.sent_result, self.reply_to)

    def pick(self):
        d = C.Picker(self, self.main.book)
        if d.exec():
            for key, box in (("to", self.to), ("cc", self.cc), ("bcc", self.bcc)):
                if d.picked[key]:
                    box.add(d.picked[key])

    def sent_result(self, ok, err):
        self.send_btn.setEnabled(True)
        if err:
            self.status.showMessage("")
            QMessageBox.warning(self, APP, f"The message couldn't be sent.\n\n{friendly(err)}")
            return
        self.sent = True
        # People you write to join Contacts by themselves.
        self.main.book.collect(", ".join(x for x in (self.to.text(), self.cc.text(), self.bcc.text()) if x))
        self.close()

    def closeEvent(self, e):
        if self.sent or not (self.edit.document().isModified() or self.to.text() or self.attachments):
            return e.accept()
        r = QMessageBox.question(self, APP, "Do you want to save changes to this message in Drafts?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return e.ignore()
        if r == QMessageBox.Save:
            try:
                self.main.save_draft(self.account(), self.message())
            except OSError:
                pass
        e.accept()


# --------------------------------------------------------------- main window ---
class Mail(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon.fromTheme("internet-mail", QIcon.fromTheme("mail-message")))
        self.resize(1180, 760)
        self.cfg = B.load_config()
        self.book = C.Book()
        self.contacts_win = None
        self.workers = {}
        self.folders = {}          # acct id -> [folder dicts]
        self.messages = []         # current list
        self.current = None        # (acct id, folder name)
        self.last_uid = {}         # (acct id, folder) -> highest uid seen, for notifications
        self.cache = {}            # (acct, folder) -> messages
        self.opening = None
        self.pending_open = None      # (account id, folder, uid) from `halcyon-mail --open`

        w = QWidget()
        self.setCentralWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = CommandBar()
        new_menu = QMenu(self)
        new_menu.addAction(QIcon.fromTheme("mail-message-new"), "E-mail message", self.new_message,
                           QKeySequence("Ctrl+N"))
        new_menu.addAction(QIcon.fromTheme("folder-new"), "Folder…", self.new_folder)
        bar.add("New", "mail-message-new", self.new_message, menu=new_menu, tip="New e-mail message (Ctrl+N)")
        self.reply_btn = bar.add("Reply", "mail-reply-sender", self.reply, tip="Reply (Ctrl+R)")
        self.replyall_btn = bar.add("Reply all", "mail-reply-all", lambda: self.reply(all_=True),
                                    tip="Reply all (Ctrl+Shift+R)")
        self.forward_btn = bar.add("Forward", "mail-forward", self.forward, tip="Forward (Ctrl+F)")
        bar.sep()
        self.delete_btn = bar.add("Delete", "edit-delete", self.delete, tip="Delete (Del)")
        self.junk_btn = bar.add("Junk", "mail-mark-junk", self.junk, tip="Move to Junk e-mail")
        bar.sep()
        self.sync_btn = bar.add("Sync", "mail-send-receive", self.sync, tip="Send/receive (F5)")
        bar.sep()
        bar.add("Contacts", "x-office-address-book", self.show_contacts, tip="Contacts (Ctrl+Shift+C)")
        bar.lay.addStretch(1)
        menu = QMenu(self)
        menu.addAction("Add an e-mail account…", self.add_account)
        self.props_menu = menu.addMenu("Account properties")
        self.remove_menu = menu.addMenu("Remove account")
        menu.addSeparator()
        menu.addAction("Mark as read", lambda: self.mark(True), QKeySequence("Ctrl+Q"))
        menu.addAction("Mark as unread", lambda: self.mark(False), QKeySequence("Ctrl+U"))
        menu.addAction("Flag", self.flag, QKeySequence("Insert"))
        menu.addSeparator()
        menu.addAction("Reading pane on the right", lambda: self.splitter.setOrientation(Qt.Horizontal))
        self.find = QLineEdit(placeholderText="Find a message", objectName="find")
        self.find.setFixedWidth(220)
        self.find.addAction(QIcon.fromTheme("edit-find"), QLineEdit.TrailingPosition)
        self.find.textChanged.connect(self.fill_list)
        bar.lay.addWidget(self.find)
        bar.add("", "application-menu", menu=menu, tip="Menu")
        lay.addWidget(bar)

        self.splitter = QSplitter(Qt.Horizontal, handleWidth=1, childrenCollapsible=False)
        self.tree = QTreeWidget(objectName="folders", headerHidden=True, indentation=14, rootIsDecorated=False)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.itemClicked.connect(self.folder_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.folder_menu)
        self.model = QStandardItemModel(self)
        self.list = QListView(objectName="messages")
        self.list.setModel(self.model)
        self.list.setItemDelegate(MessageDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QListView.ExtendedSelection)
        self.list.setUniformItemSizes(True)
        self.list.selectionModel().currentChanged.connect(self.message_selected)
        self.list.doubleClicked.connect(lambda _: self.open_window())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.message_menu)
        self.reader = ReadingPane()
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.list)
        self.splitter.addWidget(self.reader)
        self.splitter.setSizes([220, 380, 580])
        self.splitter.setStretchFactor(2, 1)
        lay.addWidget(self.splitter, 1)

        self.status = QLabel()
        self.online = QLabel("Working online")
        sb = QStatusBar()
        sb.addWidget(self.status, 1)
        sb.addPermanentWidget(self.online)
        self.setStatusBar(sb)

        for key, fn in (("Ctrl+R", self.reply), ("Ctrl+Shift+R", lambda: self.reply(all_=True)),
                        ("Ctrl+F", self.forward), ("Delete", self.delete), ("F5", self.sync),
                        ("Ctrl+E", self.find.setFocus), ("Ctrl+M", self.sync),
                        ("Ctrl+Shift+C", self.show_contacts)):
            a = QAction(self, shortcut=QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
        self.poll = QTimer(self, interval=POLL_MS, timeout=self.check_new)
        self.poll.start()
        self.refresh_accounts()
        self.update_buttons()
        if not self.cfg["accounts"]:
            QTimer.singleShot(300, self.add_account)

    # --- accounts and folders
    def acct(self, aid):
        return next((a for a in self.cfg["accounts"] if a["id"] == aid), None)

    def worker(self, aid):
        if aid not in self.workers:
            self.workers[aid] = Worker(self.acct(aid))
        return self.workers[aid]

    def refresh_accounts(self):
        self.tree.clear()
        self.props_menu.clear()
        self.remove_menu.clear()
        for a in self.cfg["accounts"]:
            self.props_menu.addAction(a["email"], lambda a=a: self.edit_account(a))
            self.remove_menu.addAction(a["email"], lambda a=a: self.remove_account(a))
            top = QTreeWidgetItem([a.get("label") or a["email"]])
            f = top.font(0)
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() + 1)
            top.setFont(0, f)
            top.setForeground(0, QColor(TEXT))
            top.setIcon(0, QIcon.fromTheme("internet-mail"))
            top.setData(0, ROLE, (a["id"], None))
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
            self.load_folders(a["id"])
        if not self.cfg["accounts"]:
            self.reader.clear("Add an e-mail account to get started: Menu → Add an e-mail account…")

    def load_folders(self, aid):
        self.status.setText("Connecting…")
        self.worker(aid).imap_job(lambda i: [(f, i.unseen(f["name"])) for f in i.folders()],
                                  lambda r, e, aid=aid: self.folders_loaded(aid, r, e))

    def folders_loaded(self, aid, result, err):
        top = self.top_item(aid)
        if top is None:
            return
        if err:
            self.status.setText(f"{self.acct(aid)['email']}: {friendly(err)}")
            self.online.setText("Working offline")
            return
        self.online.setText("Working online")
        self.folders[aid] = [f for f, _ in result]
        top.takeChildren()
        for f, unseen in result:
            it = QTreeWidgetItem()
            it.setData(0, ROLE, (aid, f["name"]))
            it.setIcon(0, QIcon.fromTheme(FOLDER_ICONS.get(f["kind"], "folder")))
            self.set_count(it, f, unseen)
            top.addChild(it)
        top.setExpanded(True)
        if self.pending_open and self.pending_open[0] == aid:
            it = self.folder_item(aid, self.pending_open[1])
            if it is not None:
                self.tree.setCurrentItem(it)
                self.folder_clicked(it)
                self.status.setText("")
                return
        if self.current is None:
            inbox = next((top.child(i) for i in range(top.childCount())
                          if top.child(i).data(0, ROLE)[1].upper() == "INBOX"), None)
            if inbox:
                self.tree.setCurrentItem(inbox)
                self.folder_clicked(inbox)
        self.status.setText("")

    def set_count(self, it, f, unseen):
        label = f["display"] if f["kind"] != "folder" else f["path"]
        it.setText(0, f"{label} ({unseen})" if unseen and f["kind"] not in ("sent", "trash") else label)
        font = it.font(0)
        font.setBold(bool(unseen) and f["kind"] not in ("sent", "trash"))
        it.setFont(0, font)
        it.setForeground(0, QColor("#1e5aa8") if unseen and f["kind"] not in ("sent", "trash") else QColor(TEXT))

    def top_item(self, aid):
        for i in range(self.tree.topLevelItemCount()):
            t = self.tree.topLevelItem(i)
            if t.data(0, ROLE)[0] == aid:
                return t
        return None

    def folder_item(self, aid, name):
        top = self.top_item(aid)
        for i in range(top.childCount() if top else 0):
            if top.child(i).data(0, ROLE)[1] == name:
                return top.child(i)
        return None

    def folder(self, aid, name):
        return next((f for f in self.folders.get(aid, []) if f["name"] == name), None)

    def special(self, aid, kind):
        return next((f["name"] for f in self.folders.get(aid, []) if f["kind"] == kind), None)

    def folder_clicked(self, item, *_):
        aid, name = item.data(0, ROLE)
        if not name:
            return
        self.current = (aid, name)
        self.reader.clear()
        self.messages = self.cache.get(self.current, [])
        self.fill_list()
        f = self.folder(aid, name)
        self.setWindowTitle(f"{f['display'] if f else name} - {APP}")
        self.load_messages(aid, name)

    def load_messages(self, aid, name, quiet=False):
        if not quiet:
            self.status.setText("Getting messages…")
        self.worker(aid).imap_job(lambda i: (i.headers(name), i.unseen(name)),
                                  lambda r, e: self.messages_loaded(aid, name, r, e))

    def messages_loaded(self, aid, name, result, err):
        if err:
            self.status.setText(friendly(err))
            return
        msgs, unseen = result
        self.cache[(aid, name)] = msgs
        if msgs:
            self.last_uid[(aid, name)] = max(self.last_uid.get((aid, name), 0), max(m["uid"] for m in msgs))
        it = self.folder_item(aid, name)
        if it is not None:
            self.set_count(it, self.folder(aid, name), unseen)
        if self.current == (aid, name):
            keep = self.selected_uids()
            self.messages = msgs
            want = self.pending_open
            if want and want[:2] == (aid, name):
                self.pending_open = None
                keep = {want[2]}
            self.fill_list(keep)
            self.update_status()
            if want and want[:2] == (aid, name):
                idx = next((self.model.index(r, 0) for r in range(self.model.rowCount())
                            if self.model.item(r).data(ROLE)["uid"] == want[2]), None)
                if idx is not None:
                    self.list.setCurrentIndex(idx)
                    self.list.scrollTo(idx)

    def fill_list(self, keep=()):
        q = self.find.text().strip().lower()
        self.model.clear()
        for m in self.messages:
            if q and q not in (m["from"] + " " + m["subject"] + " " + m["from_addr"]).lower():
                continue
            it = QStandardItem()
            it.setData(m, ROLE)
            it.setEditable(False)
            self.model.appendRow(it)
        if keep:
            sm = self.list.selectionModel()
            for row in range(self.model.rowCount()):
                if self.model.item(row).data(ROLE)["uid"] in keep:
                    idx = self.model.index(row, 0)
                    sm.select(idx, QItemSelectionModel.SelectionFlag.Select)
                    sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.update_status()
        self.update_buttons()

    def update_status(self):
        if not self.current:
            return
        n = len(self.messages)
        unread = sum(1 for m in self.messages if not m["seen"])
        self.status.setText(f"{n} message(s), {unread} unread")

    # --- reading
    def selected(self):
        return [i.data(ROLE) for i in (self.model.itemFromIndex(x) for x in self.list.selectionModel().selectedRows())
                if i is not None]

    def selected_uids(self):
        return {m["uid"] for m in self.selected()}

    def message_selected(self, idx, _prev=None):
        self.update_buttons()
        it = self.model.itemFromIndex(idx)
        if it is None:
            return
        m = it.data(ROLE)
        aid, name = self.current
        self.opening = (aid, name, m["uid"])
        self.reader.empty.setText("Opening…")
        self.worker(aid).imap_job(lambda i: B.parse(i.body(name, m["uid"])),
                                  lambda r, e: self.message_loaded(aid, name, m, r, e))

    def message_loaded(self, aid, name, m, parsed, err):
        if self.opening != (aid, name, m["uid"]):
            return
        if err:
            self.reader.clear(friendly(err))
            return
        parsed["uid"], parsed["folder"], parsed["acct"] = m["uid"], name, aid
        self.reader.show_message(parsed, set(self.cfg.get("show_pictures_from", [])))
        if not m["seen"]:
            m["seen"] = True
            self.list.viewport().update()
            self.update_status()
            it = self.folder_item(aid, name)
            f = self.folder(aid, name)
            if it is not None and f:
                unread = sum(1 for x in self.messages if not x["seen"])
                self.set_count(it, f, unread)

    def open_window(self):
        if not self.reader.msg:
            return
        win = QMainWindow(self)
        win.setWindowTitle(self.reader.msg["subject"])
        win.resize(820, 680)
        pane = ReadingPane()
        pane.show_message(self.reader.msg, set(self.cfg.get("show_pictures_from", [])))
        win.setCentralWidget(pane)
        win.show()

    def update_buttons(self):
        has = bool(self.selected()) and self.current is not None
        for b in (self.reply_btn, self.replyall_btn, self.forward_btn, self.delete_btn, self.junk_btn):
            b.setEnabled(has)
        if self.current:
            self.junk_btn.setEnabled(has and bool(self.special(self.current[0], "junk")))

    # --- acting on messages
    def act(self, fn, done_text=None, reload=True):
        if not self.current or not self.selected():
            return
        aid, name = self.current
        uids = sorted(self.selected_uids())
        self.worker(aid).imap_job(lambda i: fn(i, name, uids),
                                  lambda r, e: self.acted(aid, name, e, done_text, reload))

    def acted(self, aid, name, err, text, reload):
        if err:
            QMessageBox.warning(self, APP, friendly(err))
        elif text:
            self.status.setText(text)
        if reload:
            self.load_messages(aid, name, quiet=True)

    def delete(self):
        if not self.current or not self.selected():
            return
        aid, name = self.current
        trash = self.special(aid, "trash")
        uids = self.selected_uids()
        if trash and trash != name:
            self.act(lambda i, f, u: i.move(f, u, trash))
        else:
            if QMessageBox.question(self, APP, f"Permanently delete {len(uids)} message(s)?") != QMessageBox.Yes:
                return
            self.act(lambda i, f, u: i.delete_forever(f, u))
        self.drop_selected()

    def junk(self):
        if not self.current:
            return
        junk = self.special(self.current[0], "junk")
        if junk and junk != self.current[1]:
            self.act(lambda i, f, u: i.move(f, u, junk))
            self.drop_selected()

    def drop_selected(self):
        uids = self.selected_uids()
        self.messages = [m for m in self.messages if m["uid"] not in uids]
        self.fill_list()
        self.reader.clear()

    def mark(self, read):
        for m in self.selected():
            m["seen"] = read
        self.list.viewport().update()
        self.update_status()
        self.act(lambda i, f, u: i.set_flag(f, u, "\\Seen", read))

    def flag(self):
        sel = self.selected()
        on = not all(m["flagged"] for m in sel)
        for m in sel:
            m["flagged"] = on
        self.list.viewport().update()
        self.act(lambda i, f, u: i.set_flag(f, u, "\\Flagged", on), reload=False)

    def message_menu(self, pos):
        if not self.selected():
            return
        m = QMenu(self)
        m.addAction(QIcon.fromTheme("mail-reply-sender"), "Reply", self.reply)
        m.addAction(QIcon.fromTheme("mail-reply-all"), "Reply all", lambda: self.reply(all_=True))
        m.addAction(QIcon.fromTheme("mail-forward"), "Forward", self.forward)
        m.addSeparator()
        m.addAction("Mark as read", lambda: self.mark(True))
        m.addAction("Mark as unread", lambda: self.mark(False))
        m.addAction(QIcon.fromTheme("flag-red"), "Flag / unflag", self.flag)
        if self.reader.msg and self.reader.msg["from"][1]:
            addr = self.reader.msg["from"][1].lower()
            if addr not in self.cfg["show_pictures_from"]:
                m.addAction("Always show pictures from this sender", lambda: self.trust(addr))
        moves = m.addMenu("Move to folder")
        aid, name = self.current
        for f in self.folders.get(aid, []):
            if f["name"] != name:
                moves.addAction(QIcon.fromTheme(FOLDER_ICONS.get(f["kind"], "folder")), f["path"] if f["kind"] == "folder"
                                else f["display"], lambda d=f["name"]: (self.act(lambda i, fo, u: i.move(fo, u, d)),
                                                                        self.drop_selected()))
        m.addSeparator()
        m.addAction(QIcon.fromTheme("edit-delete"), "Delete", self.delete)
        m.exec(self.list.viewport().mapToGlobal(pos))

    def trust(self, addr):
        self.cfg["show_pictures_from"].append(addr)
        B.save_config(self.cfg)
        self.reader.render(True)

    def folder_menu(self, pos):
        it = self.tree.itemAt(pos)
        if not it:
            return
        aid, name = it.data(0, ROLE)
        m = QMenu(self)
        m.addAction("Sync", lambda: self.load_folders(aid))
        m.addAction("New folder…", lambda: self.new_folder(aid))
        if name:
            f = self.folder(aid, name)
            if f and f["kind"] in ("trash", "junk"):
                m.addAction("Empty " + f["display"], lambda: self.empty_folder(aid, name, f["display"]))
        m.addSeparator()
        m.addAction("Properties", lambda: self.edit_account(self.acct(aid)))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def empty_folder(self, aid, name, label):
        if QMessageBox.question(self, APP, f"Permanently delete everything in {label}?") != QMessageBox.Yes:
            return

        def go(i):
            i.select(name)
            typ, data = i.call(lambda c: c.uid("search", None, "ALL"))
            uids = (data[0] or b"").split()
            if uids:
                i.delete_forever(name, [int(u) for u in uids])
        self.worker(aid).imap_job(go, lambda r, e: self.acted(aid, name, e, f"{label} emptied.", True))

    def new_folder(self, aid=None):
        from PySide6.QtWidgets import QInputDialog
        aid = aid or (self.current[0] if self.current else (self.cfg["accounts"][0]["id"] if self.cfg["accounts"]
                                                             else None))
        if not aid:
            return
        name, ok = QInputDialog.getText(self, "Create Folder", "Folder name:")
        if ok and name.strip():
            self.worker(aid).imap_job(lambda i: i.call(lambda c: c.create(B.quote(name.strip()))),
                                      lambda r, e: (e and QMessageBox.warning(self, APP, friendly(e)),
                                                    self.load_folders(aid)))

    # --- writing
    def default_account(self):
        if self.current:
            return self.acct(self.current[0])
        return self.cfg["accounts"][0] if self.cfg["accounts"] else None

    def show_contacts(self):
        if self.contacts_win is None:
            self.contacts_win = C.ContactsWindow(self.book, CommandBar, self)
            self.contacts_win.write_to.connect(self.write_to)
        self.book.load()
        self.contacts_win.fill()
        self.contacts_win.show()
        self.contacts_win.raise_()

    def write_to(self, addr):
        a = self.default_account()
        if not a:
            return self.add_account()
        Compose(self, a, to=addr + ", ").show()

    def new_message(self):
        a = self.default_account()
        if not a:
            return self.add_account()
        Compose(self, a).show()

    def quote_block(self, msg, heading=True):
        name, addr = msg["from"]
        if msg["html"]:
            doc = QTextDocument()
            doc.setHtml(msg["html"])
            inner = doc.toHtml()
        else:
            inner = "<p>" + html.escape(msg["text"].strip()).replace("\n", "<br>") + "</p>"
        head = ""
        if heading:
            to = "; ".join(n or a for n, a in msg["to"])
            head = ("<div style='font-family:Arial; font-size:10pt'><b>----- Original Message -----</b><br>"
                    f"<b>From:</b> {html.escape(formataddr((name, addr)))}<br>"
                    f"<b>To:</b> {html.escape(to)}<br>"
                    f"<b>Sent:</b> {html.escape(B.long_date(msg.get('timestamp')) or msg['date'])}<br>"
                    f"<b>Subject:</b> {html.escape(msg['subject'])}</div>")
        return head + inner

    def reply(self, all_=False):
        msg = self.reader.msg
        if not msg:
            return
        a = self.acct(msg["acct"])
        mine = {x["email"].lower() for x in self.cfg["accounts"]}
        to_list = msg["reply_to"] or [msg["from"]]
        to = ", ".join(formataddr(x) for x in to_list)
        cc = ""
        if all_:
            others = [x for x in msg["to"] + msg["cc"] if x[1].lower() not in mine and x not in to_list]
            cc = ", ".join(formataddr(x) for x in others)
        subj = msg["subject"] if msg["subject"].lower().startswith("re:") else "Re: " + msg["subject"]
        Compose(self, a, to=to, cc=cc, subject=subj, quoted_html=self.quote_block(msg), reply_to=msg).show()

    def forward(self):
        msg = self.reader.msg
        if not msg:
            return
        a = self.acct(msg["acct"])
        subj = msg["subject"] if msg["subject"].lower().startswith("fw:") else "Fw: " + msg["subject"]
        c = Compose(self, a, subject=subj, quoted_html=self.quote_block(msg))
        # Forward the original attachments too.
        tmp = Path(tempfile.mkdtemp(prefix="halcyon-mail-fw-"))
        for att in msg["attachments"]:
            p = tmp / Path(att["name"]).name
            p.write_bytes(att["data"])
            c.attachments.append(str(p))
        c.show_files()
        c.show()

    def send(self, acct, msg, cb, reply_to=None):
        aid = acct["id"]
        sent = self.special(aid, "sent")
        w = self.worker(aid)
        gmail = acct["imap"]["host"].endswith("gmail.com")     # Gmail files sent mail itself

        def go():
            B.send(acct, w.password(), msg)
            # copy to Sent items (Bcc stripped), mark the original answered
            if (sent and not gmail) or reply_to:
                if w.imap is None:
                    w.imap = B.Imap(acct, w.password())
                if sent and not gmail:
                    del msg["Bcc"]
                    w.imap.append(sent, msg.as_bytes())
                if reply_to and reply_to.get("uid"):
                    w.imap.set_flag(reply_to["folder"], [reply_to["uid"]], "\\Answered", True)
            return True
        w.run(go, lambda r, e: (cb(r, e), not e and self.status.setText("Message sent."),
                                not e and self.current and self.load_messages(*self.current, quiet=True)))

    def save_draft(self, acct, msg):
        drafts = self.special(acct["id"], "drafts")
        if not drafts:
            return
        self.worker(acct["id"]).imap_job(lambda i: i.append(drafts, msg.as_bytes(), "(\\Seen \\Draft)"),
                                         lambda r, e: self.status.setText("Saved to Drafts." if not e else friendly(e)))

    # --- syncing and new mail
    def sync(self):
        for a in self.cfg["accounts"]:
            self.load_folders(a["id"])
        if self.current:
            self.load_messages(*self.current)

    def check_new(self):
        for a in self.cfg["accounts"]:
            aid = a["id"]
            inbox = next((f["name"] for f in self.folders.get(aid, []) if f["kind"] == "inbox"), None)
            if not inbox:
                continue
            since = self.last_uid.get((aid, inbox), 0)
            self.worker(aid).imap_job(lambda i, n=inbox, s=since: (i.headers(n, limit=20, since_uid=s), i.unseen(n)),
                                      lambda r, e, aid=aid, n=inbox, s=since: self.new_arrived(aid, n, s, r, e))

    def new_arrived(self, aid, name, since, result, err):
        if err:
            self.online.setText("Working offline")
            return
        self.online.setText("Working online")
        new, unseen = result
        it, f = self.folder_item(aid, name), self.folder(aid, name)
        if it is not None and f:
            self.set_count(it, f, unseen)
        fresh = [m for m in new if not m["seen"]]
        if new:
            self.last_uid[(aid, name)] = max(m["uid"] for m in new)
        if since and fresh:
            if len(fresh) == 1:
                notify(f"New e-mail from {fresh[0]['from']}", fresh[0]["subject"])
            else:
                notify(f"{len(fresh)} new e-mail messages", ", ".join(m["from"] for m in fresh[:3]))
        if self.current == (aid, name) and new:
            self.load_messages(aid, name, quiet=True)

    # --- account management
    def add_account(self):
        d = AccountDialog(self)
        if d.exec() == QDialog.Accepted:
            self.cfg["accounts"].append(d.acct)
            B.save_config(self.cfg)
            self.refresh_accounts()

    def edit_account(self, a):
        d = AccountDialog(self, a)
        if d.exec() == QDialog.Accepted:
            self.cfg["accounts"] = [d.acct if x["id"] == a["id"] else x for x in self.cfg["accounts"]]
            B.save_config(self.cfg)
            self.workers.pop(a["id"], None)
            self.refresh_accounts()

    def remove_account(self, a):
        if QMessageBox.question(self, APP, f"Remove {a['email']} from Halcyon Mail? The mail stays on the "
                                "server.") != QMessageBox.Yes:
            return
        self.cfg["accounts"] = [x for x in self.cfg["accounts"] if x["id"] != a["id"]]
        B.save_config(self.cfg)
        B.forget_password(a["id"])
        self.workers.pop(a["id"], None)
        if self.current and self.current[0] == a["id"]:
            self.current = None
            self.messages = []
            self.fill_list()
        self.refresh_accounts()


def light_palette():
    pal = QPalette()
    for role, color in ((QPalette.Window, "#f3f7fc"), (QPalette.WindowText, "#000000"),
                        (QPalette.Base, "#ffffff"), (QPalette.AlternateBase, "#f4f7fb"),
                        (QPalette.Text, "#000000"), (QPalette.PlaceholderText, "#7a8899"),
                        (QPalette.Button, "#eef3f9"), (QPalette.ButtonText, "#000000"),
                        (QPalette.Highlight, "#3399ff"), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.Link, "#1e5aa8"), (QPalette.ToolTipBase, "#ffffe1"),
                        (QPalette.ToolTipText, "#000000")):
        pal.setColor(role, QColor(color))
    return pal


def main():
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("halcyon-mail")
    app.setDesktopFileName("halcyon-mail")
    app.setStyle("Fusion")
    if not QIcon.themeName() or QIcon.fromTheme("mail-reply-sender").isNull():
        QIcon.setThemeSearchPaths(QIcon.themeSearchPaths() + [str(Path.home() / ".local/share/icons"),
                                                              "/usr/share/icons"])
        for theme in ("oxygen", "breeze"):          # Halcyon uses Oxygen's mail icons
            QIcon.setThemeName(theme)
            if not QIcon.fromTheme("mail-reply-sender").isNull():
                break
    app.setPalette(light_palette())
    if hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyleSheet(QSS)
    f = app.font()
    for fam in ("Segoe UI", "Selawik", "Noto Sans"):
        if fam in QFontDatabase.families():
            f.setFamily(fam)
            break
    f.setPointSizeF(9)
    app.setFont(f)
    w = Mail()
    w.show()
    args = [a for a in sys.argv[1:] if a.startswith("mailto:")]
    if args and w.cfg["accounts"]:
        url = QUrl(args[0])
        from urllib.parse import parse_qs
        q = parse_qs(url.query())
        Compose(w, w.cfg["accounts"][0], to=url.path(), subject=(q.get("subject") or [""])[0]).show()
    # halcyon-mail --open ACCOUNT FOLDER UID: a message found by Halcyon Search
    if "--open" in sys.argv:
        i = sys.argv.index("--open")
        try:
            w.pending_open = (sys.argv[i + 1], sys.argv[i + 2], int(sys.argv[i + 3]))
        except (IndexError, ValueError):
            pass
    # halcyon-mail --attach FILE...: a new message with files attached (Photo Viewer's E-mail button)
    if "--attach" in sys.argv:
        files = [str(Path(f).resolve()) for f in sys.argv[sys.argv.index("--attach") + 1:]
                 if not f.startswith("-") and Path(f).is_file()]
        if files and w.cfg["accounts"]:
            c = Compose(w, w.cfg["accounts"][0],
                        subject=Path(files[0]).name if len(files) == 1 else f"{len(files)} files")
            c.attachments = files
            c.show_files()
            c.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
