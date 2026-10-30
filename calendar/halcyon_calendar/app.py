"""Halcyon Calendar, after Windows Calendar in Vista: a mini month, your
calendars and tasks on the left; Day, Week or Month in the middle; the
Details pane on the right. Reminders pop up even with the window closed
(`halcyon-calendar --daemon`, started at logon).

    halcyon-calendar [FILE.ics | webcal://…]   open, importing or subscribing"""
import fcntl
import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QDateTime, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QPalette, QPen, QPixmap, QTextCharFormat)
from PySide6.QtWidgets import (QApplication, QCalendarWidget, QCheckBox, QColorDialog, QComboBox, QDateEdit,
                               QDateTimeEdit, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QScrollArea, QSplitter, QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from . import model as M

APP = "Halcyon Calendar"
TEXT = "#1e395b"
GRAY = "#6d7f95"
HOUR = 44                         # pixels per hour in Day/Week
LOCK = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "halcyon-calendar.lock"


def to_dt(qdt):
    return qdt.toPython().replace(tzinfo=None, second=0, microsecond=0)


def week_start(d):
    return d - timedelta(days=d.weekday() + 1 if d.weekday() != 6 else 0)     # Sunday, as Vista did


def fmt_time(dt):
    return dt.strftime("%-I:%M %p").lower().replace(":00", "")


# ----------------------------------------------------------------- chrome ---
class Bar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(38)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(6, 3, 6, 3)
        self.lay.setSpacing(2)

    def add(self, text, icon, slot=None, menu=None, tip=None):
        b = QToolButton(text=text, icon=QIcon.fromTheme(icon), toolButtonStyle=Qt.ToolButtonTextBesideIcon,
                        autoRaise=True, toolTip=tip or text)
        b.setIconSize(QSize(18, 18))
        if menu:
            b.setMenu(menu)
            b.setPopupMode(QToolButton.InstantPopup)
        if slot:
            b.clicked.connect(slot)
        self.lay.addWidget(b)
        return b

    def sep(self):
        line = QFrame(frameShape=QFrame.VLine)
        line.setStyleSheet("color: #b7c9df;")
        self.lay.addSpacing(3)
        self.lay.addWidget(line)
        self.lay.addSpacing(3)

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


def section_label(text):
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {TEXT}; font-weight: bold; padding: 8px 4px 2px 4px; "
                      "border-bottom: 1px solid #c5d6e8;")
    return lbl


def swatch(color, checked=True):
    pm = QPixmap(14, 14)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color).darker(140), 1))
    p.setBrush(QColor(color) if checked else QColor("white"))
    p.drawRoundedRect(QRectF(1.5, 1.5, 11, 11), 2, 2)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- day/week ---
class TimeGrid(QWidget):
    """Day or week of hour rows; appointments drawn as rounded glass blocks."""
    selected = Signal(object, object)          # event, occurrence start
    create = Signal(object, object, bool)      # start, end, all-day
    moved = Signal(object, object, object)     # event, new start, new end

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.days = 7
        self.first = date.today()
        self.items = []                        # (rect, event, start, end, kind)
        self.allday_h = 24
        self.head_h = 26
        self.gutter = 54
        self.drag = None
        self.setMouseTracking(True)
        self.setMinimumHeight(self.head_h + self.allday_h + HOUR * 24)
        self.setFocusPolicy(Qt.StrongFocus)

    def top(self):
        return self.head_h + self.allday_h

    def col_w(self):
        return (self.width() - self.gutter) / self.days

    def y_of(self, t):
        return self.top() + (t.hour + t.minute / 60) * HOUR

    def at(self, pos):
        """(datetime snapped to 15 min, day index) under a point in the hour grid."""
        i = int((pos.x() - self.gutter) // self.col_w())
        i = max(0, min(self.days - 1, i))
        mins = max(0, min(24 * 60 - 15, int((pos.y() - self.top()) / HOUR * 60) // 15 * 15))
        return datetime.combine(self.first + timedelta(days=i), datetime.min.time()) + timedelta(minutes=mins), i

    def layout_items(self):
        start = datetime.combine(self.first, datetime.min.time())
        occ = self.win.store.between(start, start + timedelta(days=self.days))
        self.items = []
        cw = self.col_w()
        # all-day strip
        allday = [(e, s, f) for e, s, f in occ if e.get("allday") or (f - s) >= timedelta(hours=24)]
        rows = []
        for e, s, f in allday:
            a = max(0, (s.date() - self.first).days)
            b = min(self.days - 1, ((f - timedelta(seconds=1)).date() - self.first).days)
            row = next((i for i, r in enumerate(rows) if all(b < x or a > y for x, y in r)), None)
            if row is None:
                rows.append([])
                row = len(rows) - 1
            rows[row].append((a, b))
            r = QRectF(self.gutter + a * cw + 2, self.head_h + 2 + row * 20, (b - a + 1) * cw - 4, 18)
            self.items.append((r, e, s, f, "allday"))
        self.allday_h = max(24, 4 + len(rows) * 20)
        self.setMinimumHeight(self.head_h + self.allday_h + HOUR * 24)
        # timed: per day, overlapping groups share the column
        timed = [(e, s, f) for e, s, f in occ if (e, s, f) not in allday]
        for i in range(self.days):
            d = self.first + timedelta(days=i)
            day = sorted([(e, max(s, datetime.combine(d, datetime.min.time())),
                           min(f, datetime.combine(d + timedelta(days=1), datetime.min.time())), s, f)
                          for e, s, f in timed if s.date() <= d <= (f - timedelta(seconds=1)).date()],
                         key=lambda x: x[1])
            groups, cur, end = [], [], None
            for it in day:
                if cur and it[1] >= end:
                    groups.append(cur)
                    cur, end = [], None
                cur.append(it)
                end = max(end, it[2]) if end else it[2]
            if cur:
                groups.append(cur)
            for g in groups:
                cols = []
                for it in g:
                    c = next((k for k, col in enumerate(cols) if col[-1][2] <= it[1]), None)
                    if c is None:
                        cols.append([it])
                    else:
                        cols[c].append(it)
                for k, col in enumerate(cols):
                    for e, a, b, s, f in col:
                        x = self.gutter + i * cw + 2 + k * (cw - 6) / len(cols)
                        y1 = self.top() + (a - datetime.combine(d, datetime.min.time())).total_seconds() / 3600 * HOUR
                        y2 = self.top() + (b - datetime.combine(d, datetime.min.time())).total_seconds() / 3600 * HOUR
                        self.items.append((QRectF(x, y1 + 1, (cw - 6) / len(cols) - 1, max(16, y2 - y1 - 2)),
                                           e, s, f, "timed"))

    def paintEvent(self, ev):
        self.layout_items()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, cw = self.width(), self.col_w()
        p.fillRect(self.rect(), QColor("white"))
        today = date.today()
        # hours: working hours white, the rest pale blue
        for h in range(24):
            y = self.top() + h * HOUR
            if h < 8 or h >= 17:
                p.fillRect(QRectF(self.gutter, y, w - self.gutter, HOUR), QColor("#eef4fb"))
            p.setPen(QColor("#d5e0ec"))
            p.drawLine(QPoint(self.gutter, int(y)), QPoint(w, int(y)))
            p.setPen(QColor("#edf2f8"))
            p.drawLine(QPoint(self.gutter, int(y + HOUR / 2)), QPoint(w, int(y + HOUR / 2)))
            p.setPen(QColor(GRAY))
            label = "12 AM" if h == 0 else "Noon" if h == 12 else f"{h % 12} {'AM' if h < 12 else 'PM'}"
            p.drawText(QRect(0, int(y) + 2, self.gutter - 6, 16), Qt.AlignRight | Qt.AlignTop, label)
        for i in range(self.days):
            x = self.gutter + i * cw
            d = self.first + timedelta(days=i)
            if d == today:
                p.fillRect(QRectF(x, self.top(), cw, HOUR * 24), QColor(255, 244, 196, 70))
            p.setPen(QColor("#c5d6e8"))
            p.drawLine(QPoint(int(x), 0), QPoint(int(x), self.height()))
            # header
            r = QRectF(x, 0, cw, self.head_h)
            g = QLinearGradient(0, 0, 0, self.head_h)
            if d == today:
                g.setColorAt(0, QColor("#fff3c4"))
                g.setColorAt(1, QColor("#f7d97a"))
            else:
                g.setColorAt(0, QColor("#f6f9fd"))
                g.setColorAt(1, QColor("#dfe9f5"))
            p.fillRect(r, g)
            p.setPen(QColor(TEXT))
            f = QFont(self.font())
            f.setBold(d == today)
            p.setFont(f)
            text = d.strftime("%A, %B %-d") if self.days == 1 else d.strftime("%a %-m/%-d")
            p.drawText(r, Qt.AlignCenter, text)
        f = QFont(self.font())
        f.setBold(False)
        p.setFont(f)
        p.fillRect(QRectF(0, self.head_h, w, self.allday_h), QColor("#e4edf8"))
        p.setPen(QColor("#a9bfd8"))
        p.drawLine(QPoint(0, self.top()), QPoint(w, self.top()))
        p.drawLine(QPoint(0, self.head_h), QPoint(w, self.head_h))
        # appointments
        sel = self.win.current
        for r, e, s, fnd, kind in self.items:
            self.draw_item(p, r, e, s, fnd, kind, sel is not None and sel[0]["id"] == e["id"] and sel[1] == s)
        # now
        now = datetime.now()
        if self.first <= now.date() < self.first + timedelta(days=self.days):
            i = (now.date() - self.first).days
            y = self.y_of(now)
            p.setPen(QPen(QColor("#e8432e"), 2))
            p.drawLine(QPointF(self.gutter + i * cw, y), QPointF(self.gutter + (i + 1) * cw, y))
        pv = getattr(self, "_preview", None)
        if self.drag and self.drag[0] in ("move", "resize") and pv:
            e, a, b = pv
            if a.date() == (b - timedelta(seconds=1)).date() and self.first <= a.date() < self.first + timedelta(days=self.days):
                i = (a.date() - self.first).days
                r = QRectF(self.gutter + i * cw + 2, self.y_of(a) + 1, cw - 6, max(16, self.y_of(b) - self.y_of(a) - 2))
                p.setOpacity(0.75)
                self.draw_item(p, r, e, a, b, "timed", True)
                p.setOpacity(1)
        if self.drag and self.drag[0] == "new":
            a, b = sorted((self.drag[1], self.drag[2]))
            i = (a.date() - self.first).days
            r = QRectF(self.gutter + i * cw + 2, self.y_of(a), cw - 4, max(HOUR / 4, self.y_of(b) - self.y_of(a) + HOUR / 4))
            p.setPen(QPen(QColor("#3a7bd5"), 1))
            p.setBrush(QColor(58, 123, 213, 60))
            p.drawRoundedRect(r, 3, 3)

    def draw_item(self, p, r, e, s, f, kind, selected):
        col = QColor(self.win.store.calendar(e["calendar"])["color"])
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        g.setColorAt(0, col.lighter(175))
        g.setColorAt(1, col.lighter(135))
        path = QPainterPath()
        path.addRoundedRect(r, 3, 3)
        p.fillPath(path, g)
        p.setPen(QPen(col.darker(130) if not selected else QColor("#1a1a1a"), 2 if selected else 1))
        p.drawPath(path)
        p.fillRect(QRectF(r.left() + 1, r.top() + 1, 4, r.height() - 2), col)
        p.setPen(QColor("#10263f"))
        inner = r.adjusted(8, 1, -3, -1)
        title = e["title"] or "(no title)"
        if kind == "timed" and r.height() > 30:
            fb = QFont(self.font())
            fb.setBold(True)
            p.setFont(fb)
            used = p.boundingRect(inner, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, title)
            p.drawText(inner, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, title)
            p.setFont(self.font())
            sub = f"{fmt_time(s)} – {fmt_time(f)}" + (f"\n{e['location']}" if e.get("location") else "")
            p.save()
            p.setClipRect(inner)
            p.drawText(inner.adjusted(0, used.height() + 1, 0, 0), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, sub)
            p.restore()
        else:
            p.drawText(inner, Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(title if kind == "allday" else f"{fmt_time(s)} {title}",
                                                  Qt.ElideRight, int(inner.width())))
        if e.get("repeat", "none") != "none":
            p.drawText(r.adjusted(0, 1, -3, 0), Qt.AlignRight | Qt.AlignTop, "↻")

    def item_at(self, pos):
        for r, e, s, f, kind in reversed(self.items):
            if r.contains(pos):
                return r, e, s, f, kind
        return None

    def mousePressEvent(self, ev):
        pos = ev.position()
        self.layout_items()                       # what's on screen right now, even before a repaint
        hit = self.item_at(pos)
        if hit:
            r, e, s, f, kind = hit
            self.selected.emit(e, s)
            if kind == "timed" and not self.win.store.readonly(e) and e.get("repeat", "none") == "none":
                mode = "resize" if pos.y() > r.bottom() - 6 else "move"
                self.drag = (mode, e, s, f, self.at(pos)[0])
            return
        self.selected.emit(None, None)
        if pos.y() > self.top():
            t, _ = self.at(pos)
            self.drag = ("new", t, t)

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if not self.drag:
            hit = self.item_at(pos)
            self.setCursor(Qt.SizeVerCursor if hit and hit[4] == "timed" and pos.y() > hit[0].bottom() - 6
                           else Qt.ArrowCursor)
            return
        t, _ = self.at(pos)
        if self.drag[0] == "new":
            self.drag = ("new", self.drag[1], t)
        else:
            mode, e, s, f, grab = self.drag
            delta = t - grab
            if mode == "move":
                self.preview(e, s + delta, f + delta)
            else:
                self.preview(e, s, max(s + timedelta(minutes=15), f + delta))
        self.update()

    def preview(self, e, s, f):
        self._preview = (e, s, f)

    def mouseReleaseEvent(self, ev):
        d, self.drag = self.drag, None
        if not d:
            return
        if d[0] == "new":
            a, b = sorted((d[1], d[2]))
            if b - a >= timedelta(minutes=15):
                self.create.emit(a, b + timedelta(minutes=15), False)
        elif getattr(self, "_preview", None):
            e, s, f = self._preview
            self._preview = None
            if (s, f) != (d[2], d[3]):
                self.moved.emit(e, s, f)
        self.update()

    def mouseDoubleClickEvent(self, ev):
        pos = ev.position()
        self.layout_items()
        if self.item_at(pos):
            self.win.title_edit.setFocus()
            self.win.title_edit.selectAll()
            return
        if pos.y() < self.top() and pos.y() > self.head_h:
            t, _ = self.at(QPointF(pos.x(), self.top() + 1))
            self.create.emit(t.replace(hour=0, minute=0), t.replace(hour=0, minute=0) + timedelta(days=1), True)
        elif pos.y() >= self.top():
            t, _ = self.at(pos)
            t = t.replace(minute=0 if t.minute < 30 else 30)
            self.create.emit(t, t + timedelta(hours=1), False)


# ------------------------------------------------------------------- month ---
class MonthGrid(QWidget):
    selected = Signal(object, object)
    open_day = Signal(object)
    create = Signal(object, object, bool)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.month = date.today().replace(day=1)
        self.items = []
        self.cells = []

    def first(self):
        return week_start(self.month)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        head = 24
        w, h = self.width(), self.height() - head
        cw, ch = w / 7, h / 6
        start = self.first()
        occ = self.win.store.between(datetime.combine(start, datetime.min.time()),
                                     datetime.combine(start + timedelta(days=42), datetime.min.time()))
        g = QLinearGradient(0, 0, 0, head)
        g.setColorAt(0, QColor("#f6f9fd"))
        g.setColorAt(1, QColor("#dfe9f5"))
        p.fillRect(QRectF(0, 0, w, head), g)
        p.setPen(QColor(TEXT))
        for i, n in enumerate(("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")):
            p.drawText(QRectF(i * cw, 0, cw, head), Qt.AlignCenter, n)
        self.items, self.cells = [], []
        today = date.today()
        fm = p.fontMetrics()
        line = fm.height() + 2
        sel = self.win.current
        for k in range(42):
            d = start + timedelta(days=k)
            r = QRectF((k % 7) * cw, head + (k // 7) * ch, cw, ch)
            self.cells.append((r, d))
            if d.month != self.month.month:
                p.fillRect(r, QColor("#f2f5f9"))
            if d == today:
                p.fillRect(r, QColor(255, 244, 196, 120))
            p.setPen(QColor("#c5d6e8"))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            f = QFont(self.font())
            f.setBold(d == today or d.day == 1)
            p.setFont(f)
            p.setPen(QColor(TEXT) if d.month == self.month.month else QColor("#9aa9bb"))
            p.drawText(r.adjusted(4, 2, -4, 0), Qt.AlignRight | Qt.AlignTop,
                       d.strftime("%B %-d") if d.day == 1 else str(d.day))
            p.setFont(self.font())
            day = [(e, s, fn) for e, s, fn in occ if s.date() <= d <= (fn - timedelta(seconds=1)).date()]
            y = r.top() + line + 2
            room = int((r.bottom() - y - 2) // line)
            for n, (e, s, fn) in enumerate(day):
                if n == room - 1 and len(day) > room:
                    p.setPen(QColor(GRAY))
                    p.drawText(QRectF(r.left() + 4, y, r.width() - 8, line), Qt.AlignLeft,
                               f"{len(day) - n} more…")
                    break
                col = QColor(self.win.store.calendar(e["calendar"])["color"])
                ir = QRectF(r.left() + 3, y, r.width() - 6, line - 1)
                is_sel = sel is not None and sel[0]["id"] == e["id"] and sel[1] == s
                if e.get("allday") or (fn - s) >= timedelta(hours=24):
                    p.setPen(QPen(col.darker(130), 1.5 if is_sel else 1))
                    p.setBrush(col.lighter(165))
                    p.drawRoundedRect(ir, 2, 2)
                    text = e["title"]
                else:
                    if is_sel:
                        p.setPen(Qt.NoPen)
                        p.setBrush(col.lighter(170))
                        p.drawRoundedRect(ir, 2, 2)
                    p.setPen(Qt.NoPen)
                    p.setBrush(col)
                    p.drawEllipse(QRectF(ir.left() + 2, ir.center().y() - 3, 6, 6))
                    text = f"{fmt_time(s)} {e['title']}"
                p.setPen(QColor("#10263f"))
                p.drawText(ir.adjusted(11, 0, -2, 0), Qt.AlignLeft | Qt.AlignVCenter,
                           fm.elidedText(text, Qt.ElideRight, int(ir.width() - 13)))
                self.items.append((ir, e, s))
                y += line

    def mousePressEvent(self, ev):
        pos = ev.position()
        for r, e, s in self.items:
            if r.contains(pos):
                self.selected.emit(e, s)
                return
        self.selected.emit(None, None)

    def mouseDoubleClickEvent(self, ev):
        pos = ev.position()
        if any(r.contains(pos) for r, _, _ in self.items):
            return
        for r, d in self.cells:
            if r.contains(pos):
                if pos.y() < r.top() + 18:
                    self.open_day.emit(d)
                else:
                    s = datetime.combine(d, datetime.min.time())
                    self.create.emit(s, s + timedelta(days=1), True)
                return


# ---------------------------------------------------------------- details ---
class Details(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(270)
        w = QWidget(objectName="details")
        self.setWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 10, 12, 10)
        self.heading = QLabel()
        f = self.heading.font()
        f.setPointSizeF(12)
        self.heading.setFont(f)
        self.heading.setStyleSheet(f"color: {TEXT};")
        lay.addWidget(self.heading)
        self.form_box = QWidget()
        form = QFormLayout(self.form_box)
        form.setContentsMargins(0, 6, 0, 0)
        form.setLabelAlignment(Qt.AlignRight)
        win.title_edit = self.title = QLineEdit()
        self.location = QLineEdit()
        self.cal = QComboBox()
        self.url = QLineEdit(placeholderText="http://")
        self.allday = QCheckBox("All-day appointment")
        self.start = QDateTimeEdit(calendarPopup=True, displayFormat="M/d/yyyy  h:mm AP")
        self.end = QDateTimeEdit(calendarPopup=True, displayFormat="M/d/yyyy  h:mm AP")
        self.repeat = QComboBox()
        for k, v in M.REPEATS.items():
            self.repeat.addItem(v, k)
        self.until = QDateEdit(calendarPopup=True, displayFormat="M/d/yyyy")
        self.until_on = QCheckBox("Until")
        ur = QHBoxLayout()
        ur.addWidget(self.until_on)
        ur.addWidget(self.until, 1)
        self.reminder = QComboBox()
        for mins, label in M.REMINDERS:
            self.reminder.addItem(label, mins)
        self.notes = QPlainTextEdit()
        self.notes.setMinimumHeight(120)
        self.ro = QLabel(wordWrap=True)
        self.ro.setStyleSheet(f"color: {GRAY};")
        form.addRow("Title:", self.title)
        form.addRow("Location:", self.location)
        form.addRow("Calendar:", self.cal)
        form.addRow("URL:", self.url)
        form.addRow(section_label("Appointment Information"))
        form.addRow("", self.allday)
        form.addRow("Start:", self.start)
        form.addRow("End:", self.end)
        form.addRow("Recurrence:", self.repeat)
        form.addRow("", ur)
        form.addRow(section_label("Reminder"))
        form.addRow("Reminder:", self.reminder)
        form.addRow(section_label("Notes"))
        form.addRow(self.notes)
        form.addRow(self.ro)
        lay.addWidget(self.form_box)
        self.empty = QLabel("Select an appointment to see its details here, or double-click the calendar "
                            "to make a new one.", wordWrap=True)
        self.empty.setStyleSheet(f"color: {GRAY}; padding-top: 10px;")
        lay.addWidget(self.empty)
        lay.addStretch(1)
        self.event = None
        self.loading = False
        for w_ in (self.title, self.location, self.url):
            w_.editingFinished.connect(self.commit)
        for w_ in (self.start, self.end, self.until):
            w_.editingFinished.connect(self.commit)
        for w_ in (self.cal, self.repeat, self.reminder):
            w_.currentIndexChanged.connect(self.commit)
        self.allday.toggled.connect(self.commit)
        self.until_on.toggled.connect(self.commit)
        self.notes.textChanged.connect(self.notes_changed)
        self.notes_timer = QTimer(self, singleShot=True, interval=800, timeout=self.commit)
        self.show_event(None)

    def notes_changed(self):
        if not self.loading:
            self.notes_timer.start()

    def show_event(self, e):
        self.event = e
        self.form_box.setVisible(e is not None)
        self.empty.setVisible(e is None)
        self.heading.setText("Details" if e is None else ("Appointment" if not e.get("allday") else "All-day appointment"))
        if e is None:
            return
        self.loading = True
        ro = self.win.store.readonly(e)
        self.cal.clear()
        for c in self.win.store.calendars:
            if not c.get("url") or c["id"] == e["calendar"]:
                self.cal.addItem(swatch(c["color"]), c["name"], c["id"])
        self.cal.setCurrentIndex(max(0, self.cal.findData(e["calendar"])))
        self.title.setText(e["title"])
        self.location.setText(e.get("location", ""))
        self.url.setText(e.get("url", ""))
        self.allday.setChecked(bool(e.get("allday")))
        self.start.setDateTime(QDateTime(M.parse_iso(e["start"])))
        end = M.parse_iso(e["end"])
        if e.get("allday"):
            end -= timedelta(days=1)          # shown inclusive, stored exclusive
        self.end.setDateTime(QDateTime(end))
        fmt = "M/d/yyyy" if e.get("allday") else "M/d/yyyy  h:mm AP"
        self.start.setDisplayFormat(fmt)
        self.end.setDisplayFormat(fmt)
        self.repeat.setCurrentIndex(max(0, self.repeat.findData(e.get("repeat", "none"))))
        self.until_on.setChecked(bool(e.get("until")))
        self.until.setDate(QDate(M.parse_iso(e["until"]).date()) if e.get("until") else
                           QDate(M.parse_iso(e["start"]).date() + timedelta(days=90)))
        self.until.setEnabled(bool(e.get("until")))
        self.until_on.setEnabled(e.get("repeat", "none") != "none")
        mins = e.get("reminder", -1)
        if self.reminder.findData(mins) < 0:          # imported reminders can be any length: keep them
            label = f"{mins // 1440} days before" if mins % 1440 == 0 else \
                f"{mins // 60} hours before" if mins % 60 == 0 else f"{mins} minutes before"
            self.reminder.addItem(label, mins)
        self.reminder.setCurrentIndex(max(0, self.reminder.findData(mins)))
        self.notes.setPlainText(e.get("notes", ""))
        for w_ in (self.title, self.location, self.url, self.cal, self.allday, self.start, self.end, self.repeat,
                   self.until, self.until_on, self.reminder):
            w_.setEnabled(not ro and (w_ is not self.until or bool(e.get("until"))))
        self.notes.setReadOnly(ro)
        name = self.win.store.calendar(e["calendar"])["name"]
        self.ro.setText(f"This appointment comes from the subscribed calendar “{name}” and can't be changed here."
                        if ro else "")
        self.loading = False

    def commit(self, *_):
        e = self.event
        if self.loading or e is None or self.win.store.readonly(e):
            return
        e["title"] = self.title.text().strip() or "New Appointment"
        e["location"] = self.location.text().strip()
        e["url"] = self.url.text().strip()
        e["calendar"] = self.cal.currentData() or e["calendar"]
        allday = self.allday.isChecked()
        s, f = to_dt(self.start.dateTime()), to_dt(self.end.dateTime())
        if allday:
            s = datetime.combine(s.date(), datetime.min.time())
            f = datetime.combine(max(f.date(), s.date()) + timedelta(days=1), datetime.min.time())
        elif allday != e.get("allday"):            # just turned off: give it an hour
            s = s.replace(hour=9)
            f = s + timedelta(hours=1)
        if f <= s:
            f = s + (timedelta(days=1) if allday else timedelta(hours=1))
        e["allday"] = allday
        e["start"], e["end"] = M.iso(s), M.iso(f)
        e["repeat"] = self.repeat.currentData()
        e["until"] = M.iso(datetime.combine(self.until.date().toPython(), datetime.min.time())) \
            if self.until_on.isChecked() and e["repeat"] != "none" else ""
        e["reminder"] = self.reminder.currentData()
        e["notes"] = self.notes.toPlainText()
        self.win.store.update(e)
        self.win.current = (e, s if e["repeat"] == "none" else self.win.current[1])
        self.show_event(e)
        self.win.refresh(keep_details=True)


# ----------------------------------------------------------------- window ---
QSS = f"""
QMainWindow {{ background: #f3f7fc; }}
QToolButton {{ border: 1px solid transparent; border-radius: 3px; padding: 3px 6px; color: {TEXT}; }}
QToolButton:hover {{ border-color: #a8c8ec; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 #fdfeff, stop:0.5 #ebf4fd, stop:0.51 #d9eafb, stop:1 #e8f2fc); }}
QToolButton:checked, QToolButton:pressed {{ background: #c8dff7; border-color: #7da2ce; }}
QToolButton::menu-indicator {{ image: none; }}
QWidget#left {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f6f9fd, stop:1 #e9f0f8);
    border-right: 1px solid #c5d6e8; }}
QWidget#details {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f6f9fd, stop:1 #eef3f9); }}
QListWidget {{ background: transparent; border: none; outline: 0; color: #1e1e1e; }}
QListWidget::item {{ padding: 2px; }}
QListWidget::item:selected {{ background: #d6e8fb; color: black; }}
QListWidget::indicator {{ width: 12px; height: 12px; border: 1px solid #7f9db9; border-radius: 2px; background: white; }}
QListWidget::indicator:checked {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #7fbaf0, stop:1 #2f75c9);
    border-color: #2a5f9e; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 #f6f9fd, stop:1 #dbe7f5); }}
QCalendarWidget QToolButton {{ color: {TEXT}; font-weight: bold; }}
QCalendarWidget QAbstractItemView {{ background: white; selection-background-color: #3d8ee0;
    selection-color: white; outline: 0; }}
QLineEdit, QComboBox, QDateTimeEdit, QDateEdit, QPlainTextEdit {{ border: 1px solid #a7bdd6; border-radius: 2px;
    padding: 2px 4px; background: white; color: black; }}
QLabel {{ color: #1e1e1e; }}
QMenu {{ background: #f7f9fc; border: 1px solid #97a8c0; padding: 2px; }}
QMenu::item {{ padding: 4px 24px 4px 26px; }}
QMenu::item:selected {{ background: #d9e9fb; border: 1px solid #a8c8ec; border-radius: 2px; color: black; }}
QStatusBar {{ background: #e7eef7; color: {TEXT}; }}
"""


class Bus(QObject):
    done = Signal(object, object, object)


class Calendar(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon.fromTheme("office-calendar", QIcon.fromTheme("x-office-calendar")))
        self.resize(1240, 780)
        self.store = M.Store()
        self.current = None              # (event, occurrence start)
        self.mode = "week"
        self.day = date.today()
        self.bus = Bus()
        self.bus.done.connect(lambda cb, r, e: cb(r, e))

        w = QWidget()
        self.setCentralWidget(w)
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        bar = Bar()
        new_m = QMenu(self)
        new_m.addAction("Appointment", self.new_appointment, QKeySequence("Ctrl+N"))
        new_m.addAction("Task", self.new_task, QKeySequence("Ctrl+T"))
        new_m.addAction("Calendar…", self.new_calendar)
        bar.add("New Appointment", "appointment-new", self.new_appointment, tip="New appointment (Ctrl+N)")
        bar.add("New Task", "task-new", self.new_task, tip="New task (Ctrl+T)")
        self.delete_btn = bar.add("Delete", "edit-delete", self.delete, tip="Delete (Del)")
        bar.sep()
        bar.add("Today", "go-jump-today", self.today, tip="Go to today (Ctrl+Home)")
        bar.add("◀", "", lambda: self.step(-1), tip="Previous")
        bar.add("▶", "", lambda: self.step(1), tip="Next")
        self.view_btns = {}
        for key, label in (("day", "Day"), ("week", "Week"), ("month", "Month")):
            b = bar.add(label, "", lambda _=False, k=key: self.set_mode(k))
            b.setCheckable(True)
            self.view_btns[key] = b
        bar.sep()
        share = QMenu(self)
        share.addAction("Subscribe to a calendar…", self.subscribe)
        share.addAction("Import…", self.import_ics, QKeySequence("Ctrl+I"))
        share.addAction("Export…", self.export_ics)
        bar.add("Subscribe", "folder-remote", menu=share)
        bar.lay.addStretch(1)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(12)
        self.title.setFont(f)
        self.title.setStyleSheet(f"color: {TEXT}; padding-right: 10px;")
        bar.lay.addWidget(self.title)
        col.addWidget(bar)

        split = QSplitter(Qt.Horizontal, handleWidth=1, childrenCollapsible=False)
        left = QWidget(objectName="left")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.setSpacing(2)
        self.mini = QCalendarWidget(gridVisible=False, verticalHeaderFormat=QCalendarWidget.NoVerticalHeader)
        self.mini.setFirstDayOfWeek(Qt.Sunday)
        self.mini.setHorizontalHeaderFormat(QCalendarWidget.SingleLetterDayNames)
        self.mini.setFixedHeight(190)
        self.mini.clicked.connect(lambda qd: self.go(qd.toPython()))
        self.mini.currentPageChanged.connect(lambda *_: self.mark_mini())
        ll.addWidget(self.mini)
        ll.addWidget(section_label("Calendars"))
        self.cals = QListWidget()
        self.cals.itemClicked.connect(self.toggle_calendar)
        self.cals.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cals.customContextMenuRequested.connect(self.calendar_menu)
        ll.addWidget(self.cals, 1)
        ll.addWidget(section_label("Tasks"))
        self.tasks = QListWidget()
        self.tasks.itemChanged.connect(self.task_changed)
        self.tasks.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tasks.customContextMenuRequested.connect(self.task_menu)
        ll.addWidget(self.tasks, 1)
        self.task_add = QLineEdit(placeholderText="Click here to add a task")
        self.task_add.returnPressed.connect(self.add_task_inline)
        ll.addWidget(self.task_add)
        split.addWidget(left)

        self.views = QStackedWidget()
        self.grid = TimeGrid(self)
        self.scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        self.scroll.setWidget(self.grid)
        self.month = MonthGrid(self)
        self.views.addWidget(self.scroll)
        self.views.addWidget(self.month)
        for v in (self.grid, self.month):
            v.selected.connect(self.select)
            v.create.connect(self.create)
        self.grid.moved.connect(self.moved)
        self.month.open_day.connect(lambda d: (self.go(d), self.set_mode("day")))
        split.addWidget(self.views)
        self.details = Details(self)
        split.addWidget(self.details)
        split.setSizes([230, 720, 290])
        split.setStretchFactor(1, 1)
        col.addWidget(split, 1)

        for key, fn in (("Delete", self.delete), ("Ctrl+Home", self.today), ("Alt+Left", lambda: self.step(-1)),
                        ("Alt+Right", lambda: self.step(1)), ("Ctrl+Alt+1", lambda: self.set_mode("day")),
                        ("Ctrl+Alt+2", lambda: self.set_mode("week")), ("Ctrl+Alt+3", lambda: self.set_mode("month")),
                        ("Ctrl+N", self.new_appointment), ("Ctrl+T", self.new_task)):
            a = QAction(self, shortcut=QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
        self.ticker = QTimer(self, interval=60000, timeout=self.grid.update)
        self.ticker.start()
        self.set_mode("week")
        QTimer.singleShot(50, lambda: self.scroll.verticalScrollBar().setValue(int(HOUR * 7.5)))
        QTimer.singleShot(500, self.refresh_subscriptions)
        self.start_daemon()

    def run(self, fn, cb):
        def work():
            try:
                self.bus.done.emit(cb, fn(), None)
            except Exception as e:      # noqa: BLE001 - shown to the user
                self.bus.done.emit(cb, None, e)
        threading.Thread(target=work, daemon=True).start()

    def start_daemon(self):
        """Reminders come from a small background process that outlives this window."""
        if os.environ.get("HALCYON_CALENDAR_NO_DAEMON"):
            return
        subprocess.Popen([sys.executable, "-m", "halcyon_calendar.app", "--daemon"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ))

    # --- navigation
    def set_mode(self, mode):
        self.mode = mode
        for k, b in self.view_btns.items():
            b.setChecked(k == mode)
        self.refresh()

    def go(self, d):
        self.day = d
        self.refresh()

    def today(self):
        self.go(date.today())
        if self.mode != "month":
            self.scroll.verticalScrollBar().setValue(int(max(0, datetime.now().hour - 2) * HOUR))

    def step(self, n):
        if self.mode == "day":
            self.day += timedelta(days=n)
        elif self.mode == "week":
            self.day += timedelta(weeks=n)
        else:
            m = self.day.month - 1 + n
            self.day = self.day.replace(year=self.day.year + m // 12, month=m % 12 + 1, day=1)
        self.refresh()

    def refresh(self, keep_details=False):
        if self.mode == "month":
            self.month.month = self.day.replace(day=1)
            self.views.setCurrentWidget(self.month)
            self.title.setText(self.day.strftime("%B %Y"))
            self.month.update()
        else:
            self.grid.days = 1 if self.mode == "day" else 7
            self.grid.first = self.day if self.mode == "day" else week_start(self.day)
            self.views.setCurrentWidget(self.scroll)
            if self.mode == "day":
                self.title.setText(self.day.strftime("%A, %B %-d, %Y"))
            else:
                a = self.grid.first
                b = a + timedelta(days=6)
                self.title.setText(f"{a:%B %-d} – {b:%B %-d, %Y}")
            self.grid.update()
        self.mini.setSelectedDate(QDate(self.day))
        self.mark_mini()
        self.fill_calendars()
        self.fill_tasks()
        if not keep_details:
            self.details.show_event(self.current[0] if self.current else None)
        self.delete_btn.setEnabled(self.current is not None)

    def mark_mini(self):
        """Bold the days that have something on them, as Vista's mini calendar did."""
        y, m = self.mini.yearShown(), self.mini.monthShown()
        first = date(y, m, 1)
        self.mini.setDateTextFormat(QDate(), QTextCharFormat())
        busy = QTextCharFormat()
        busy.setFontWeight(QFont.Bold)
        busy.setForeground(QColor(TEXT))
        start = datetime.combine(first - timedelta(days=7), datetime.min.time())
        for e, s, f in self.store.between(start, start + timedelta(days=50)):
            d = s.date()
            while d <= (f - timedelta(seconds=1)).date():
                self.mini.setDateTextFormat(QDate(d), busy)
                d += timedelta(days=1)
        tf = self.mini.dateTextFormat(QDate(date.today()))
        tf.setBackground(QColor("#f7d97a"))
        self.mini.setDateTextFormat(QDate(date.today()), tf)

    # --- appointments
    def select(self, e, s):
        self.current = (e, s) if e is not None else None
        self.details.show_event(e)
        self.delete_btn.setEnabled(e is not None)
        self.grid.update()
        self.month.update()

    def create(self, s, f, allday):
        cal = next((c["id"] for c in self.store.calendars if not c.get("url") and c.get("visible", True)),
                   next((c["id"] for c in self.store.calendars if not c.get("url")), None))
        if cal is None:
            cal = self.store.add_calendar("Personal")["id"]
        e = self.store.new_event(s, f, allday, cal)
        self.select(e, M.parse_iso(e["start"]))
        self.refresh(keep_details=True)
        self.details.title.setFocus()
        self.details.title.selectAll()

    def new_appointment(self):
        now = datetime.now()
        d = self.day if self.mode != "month" or self.day.month == date.today().month else self.day
        s = datetime.combine(d, now.time()).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        self.create(s, s + timedelta(hours=1), False)

    def moved(self, e, s, f):
        e["start"], e["end"] = M.iso(s), M.iso(f)
        self.store.update(e)
        self.select(e, s)
        self.refresh(keep_details=True)
        self.details.show_event(e)

    def delete(self):
        if not self.current:
            return
        e, s = self.current
        if self.store.readonly(e):
            QMessageBox.information(self, APP, "Appointments from a subscribed calendar can't be deleted here.")
            return
        if e.get("repeat", "none") != "none":
            box = QMessageBox(QMessageBox.Question, APP, f"“{e['title']}” repeats. Delete just this one, or the "
                              "whole series?", parent=self)
            one = box.addButton("Delete this occurrence", QMessageBox.AcceptRole)
            box.addButton("Delete the series", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is box.button(QMessageBox.Cancel):
                return
            if box.clickedButton() is one:
                e.setdefault("exdates", []).append(M.iso(s.date()) if e.get("allday") else M.iso(s))
                self.store.update(e)
                self.current = None
                self.refresh()
                return
        self.store.delete(e["id"])
        self.current = None
        self.refresh()

    # --- calendars
    def fill_calendars(self):
        self.cals.blockSignals(True)
        self.cals.clear()
        for c in self.store.calendars:
            it = QListWidgetItem(swatch(c["color"], c.get("visible", True)),
                                 c["name"] + ("  ⟳" if c.get("url") else ""))
            it.setData(Qt.UserRole, c["id"])
            it.setToolTip(c.get("url", "") or "Click to show or hide")
            self.cals.addItem(it)
        self.cals.blockSignals(False)

    def toggle_calendar(self, it):
        c = self.store.calendar(it.data(Qt.UserRole))
        c["visible"] = not c.get("visible", True)
        self.store.save()
        self.refresh()

    def calendar_menu(self, pos):
        it = self.cals.itemAt(pos)
        m = QMenu(self)
        if it:
            c = self.store.calendar(it.data(Qt.UserRole))
            m.addAction("Rename…", lambda: self.rename_calendar(c))
            m.addAction("Color…", lambda: self.color_calendar(c))
            if c.get("url"):
                m.addAction("Refresh now", lambda: self.refresh_subscriptions(force=c["id"]))
            m.addAction("Export…", lambda: self.export_ics(c))
            m.addSeparator()
            m.addAction("Delete calendar", lambda: self.delete_calendar(c))
            m.addSeparator()
        m.addAction("New calendar…", self.new_calendar)
        m.addAction("Subscribe…", self.subscribe)
        m.exec(self.cals.viewport().mapToGlobal(pos))

    def new_calendar(self):
        name, ok = QInputDialog.getText(self, "New Calendar", "Name:")
        if ok and name.strip():
            self.store.add_calendar(name.strip())
            self.refresh()

    def rename_calendar(self, c):
        name, ok = QInputDialog.getText(self, "Rename Calendar", "Name:", text=c["name"])
        if ok and name.strip():
            c["name"] = name.strip()
            self.store.save()
            self.refresh()

    def color_calendar(self, c):
        col = QColorDialog.getColor(QColor(c["color"]), self, "Calendar Color")
        if col.isValid():
            c["color"] = col.name()
            self.store.save()
            self.refresh()

    def delete_calendar(self, c):
        n = sum(1 for e in self.store.events if e["calendar"] == c["id"])
        if QMessageBox.question(self, APP, f"Delete the calendar “{c['name']}”"
                                + (f" and its {n} appointment(s)?" if n else "?")) == QMessageBox.Yes:
            self.store.remove_calendar(c["id"])
            self.current = None
            self.refresh()

    def subscribe(self, url=None):
        if not url:
            url, ok = QInputDialog.getText(self, "Subscribe to a Calendar",
                                           "Calendar address (webcal:// or https://… .ics):\n"
                                           "Google: Settings → your calendar → “Secret address in iCal format”",
                                           text="webcal://")
            if not ok or len(url.strip()) < 12:
                return
        url = url.strip()
        name = Path(url.split("?")[0]).stem.replace("_", " ").replace("-", " ").title() or "Subscribed"
        c = self.store.add_calendar(name if len(name) < 40 else "Subscribed", url)
        self.statusBar().showMessage("Subscribing…")
        self.run(lambda: M.refresh_subscription(self.store, c),
                 lambda n, err: self.subscribed(c, n, err))

    def subscribed(self, c, n, err):
        if err:
            self.store.remove_calendar(c["id"])
            QMessageBox.warning(self, APP, f"Couldn't subscribe to that calendar.\n\n{err}")
            self.statusBar().clearMessage()
        else:
            self.statusBar().showMessage(f"Subscribed: {n} appointment(s).", 6000)
        self.refresh()

    def refresh_subscriptions(self, force=None):
        stale = [c for c in self.store.calendars if c.get("url")
                 and (c["id"] == force or time.time() - c.get("refreshed", 0) > 6 * 3600)]
        for c in stale:
            self.run(lambda c=c: M.refresh_subscription(self.store, c),
                     lambda n, err: (self.refresh(keep_details=True),
                                     err and self.statusBar().showMessage(f"Couldn't refresh a calendar: {err}", 8000)))

    def import_ics(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Import", str(Path.home()), "iCalendar (*.ics *.ical *.ifb)")
            if not path:
                return
        try:
            text = Path(path).read_text(errors="replace")
        except OSError as e:
            QMessageBox.warning(self, APP, str(e))
            return
        local = [c for c in self.store.calendars if not c.get("url")]
        names = [c["name"] for c in local] + ["New calendar…"]
        pick, ok = QInputDialog.getItem(self, "Import", "Add the appointments to:", names, 0, False)
        if not ok:
            return
        if pick == "New calendar…":
            cal = self.store.add_calendar(Path(path).stem)
        else:
            cal = local[names.index(pick)]
        events = M.parse_ics(text, cal["id"])
        have = {e["id"] for e in self.store.events}
        new = [e for e in events if e["id"] not in have]
        self.store.events += new
        self.store.save()
        if new:
            self.go(M.parse_iso(new[0]["start"]).date())
        QMessageBox.information(self, APP, f"Imported {len(new)} appointment(s) into “{cal['name']}”.")

    def export_ics(self, cal=None):
        events = [e for e in self.store.events if cal is None or e["calendar"] == cal["id"]]
        name = cal["name"] if cal else "Calendar"
        path, _ = QFileDialog.getSaveFileName(self, "Export", str(Path.home() / f"{name}.ics"), "iCalendar (*.ics)")
        if path:
            Path(path).write_text(M.to_ics(events, name))

    # --- tasks
    def fill_tasks(self):
        self.tasks.blockSignals(True)
        self.tasks.clear()
        for t in sorted(self.store.tasks, key=lambda t: (t.get("done", False), t.get("due") or "9999", t["title"])):
            label = t["title"] + (f"   (due {M.parse_iso(t['due']):%-m/%-d})" if t.get("due") else "")
            it = QListWidgetItem(label)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if t.get("done") else Qt.Unchecked)
            it.setData(Qt.UserRole, t["id"])
            if t.get("done"):
                f = it.font()
                f.setStrikeOut(True)
                it.setFont(f)
                it.setForeground(QColor(GRAY))
            elif t.get("due") and M.parse_iso(t["due"]).date() < date.today():
                it.setForeground(QColor("#c6282d"))
            self.tasks.addItem(it)
        self.tasks.blockSignals(False)

    def add_task_inline(self):
        title = self.task_add.text().strip()
        if title:
            self.store.tasks.append({"id": os.urandom(6).hex(), "title": title, "done": False, "due": ""})
            self.store.save()
            self.task_add.clear()
            self.fill_tasks()

    def new_task(self):
        self.task_add.setFocus()

    def task_changed(self, it):
        t = next((t for t in self.store.tasks if t["id"] == it.data(Qt.UserRole)), None)
        if t:
            t["done"] = it.checkState() == Qt.Checked
            self.store.save()
            QTimer.singleShot(0, self.fill_tasks)

    def task_menu(self, pos):
        it = self.tasks.itemAt(pos)
        if not it:
            return
        t = next((t for t in self.store.tasks if t["id"] == it.data(Qt.UserRole)), None)
        m = QMenu(self)
        m.addAction("Rename…", lambda: self.task_rename(t))
        m.addAction("Set due date…", lambda: self.task_due(t))
        if t.get("due"):
            m.addAction("Clear due date", lambda: (t.update(due=""), self.store.save(), self.fill_tasks()))
        m.addSeparator()
        m.addAction("Delete task", lambda: (self.store.tasks.remove(t), self.store.save(), self.fill_tasks()))
        m.exec(self.tasks.viewport().mapToGlobal(pos))

    def task_rename(self, t):
        name, ok = QInputDialog.getText(self, "Task", "Task:", text=t["title"])
        if ok and name.strip():
            t["title"] = name.strip()
            self.store.save()
            self.fill_tasks()

    def task_due(self, t):
        text, ok = QInputDialog.getText(self, "Due Date", "Due (M/D/YYYY):",
                                        text=(M.parse_iso(t["due"]).strftime("%-m/%-d/%Y") if t.get("due")
                                              else self.day.strftime("%-m/%-d/%Y")))
        if ok:
            try:
                t["due"] = M.iso(datetime.strptime(text.strip(), "%m/%d/%Y"))
            except ValueError:
                return
            self.store.save()
            self.fill_tasks()


# ----------------------------------------------------------------- daemon ---
def daemon():
    """Reminders and subscription refreshes in the background (one per session)."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                                  # already running
    last_refresh = 0.0
    while True:
        store = M.Store()
        for e, s in M.due_reminders(store):
            when = "All day" if e.get("allday") else s.strftime("%-I:%M %p")
            body = f"{when}, {s:%A, %B %-d}" + (f"\n{e['location']}" if e.get("location") else "")
            try:
                subprocess.Popen(["notify-send", "--app-name", "Halcyon Calendar", "--icon", "appointment-soon",
                                  "--urgency", "critical", "--hint", "string:sound-name:alarm-clock-elapsed",
                                  f"Reminder: {e['title']}", body], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                pass
        if time.time() - last_refresh > 6 * 3600:
            last_refresh = time.time()
            for c in store.calendars:
                if c.get("url") and time.time() - c.get("refreshed", 0) > 6 * 3600:
                    try:
                        M.refresh_subscription(store, c)
                    except Exception:
                        pass
        time.sleep(30)


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
    if "--daemon" in sys.argv:
        return daemon()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("halcyon-calendar")
    app.setDesktopFileName("halcyon-calendar")
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
    w = Calendar()
    w.show()
    if "--date" in sys.argv:                  # halcyon-calendar --date YYYY-MM-DD (from Halcyon Search)
        try:
            d = date.fromisoformat(sys.argv[sys.argv.index("--date") + 1][:10])
            w.go(d)
            w.set_mode("day")
        except (IndexError, ValueError):
            pass
    arg = next((a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--")
                and sys.argv[i - 1] != "--date"), None)
    if arg:
        if arg.startswith(("webcal:", "webcals:")) or (arg.startswith("http") and ".ics" in arg):
            QTimer.singleShot(300, lambda: w.subscribe(arg))
        elif Path(arg.removeprefix("file://")).exists():
            QTimer.singleShot(300, lambda: w.import_ics(arg.removeprefix("file://")))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
