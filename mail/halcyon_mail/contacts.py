"""Halcyon Mail's address book: people you add, plus everyone you send mail
to (added automatically, as Windows Live Mail did). vCard import and export."""
import json
import os
import re
import time
import uuid
from email.utils import formataddr, getaddresses
from pathlib import Path

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QAction
from PySide6.QtWidgets import (QCompleter, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget)

STORE = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "contacts.json"
FIELDS = (("name", "Name"), ("email", "E-mail"), ("email2", "Other e-mail"), ("phone", "Phone"),
          ("mobile", "Mobile"), ("company", "Company"), ("address", "Address"), ("birthday", "Birthday"),
          ("notes", "Notes"))
TEXT = "#1e395b"
GRAY = "#6d7f95"


# ------------------------------------------------------------------ storage ---
class Book:
    def __init__(self, path=STORE):
        self.path = Path(path)
        self.people = []
        self.load()

    def load(self):
        try:
            self.people = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.people = []

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.people, indent=1, ensure_ascii=False))
        tmp.replace(self.path)

    def find_email(self, addr):
        a = addr.lower().strip()
        return next((p for p in self.people if a and a in (p.get("email", "").lower(), p.get("email2", "").lower())),
                    None)

    def upsert(self, person):
        person.setdefault("id", uuid.uuid4().hex[:12])
        person.setdefault("added", time.time())
        self.people = [p for p in self.people if p["id"] != person["id"]] + [person]
        self.save()
        return person

    def delete(self, pid):
        self.people = [p for p in self.people if p["id"] != pid]
        self.save()

    def collect(self, header_value):
        """Add everyone in a To/Cc line we haven't met yet; fill in a missing name."""
        changed = False
        for name, addr in getaddresses([header_value]):
            if not addr or "@" not in addr:
                continue
            p = self.find_email(addr)
            if p is None:
                self.people.append({"id": uuid.uuid4().hex[:12], "name": name.strip(), "email": addr.strip(),
                                    "added": time.time(), "auto": True})
                changed = True
            elif name and not p.get("name"):
                p["name"] = name.strip()
                changed = True
        if changed:
            self.save()

    def sorted(self):
        return sorted(self.people, key=lambda p: (p.get("name") or p.get("email", "")).lower())

    def entries(self):
        """'Name <address>' for every address, for autocomplete."""
        out = []
        for p in self.sorted():
            for key in ("email", "email2"):
                if p.get(key):
                    out.append(formataddr((p.get("name") or "", p[key])))
        return out


def display(p):
    return p.get("name") or p.get("email") or "(no name)"


# -------------------------------------------------------------------- vCard ---
def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text)


def _unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _escape(v):
    return v.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def parse_vcards(text):
    people, cur = [], None
    for line in _unfold(text).splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        name, *params = key.split(";")
        name = name.upper().split(".")[-1]         # item1.EMAIL -> EMAIL
        ptypes = ",".join(params).lower()
        if name == "BEGIN" and value.upper() == "VCARD":
            cur = {}
        elif name == "END" and cur is not None:
            if cur.get("email") or cur.get("name"):
                people.append(cur)
            cur = None
        elif cur is None:
            continue
        elif name == "FN":
            cur["name"] = _unescape(value)
        elif name == "N" and not cur.get("name"):
            parts = [_unescape(x) for x in value.split(";")]
            cur["name"] = " ".join(x for x in (parts[1] if len(parts) > 1 else "", parts[0]) if x)
        elif name == "EMAIL":
            cur["email2" if cur.get("email") else "email"] = value.strip()
        elif name == "TEL":
            cur["mobile" if "cell" in ptypes and not cur.get("mobile") else "phone" if not cur.get("phone")
                else "mobile"] = value.strip()
        elif name == "ORG":
            cur["company"] = _unescape(value.split(";")[0])
        elif name == "ADR":
            cur["address"] = ", ".join(x for x in (_unescape(v) for v in value.split(";")) if x.strip())
        elif name == "BDAY":
            cur["birthday"] = value.strip()
        elif name == "NOTE":
            cur["notes"] = _unescape(value)
    return people


def to_vcards(people):
    out = []
    for p in people:
        lines = ["BEGIN:VCARD", "VERSION:3.0", "FN:" + _escape(p.get("name") or p.get("email", ""))]
        name = (p.get("name") or "").rsplit(" ", 1)
        lines.append("N:" + (_escape(name[-1]) + ";" + _escape(name[0]) if len(name) == 2 else _escape(name[0])) + ";;;")
        for key in ("email", "email2"):
            if p.get(key):
                lines.append("EMAIL;TYPE=INTERNET:" + p[key])
        if p.get("phone"):
            lines.append("TEL;TYPE=VOICE:" + p["phone"])
        if p.get("mobile"):
            lines.append("TEL;TYPE=CELL:" + p["mobile"])
        if p.get("company"):
            lines.append("ORG:" + _escape(p["company"]))
        if p.get("address"):
            lines.append("ADR;TYPE=HOME:;;" + _escape(p["address"]) + ";;;;")
        if p.get("birthday"):
            lines.append("BDAY:" + p["birthday"])
        if p.get("notes"):
            lines.append("NOTE:" + _escape(p["notes"]))
        lines.append("END:VCARD")
        out.append("\r\n".join(lines))
    return "\r\n".join(out) + "\r\n"


# ---------------------------------------------------------- recipient boxes ---
class RecipientEdit(QLineEdit):
    """To/Cc/Bcc box that completes names and addresses from Contacts, one per comma."""
    def __init__(self, book, text=""):
        super().__init__(text)
        self.model = QStringListModel(book.entries(), self)
        self.comp = QCompleter(self.model, self)
        self.comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.comp.setFilterMode(Qt.MatchContains)
        self.comp.setWidget(self)
        self.comp.activated.connect(self.insert)
        self.textEdited.connect(self.complete_current)

    def current_token(self):
        before = self.text()[:self.cursorPosition()]
        return before.rsplit(",", 1)[-1].strip()

    def complete_current(self, _):
        tok = self.current_token()
        if len(tok) < 1:
            self.comp.popup().hide()
            return
        self.comp.setCompletionPrefix(tok)
        if self.comp.completionCount():
            self.comp.complete()
        else:
            self.comp.popup().hide()

    def insert(self, entry):
        pos = self.cursorPosition()
        before, after = self.text()[:pos], self.text()[pos:]
        head = before.rsplit(",", 1)[0] + ", " if "," in before else ""
        rest = after.split(",", 1)[1].strip() if "," in after else ""
        self.setText(head + entry + ", " + rest)
        self.setCursorPosition(len(head + entry + ", "))

    def add(self, entries):
        cur = self.text().strip().rstrip(",")
        self.setText(", ".join(x for x in [cur] + list(entries) if x) + ", ")

    def keyPressEvent(self, e):
        if self.comp.popup().isVisible() and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
            idx = self.comp.popup().currentIndex()
            if not idx.isValid():
                idx = self.comp.completionModel().index(0, 0)
            self.insert(idx.data())
            self.comp.popup().hide()
            return
        super().keyPressEvent(e)


class Picker(QDialog):
    """'Send an E-mail' contact picker behind the To / Cc / Bcc buttons."""
    def __init__(self, parent, book):
        super().__init__(parent)
        self.setWindowTitle("Send an E-mail")
        self.resize(460, 480)
        self.picked = {"to": [], "cc": [], "bcc": []}
        lay = QVBoxLayout(self)
        self.find = QLineEdit(placeholderText="Find a contact")
        lay.addWidget(self.find)
        self.list = QListWidget(selectionMode=QListWidget.ExtendedSelection)
        for entry in book.entries():
            self.list.addItem(entry)
        lay.addWidget(self.list, 1)
        self.find.textChanged.connect(self.filter)
        row = QHBoxLayout()
        self.boxes = {}
        for key in ("to", "cc", "bcc"):
            b = QPushButton(key.capitalize() + " →")
            b.clicked.connect(lambda _=False, k=key: self.take(k))
            box = QLineEdit(readOnly=True)
            col = QVBoxLayout()
            col.addWidget(b)
            col.addWidget(box)
            row.addLayout(col)
            self.boxes[key] = box
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.list.itemDoubleClicked.connect(lambda _: self.take("to"))

    def filter(self, t):
        t = t.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def take(self, key):
        for it in self.list.selectedItems():
            if it.text() not in self.picked[key]:
                self.picked[key].append(it.text())
        self.boxes[key].setText(", ".join(self.picked[key]))


# ------------------------------------------------------------ contact card ---
class EditDialog(QDialog):
    def __init__(self, parent, person=None):
        super().__init__(parent)
        self.person = dict(person or {})
        self.setWindowTitle("Edit Contact" if person else "Add a Contact")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.fields = {}
        for key, label in FIELDS:
            w = QPlainTextEdit(self.person.get(key, "")) if key in ("notes", "address") else \
                QLineEdit(self.person.get(key, ""))
            if isinstance(w, QPlainTextEdit):
                w.setFixedHeight(60)
            form.addRow(label + ":", w)
            self.fields[key] = w
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Add contact" if not person else "Save")
        bb.accepted.connect(self.check)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def check(self):
        for key, w in self.fields.items():
            v = w.toPlainText() if isinstance(w, QPlainTextEdit) else w.text()
            self.person[key] = v.strip()
        if not (self.person["name"] or self.person["email"]):
            QMessageBox.warning(self, self.windowTitle(), "Type a name or an e-mail address.")
            return
        self.person.pop("auto", None)
        self.accept()


class ContactsWindow(QMainWindow):
    """Windows Live Contacts: the list on the left, the selected card on the right."""
    write_to = Signal(str)

    def __init__(self, book, command_bar_cls, parent=None):
        super().__init__(parent)
        self.book = book
        self.setWindowTitle("Contacts - Halcyon Mail")
        self.setWindowIcon(QIcon.fromTheme("x-office-address-book", QIcon.fromTheme("contact-new")))
        self.resize(820, 560)
        w = QWidget()
        self.setCentralWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = command_bar_cls()
        bar.add("New", "contact-new", self.new, tip="New contact (Ctrl+N)")
        self.edit_btn = bar.add("Edit", "document-edit", self.edit)
        self.del_btn = bar.add("Delete", "edit-delete", self.delete)
        self.mail_btn = bar.add("E-mail", "mail-message-new", self.email)
        bar.sep()
        bar.add("Import", "document-import", self.import_vcf, tip="Import contacts from a vCard (.vcf) file")
        bar.add("Export", "document-export", self.export_vcf, tip="Export all contacts as vCards (.vcf)")
        bar.lay.addStretch(1)
        self.find = QLineEdit(placeholderText="Find a contact", objectName="find")
        self.find.setFixedWidth(200)
        self.find.textChanged.connect(self.fill)
        bar.lay.addWidget(self.find)
        lay.addWidget(bar)
        split = QSplitter(Qt.Horizontal, handleWidth=1)
        self.list = QListWidget(objectName="contacts")
        self.list.setStyleSheet("QListWidget { border: none; border-right: 1px solid #c5d6e8; background: white; }"
                                "QListWidget::item { padding: 5px 6px; border-bottom: 1px solid #eef2f7; }"
                                "QListWidget::item:selected { background: #d6e8fb; color: black; }")
        self.list.currentItemChanged.connect(lambda *_: self.show_card())
        self.list.itemDoubleClicked.connect(lambda _: self.edit())
        self.card = QLabel(alignment=Qt.AlignTop | Qt.AlignLeft, wordWrap=True, textFormat=Qt.RichText)
        self.card.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.card.setOpenExternalLinks(False)
        self.card.linkActivated.connect(lambda url: self.write_to.emit(url.removeprefix("mailto:")))
        self.card.setContentsMargins(22, 18, 22, 18)
        self.card.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #eef4fb);")
        split.addWidget(self.list)
        split.addWidget(self.card)
        split.setSizes([300, 520])
        lay.addWidget(split, 1)
        for key, fn in (("Ctrl+N", self.new), ("Delete", self.delete), ("Ctrl+E", self.find.setFocus)):
            a = QAction(self, shortcut=QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
        self.fill()

    def fill(self, *_):
        q = self.find.text().strip().lower()
        sel = self.current()
        self.list.clear()
        for p in self.book.sorted():
            if q and q not in " ".join(str(v) for v in p.values()).lower():
                continue
            it = QListWidgetItem(QIcon.fromTheme("user-identity"), display(p) +
                                 (f"\n{p['email']}" if p.get("name") and p.get("email") else ""))
            it.setData(Qt.UserRole, p["id"])
            self.list.addItem(it)
            if sel and p["id"] == sel["id"]:
                self.list.setCurrentItem(it)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.show_card()

    def current(self):
        it = self.list.currentItem() if hasattr(self, "list") else None
        pid = it.data(Qt.UserRole) if it else None
        return next((p for p in self.book.people if p["id"] == pid), None)

    def show_card(self):
        p = self.current()
        for b in (self.edit_btn, self.del_btn, self.mail_btn):
            b.setEnabled(p is not None)
        if not p:
            self.card.setText(f"<p style='color:{GRAY}'>No contacts yet. People you send e-mail to are added "
                              "here automatically, or click <b>New</b>.</p>")
            return
        rows = []
        for key, label in FIELDS[1:]:
            v = p.get(key)
            if not v:
                continue
            esc = v.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")
            if key in ("email", "email2"):
                esc = f"<a href='mailto:{esc}' style='color:#1e5aa8'>{esc}</a>"
            rows.append(f"<tr><td style='color:{GRAY}; padding:4px 16px 4px 0'>{label}</td><td>{esc}</td></tr>")
        auto = (f"<p style='color:{GRAY}; font-size:8pt'>Added automatically when you sent this person "
                "e-mail.</p>") if p.get("auto") else ""
        name = (p.get("name") or p.get("email", "")).replace("&", "&amp;").replace("<", "&lt;")
        self.card.setText(f"<div style='font-size:16pt; color:{TEXT}'>{name}</div>"
                          f"<table style='margin-top:12px'>{''.join(rows)}</table>{auto}")

    def new(self):
        d = EditDialog(self)
        if d.exec():
            self.book.upsert(d.person)
            self.fill()

    def edit(self):
        p = self.current()
        if p:
            d = EditDialog(self, p)
            if d.exec():
                self.book.upsert(d.person)
                self.fill()

    def delete(self):
        p = self.current()
        if p and QMessageBox.question(self, "Delete Contact", f"Delete {display(p)}?") == QMessageBox.Yes:
            self.book.delete(p["id"])
            self.fill()

    def email(self):
        p = self.current()
        if p and p.get("email"):
            self.write_to.emit(formataddr((p.get("name") or "", p["email"])))

    def import_vcf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Contacts", str(Path.home()), "vCard (*.vcf *.vcard)")
        if not path:
            return
        try:
            found = parse_vcards(Path(path).read_text(errors="replace"))
        except OSError as e:
            QMessageBox.warning(self, "Import", str(e))
            return
        added = updated = 0
        for p in found:
            old = self.book.find_email(p["email"]) if p.get("email") else None
            if old is None:
                self.book.people.append(dict(p, id=uuid.uuid4().hex[:12], added=time.time()))
                added += 1
                continue
            # Already here (often collected from mail with just an address): fill in what's missing.
            fresh = {k: v for k, v in p.items() if v and not old.get(k)}
            if fresh:
                old.update(fresh)
                old.pop("auto", None)
                updated += 1
        self.book.save()
        self.fill()
        QMessageBox.information(self, "Import", f"Imported {added} new contact(s)"
                                + (f" and filled in details for {updated} you already had." if updated else "."))

    def export_vcf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Contacts", str(Path.home() / "Contacts.vcf"),
                                              "vCard (*.vcf)")
        if path:
            Path(path).write_text(to_vcards(self.book.sorted()))
