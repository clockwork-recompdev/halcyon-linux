"""Halcyon Calendar's data: calendars, appointments (with repeats and
reminders) and tasks, kept in one JSON file; iCalendar (.ics) import,
export and subscriptions."""
import json
import os
import re
import time
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:            # pragma: no cover
    ZoneInfo = None

STORE = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "calendar.json"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "halcyon" / "calendar-reminders.json"
COLORS = ["#3a7bd5", "#2e9d4f", "#d9822b", "#b03a9a", "#c8331f", "#178c8c", "#6a5acd", "#8a6d3b"]
REPEATS = {"none": "Does not repeat", "daily": "Daily", "weekdays": "Every weekday", "weekly": "Weekly",
           "monthly": "Monthly", "yearly": "Yearly"}
REMINDERS = [(-1, "None"), (0, "At start time"), (5, "5 minutes before"), (10, "10 minutes before"), (15, "15 minutes before"),
             (30, "30 minutes before"), (60, "1 hour before"), (120, "2 hours before"), (1440, "1 day before"),
             (2880, "2 days before"), (10080, "1 week before")]


def iso(dt):
    return dt.isoformat(timespec="minutes") if isinstance(dt, datetime) else dt.isoformat()


def parse_iso(s):
    return datetime.fromisoformat(s)


class Store:
    def __init__(self, path=STORE):
        self.path = Path(path)
        self.calendars, self.events, self.tasks = [], [], []
        self.load()

    def load(self):
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            d = {}
        self.calendars = d.get("calendars") or [{"id": "personal", "name": "Personal", "color": COLORS[0],
                                                 "visible": True}]
        self.events = d.get("events", [])
        self.tasks = d.get("tasks", [])

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"calendars": self.calendars, "events": self.events, "tasks": self.tasks},
                                  indent=1, ensure_ascii=False))
        tmp.replace(self.path)

    def calendar(self, cid):
        return next((c for c in self.calendars if c["id"] == cid), self.calendars[0])

    def add_calendar(self, name, url=None):
        c = {"id": uuid.uuid4().hex[:10], "name": name, "color": COLORS[len(self.calendars) % len(COLORS)],
             "visible": True}
        if url:
            c["url"] = url
        self.calendars.append(c)
        self.save()
        return c

    def remove_calendar(self, cid):
        self.calendars = [c for c in self.calendars if c["id"] != cid]
        self.events = [e for e in self.events if e["calendar"] != cid]
        if not self.calendars:
            self.calendars = [{"id": "personal", "name": "Personal", "color": COLORS[0], "visible": True}]
        self.save()

    def new_event(self, start, end, allday=False, calendar=None, title="New Appointment"):
        e = {"id": uuid.uuid4().hex, "calendar": calendar or self.calendars[0]["id"], "title": title,
             "location": "", "start": iso(start), "end": iso(end), "allday": allday, "repeat": "none",
             "until": "", "reminder": -1, "notes": "", "url": ""}
        self.events.append(e)
        self.save()
        return e

    def update(self, e):
        self.events = [e if x["id"] == e["id"] else x for x in self.events]
        self.save()

    def delete(self, eid):
        self.events = [e for e in self.events if e["id"] != eid]
        self.save()

    def readonly(self, e):
        return bool(self.calendar(e["calendar"]).get("url"))

    # --- occurrences
    def between(self, start, end, visible_only=True):
        """[(event, occurrence start, occurrence end)] overlapping [start, end)."""
        shown = {c["id"] for c in self.calendars if c.get("visible", True) or not visible_only}
        out = []
        for e in self.events:
            if e["calendar"] not in shown:
                continue
            for s, f in occurrences(e, start, end):
                out.append((e, s, f))
        out.sort(key=lambda x: (x[1], not x[0].get("allday")))
        return out


def occurrences(e, start, end):
    s0, f0 = parse_iso(e["start"]), parse_iso(e["end"])
    if e.get("allday"):
        s0 = datetime.combine(s0.date(), datetime.min.time())
        f0 = datetime.combine(f0.date(), datetime.min.time())
        if f0 <= s0:
            f0 = s0 + timedelta(days=1)
    dur = f0 - s0
    rep = e.get("repeat", "none")
    until = parse_iso(e["until"]) if e.get("until") else None
    if rep == "none":
        return [(s0, f0)] if s0 < end and f0 > start else []
    out, s, n = [], s0, 0
    exdates = set(e.get("exdates", []))
    count = e.get("count")
    # jump close to the window for long-running repeats
    if rep in ("daily", "weekly", "weekdays") and s0 < start - timedelta(days=7):
        step = 7 if rep == "weekly" else 1
        k = max(0, (start - s0).days // step - 1)
        s = s0 + timedelta(days=k * step)
        n = k if rep != "weekdays" else 0
    while s < end and n < 5000:
        if until and s.date() > until.date():
            break
        if count and n >= count:
            break
        if not (rep == "weekdays" and s.weekday() >= 5):
            if s + dur > start and iso(s.date()) not in exdates and iso(s) not in exdates:
                out.append((s, s + dur))
            n += 1
        s = _advance(s, s0, rep, n)
    return out


def _advance(s, s0, rep, n):
    if rep in ("daily", "weekdays"):
        return s + timedelta(days=1)
    if rep == "weekly":
        return s + timedelta(weeks=1)
    if rep == "monthly":
        m = s.month + 1
        y = s.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        day = s0.day
        while True:
            try:
                return s.replace(year=y, month=m, day=day)
            except ValueError:            # 31st in a short month: skip that month, as Outlook does
                m += 1
                if m > 12:
                    m, y = 1, y + 1
    if rep == "yearly":
        try:
            return s.replace(year=s.year + 1)
        except ValueError:                # Feb 29
            return s.replace(year=s.year + 1, day=28)
    return s + timedelta(days=36500)


# ------------------------------------------------------------------ reminders ---
def due_reminders(store, now=None, window=timedelta(minutes=10)):
    """[(event, occurrence start)] whose reminder time is in (now - window, now] and not yet shown."""
    now = now or datetime.now()
    try:
        fired = set(json.loads(STATE.read_text()))
    except (OSError, ValueError):
        fired = set()
    out = []
    for e, s, _ in store.between(now - timedelta(days=1), now + timedelta(days=8), visible_only=False):
        mins = e.get("reminder", -1)
        if mins is None or mins < 0:
            continue
        at = (s if not e.get("allday") else s.replace(hour=9)) - timedelta(minutes=mins)
        key = f"{e['id']}@{iso(s)}"
        if now - window < at <= now and key not in fired:
            out.append((e, s))
            fired.add(key)
    if out:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(sorted(fired)[-2000:]))
    return out


# ------------------------------------------------------------------- iCalendar ---
def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text)


def _unesc(v):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _esc(v):
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _parse_dt(value, params):
    """(local naive datetime, all-day?)"""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        return datetime.strptime(v[:8], "%Y%m%d"), True
    dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None), False
    tz = params.get("TZID")
    if tz and ZoneInfo:
        try:
            return dt.replace(tzinfo=ZoneInfo(_tz_name(tz))).astimezone().replace(tzinfo=None), False
        except Exception:
            pass
    return dt, False


WINDOWS_TZ = {"Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
              "Mountain Standard Time": "America/Denver", "Pacific Standard Time": "America/Los_Angeles",
              "GMT Standard Time": "Europe/London", "W. Europe Standard Time": "Europe/Berlin",
              "Romance Standard Time": "Europe/Paris", "Tokyo Standard Time": "Asia/Tokyo",
              "AUS Eastern Standard Time": "Australia/Sydney", "UTC": "UTC"}


def _tz_name(tz):
    tz = tz.strip('"')
    return WINDOWS_TZ.get(tz, tz)


def _duration(v):
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return sign * timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse_ics(text, calendar_id):
    """Events from an iCalendar file, in Halcyon's format."""
    events, cur, alarm = [], None, None
    for line in _unfold(text).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        name, *ps = key.split(";")
        name = name.upper()
        params = {}
        for p in ps:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.upper()] = v
        if name == "BEGIN" and value.strip().upper() == "VEVENT":
            cur = {"rrule": {}}
        elif name == "BEGIN" and value.strip().upper() == "VALARM" and cur is not None:
            alarm = {}
        elif name == "END" and value.strip().upper() == "VALARM":
            if cur is not None and alarm and "trigger" in alarm:
                cur["reminder"] = alarm["trigger"]
            alarm = None
        elif name == "END" and value.strip().upper() == "VEVENT" and cur is not None:
            e = _finish(cur, calendar_id)
            if e:
                events.append(e)
            cur = None
        elif alarm is not None:
            if name == "TRIGGER":
                d = _duration(value)
                if d is not None and d <= timedelta(0):
                    alarm["trigger"] = int(-d.total_seconds() // 60)
        elif cur is not None:
            if name in ("DTSTART", "DTEND"):
                cur[name] = _parse_dt(value, params)
            elif name == "DURATION":
                cur["DURATION"] = _duration(value)
            elif name in ("SUMMARY", "LOCATION", "DESCRIPTION", "URL", "UID"):
                cur[name] = _unesc(value)
            elif name == "RRULE":
                cur["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
            elif name == "EXDATE":
                for v in value.split(","):
                    dt, allday = _parse_dt(v, params)
                    cur.setdefault("exdates", []).append(iso(dt.date()) if allday else iso(dt))
    return events


def _finish(c, calendar_id):
    if "DTSTART" not in c:
        return None
    s, allday = c["DTSTART"]
    if "DTEND" in c:
        f = c["DTEND"][0]
    elif c.get("DURATION"):
        f = s + c["DURATION"]
    else:
        f = s + (timedelta(days=1) if allday else timedelta(hours=1))
    r = c["rrule"]
    freq = r.get("FREQ", "").upper()
    repeat = "none"
    if freq == "DAILY":
        repeat = "daily"
    elif freq == "WEEKLY":
        days = r.get("BYDAY", "")
        repeat = "weekdays" if set(days.split(",")) == {"MO", "TU", "WE", "TH", "FR"} else "weekly"
    elif freq == "MONTHLY":
        repeat = "monthly"
    elif freq == "YEARLY":
        repeat = "yearly"
    until = ""
    if r.get("UNTIL"):
        try:
            until = iso(_parse_dt(r["UNTIL"], {})[0])
        except ValueError:
            pass
    e = {"id": (c.get("UID") or uuid.uuid4().hex) + "@" + calendar_id, "calendar": calendar_id,
         "title": c.get("SUMMARY", "(no title)"), "location": c.get("LOCATION", ""), "start": iso(s),
         "end": iso(f), "allday": allday, "repeat": repeat, "until": until, "reminder": c.get("reminder", -1),
         "notes": c.get("DESCRIPTION", ""), "url": c.get("URL", "")}
    if r.get("COUNT", "").isdigit():
        e["count"] = int(r["COUNT"])
    if c.get("exdates"):
        e["exdates"] = c["exdates"]
    return e


def to_ics(events, name="Halcyon Calendar"):
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Halcyon//Calendar//EN", f"X-WR-CALNAME:{_esc(name)}"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for e in events:
        s, f = parse_iso(e["start"]), parse_iso(e["end"])
        out += ["BEGIN:VEVENT", f"UID:{e['id'].split('@')[0]}@halcyon", f"DTSTAMP:{stamp}"]
        if e.get("allday"):
            out += [f"DTSTART;VALUE=DATE:{s:%Y%m%d}", f"DTEND;VALUE=DATE:{max(f, s + timedelta(days=1)):%Y%m%d}"]
        else:
            out += [f"DTSTART:{s:%Y%m%dT%H%M%S}", f"DTEND:{f:%Y%m%dT%H%M%S}"]
        out.append("SUMMARY:" + _esc(e["title"]))
        if e.get("location"):
            out.append("LOCATION:" + _esc(e["location"]))
        if e.get("notes"):
            out.append("DESCRIPTION:" + _esc(e["notes"]))
        rep = e.get("repeat", "none")
        if rep != "none":
            rule = {"daily": "FREQ=DAILY", "weekdays": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekly": "FREQ=WEEKLY",
                    "monthly": "FREQ=MONTHLY", "yearly": "FREQ=YEARLY"}[rep]
            if e.get("until"):
                rule += f";UNTIL={parse_iso(e['until']):%Y%m%d}"
            elif e.get("count"):
                rule += f";COUNT={e['count']}"
            out.append("RRULE:" + rule)
        if e.get("reminder", -1) >= 0:
            out += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", f"TRIGGER:-PT{e['reminder']}M",
                    "END:VALARM"]
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    lines = []
    for line in out:                 # fold at 75 octets
        b = line.encode()
        while len(b) > 75:
            cut = 75
            while (b[cut] & 0xC0) == 0x80:
                cut -= 1
            lines.append(b[:cut].decode())
            b = b" " + b[cut:]
        lines.append(b.decode())
    return "\r\n".join(lines) + "\r\n"


def fetch_ics(url):
    url = re.sub(r"^webcals?://", "https://", url.strip())
    req = urllib.request.Request(url, headers={"User-Agent": "Halcyon Calendar"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def refresh_subscription(store, cal):
    text = fetch_ics(cal["url"])
    events = parse_ics(text, cal["id"])
    store.events = [e for e in store.events if e["calendar"] != cal["id"]] + events
    cal["refreshed"] = time.time()
    store.save()
    return len(events)
