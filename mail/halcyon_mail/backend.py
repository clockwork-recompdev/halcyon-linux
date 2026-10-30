"""Halcyon Mail's plumbing: accounts (server settings found automatically,
passwords in the system keyring), IMAP and SMTP, and turning messages into
something the reading pane can show."""
import base64
import email
import email.policy
import html
import imaplib
import json
import os
import re
import smtplib
import ssl
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parsedate_to_datetime
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "halcyon" / "mail.json"
SECRETS = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "mail-secrets.json"
POLICY = email.policy.default

# Big providers, so the common case needs no lookup. (imap host, port, security), (smtp ...)
PROVIDERS = {
    "gmail.com": (("imap.gmail.com", 993, "ssl"), ("smtp.gmail.com", 465, "ssl")),
    "googlemail.com": (("imap.gmail.com", 993, "ssl"), ("smtp.gmail.com", 465, "ssl")),
    "outlook.com": (("outlook.office365.com", 993, "ssl"), ("smtp-mail.outlook.com", 587, "starttls")),
    "hotmail.com": (("outlook.office365.com", 993, "ssl"), ("smtp-mail.outlook.com", 587, "starttls")),
    "live.com": (("outlook.office365.com", 993, "ssl"), ("smtp-mail.outlook.com", 587, "starttls")),
    "msn.com": (("outlook.office365.com", 993, "ssl"), ("smtp-mail.outlook.com", 587, "starttls")),
    "yahoo.com": (("imap.mail.yahoo.com", 993, "ssl"), ("smtp.mail.yahoo.com", 465, "ssl")),
    "icloud.com": (("imap.mail.me.com", 993, "ssl"), ("smtp.mail.me.com", 587, "starttls")),
    "me.com": (("imap.mail.me.com", 993, "ssl"), ("smtp.mail.me.com", 587, "starttls")),
    "mac.com": (("imap.mail.me.com", 993, "ssl"), ("smtp.mail.me.com", 587, "starttls")),
    "aol.com": (("imap.aol.com", 993, "ssl"), ("smtp.aol.com", 465, "ssl")),
    "fastmail.com": (("imap.fastmail.com", 993, "ssl"), ("smtp.fastmail.com", 465, "ssl")),
    "gmx.com": (("imap.gmx.com", 993, "ssl"), ("mail.gmx.com", 587, "starttls")),
    "zoho.com": (("imap.zoho.com", 993, "ssl"), ("smtp.zoho.com", 465, "ssl")),
}
# These refuse normal passwords from mail apps; they need an "app password".
APP_PASSWORD = {
    "gmail.com": "https://myaccount.google.com/apppasswords",
    "googlemail.com": "https://myaccount.google.com/apppasswords",
    "yahoo.com": "https://login.yahoo.com/account/security",
    "icloud.com": "https://account.apple.com/account/manage",
    "me.com": "https://account.apple.com/account/manage",
    "mac.com": "https://account.apple.com/account/manage",
    "aol.com": "https://login.aol.com/account/security",
    "outlook.com": "https://account.live.com/proofs/AppPassword",
    "hotmail.com": "https://account.live.com/proofs/AppPassword",
    "live.com": "https://account.live.com/proofs/AppPassword",
    "msn.com": "https://account.live.com/proofs/AppPassword",
}


def load_config():
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("accounts", [])
    cfg.setdefault("show_pictures_from", [])
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1))


# --------------------------------------------------------------- passwords ---
def set_password(acct_id, label, password):
    """System keyring (Secret Service: KWallet or GNOME Keyring); a private file if there is none."""
    try:
        r = subprocess.run(["secret-tool", "store", "--label", f"Halcyon Mail: {label}",
                            "service", "halcyon-mail", "account", acct_id],
                           input=password, text=True, capture_output=True, timeout=20)
        if r.returncode == 0:
            _file_secret(acct_id, None)
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    _file_secret(acct_id, password)


def get_password(acct_id):
    try:
        r = subprocess.run(["secret-tool", "lookup", "service", "halcyon-mail", "account", acct_id],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout:
            return r.stdout.rstrip("\n")
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        return json.loads(SECRETS.read_text()).get(acct_id)
    except (OSError, ValueError):
        return None


def forget_password(acct_id):
    try:
        subprocess.run(["secret-tool", "clear", "service", "halcyon-mail", "account", acct_id],
                       capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass
    _file_secret(acct_id, None)


def _file_secret(acct_id, password):
    try:
        data = json.loads(SECRETS.read_text())
    except (OSError, ValueError):
        data = {}
    if password is None:
        if acct_id not in data:
            return
        data.pop(acct_id)
    else:
        data[acct_id] = password
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(SECRETS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)


# ------------------------------------------------------- server discovery ---
def autoconfig(address):
    """{'imap': {...}, 'smtp': {...}} for an address: known providers, then Thunderbird's
    provider database (the same one Thunderbird uses), then a guess."""
    domain = address.rsplit("@", 1)[-1].lower().strip()
    if domain in PROVIDERS:
        (ih, ip, isec), (sh, sp, ssec) = PROVIDERS[domain]
        return {"imap": {"host": ih, "port": ip, "security": isec},
                "smtp": {"host": sh, "port": sp, "security": ssec}, "source": "known"}
    for url in (f"https://autoconfig.thunderbird.net/v1.1/{domain}",
                f"https://autoconfig.{domain}/mail/config-v1.1.xml?emailaddress={address}"):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Halcyon Mail"}),
                                        timeout=8) as r:
                found = _parse_ispdb(r.read())
            if found:
                found["source"] = "ispdb"
                return found
        except Exception:
            continue
    return {"imap": {"host": f"imap.{domain}", "port": 993, "security": "ssl"},
            "smtp": {"host": f"smtp.{domain}", "port": 587, "security": "starttls"}, "source": "guess"}


def _parse_ispdb(data):
    root = ET.fromstring(data)
    sec = {"SSL": "ssl", "STARTTLS": "starttls", "plain": "none"}
    out = {}
    for kind, tag in (("imap", "incomingServer"), ("smtp", "outgoingServer")):
        for s in root.iter(tag):
            if s.get("type") != kind:
                continue
            out[kind] = {"host": s.findtext("hostname"), "port": int(s.findtext("port") or 0),
                         "security": sec.get(s.findtext("socketType") or "", "ssl")}
            break
    return out if "imap" in out and "smtp" in out else None


def app_password_page(address):
    return APP_PASSWORD.get(address.rsplit("@", 1)[-1].lower())


# -------------------------------------------------------------------- IMAP ---
def decode_mutf7(s):
    """IMAP folder names are modified UTF-7 ('Envoy&AOk-s' -> 'Envoyés')."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "&":
            j = s.find("-", i)
            if j < 0:
                return s
            if j == i + 1:
                out.append("&")
            else:
                b64 = s[i + 1:j].replace(",", "/")
                try:
                    out.append(base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be"))
                except Exception:
                    out.append(s[i:j + 1])
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def quote(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


SPECIAL_NAMES = {
    "inbox": ("inbox", "Inbox"), "sent": ("sent", "Sent items"), "sent items": ("sent", "Sent items"),
    "sent messages": ("sent", "Sent items"), "sent mail": ("sent", "Sent items"),
    "drafts": ("drafts", "Drafts"), "draft": ("drafts", "Drafts"),
    "trash": ("trash", "Deleted items"), "deleted items": ("trash", "Deleted items"),
    "deleted messages": ("trash", "Deleted items"), "bin": ("trash", "Deleted items"),
    "junk": ("junk", "Junk e-mail"), "spam": ("junk", "Junk e-mail"), "junk e-mail": ("junk", "Junk e-mail"),
    "junk email": ("junk", "Junk e-mail"), "bulk mail": ("junk", "Junk e-mail"),
    "archive": ("archive", "Archive"), "outbox": ("outbox", "Outbox"),
}
SPECIAL_FLAGS = {"\\sent": "sent", "\\drafts": "drafts", "\\trash": "trash", "\\junk": "junk",
                 "\\archive": "archive", "\\all": "all", "\\flagged": "flaggedview"}
ORDER = ["inbox", "drafts", "sent", "outbox", "junk", "trash", "archive"]


class Imap:
    def __init__(self, acct, password):
        self.acct, self.password = acct, password
        self.conn = None
        self.selected = None

    def connect(self):
        s = self.acct["imap"]
        ctx = ssl.create_default_context()
        if s["security"] == "ssl":
            c = imaplib.IMAP4_SSL(s["host"], int(s["port"]), ssl_context=ctx, timeout=30)
        else:
            c = imaplib.IMAP4(s["host"], int(s["port"]), timeout=30)
            if s["security"] == "starttls":
                c.starttls(ssl_context=ctx)
        c.login(s.get("user") or self.acct["email"], self.password)
        self.conn, self.selected = c, None
        return c

    def close(self):
        try:
            if self.conn:
                self.conn.logout()
        except Exception:
            pass
        self.conn = None

    def call(self, fn):
        """Run fn(conn), reconnecting once if the server dropped us."""
        for attempt in (0, 1):
            try:
                if not self.conn:
                    self.connect()
                return fn(self.conn)
            except (imaplib.IMAP4.abort, OSError, EOFError):
                self.conn = None
                if attempt:
                    raise

    def _check(self, typ, data, what):
        if typ != "OK":
            raise RuntimeError(f"{what}: {(data[0] or b'').decode(errors='replace') if data else typ}")
        return data

    def select(self, folder, readonly=False):
        def go(c):
            if self.selected != (folder, readonly):
                self._check(*c.select(quote(folder), readonly=readonly), f"Couldn't open {folder}")
                self.selected = (folder, readonly)
        self.call(go)

    def folders(self):
        """[{name (raw), display, kind, delim}] in Windows Live Mail order."""
        def go(c):
            typ, data = c.list()
            return self._check(typ, data, "Couldn't list folders")
        raw = self.call(go)
        lines, out = [], []
        for item in raw:
            if isinstance(item, tuple):
                lines.append(item[0] + b' "' + item[1].replace(b'"', b'\\"') + b'"')
            elif item:
                lines.append(item)
        for line in lines:
            m = re.match(rb'\((?P<flags>[^)]*)\) (?P<delim>"(?:[^"\\]|\\.)*"|NIL) (?P<name>.+)$', line)
            if not m:
                continue
            flags = m.group("flags").decode(errors="replace").lower().split()
            if "\\noselect" in flags or "\\nonexistent" in flags:
                continue
            name = m.group("name").decode(errors="replace").strip()
            if name.startswith('"'):
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            delim = m.group("delim").decode().strip('"') if m.group("delim") != b"NIL" else ""
            display = decode_mutf7(name)
            leaf = display.split(delim)[-1] if delim else display
            kind = next((SPECIAL_FLAGS[f] for f in flags if f in SPECIAL_FLAGS), None)
            if name.upper() == "INBOX":
                kind = "inbox"
            if kind in ("all", "flaggedview"):
                continue                       # Gmail's [All Mail] / [Starred] duplicate everything
            label = leaf
            if not kind and leaf.lower() in SPECIAL_NAMES:
                kind = SPECIAL_NAMES[leaf.lower()][0]
            if kind:
                label = next((v[1] for v in SPECIAL_NAMES.values() if v[0] == kind), leaf)
            out.append({"name": name, "display": label, "path": display, "kind": kind or "folder", "delim": delim})
        # One of each special folder (the first wins), specials first in Live Mail's order.
        seen = set()
        for f in out:
            if f["kind"] != "folder":
                if f["kind"] in seen:
                    f["kind"] = "folder"
                    f["display"] = f["path"].split(f["delim"])[-1] if f["delim"] else f["path"]
                seen.add(f["kind"])
        out.sort(key=lambda f: (ORDER.index(f["kind"]) if f["kind"] in ORDER else len(ORDER), f["path"].lower()))
        return out

    def unseen(self, folder):
        def go(c):
            typ, data = c.status(quote(folder), "(UNSEEN)")
            if typ != "OK" or not data or not data[0]:
                return 0
            m = re.search(rb"UNSEEN (\d+)", data[0])
            return int(m.group(1)) if m else 0
        return self.call(go)

    def headers(self, folder, limit=200, since_uid=0):
        """Newest `limit` messages: [{uid, from, from_addr, to, subject, date, size, seen, flagged,
        answered, attach}]."""
        self.select(folder, readonly=True)

        def go(c):
            typ, data = c.uid("search", None, f"UID {since_uid + 1}:*" if since_uid else "ALL")
            uids = [int(u) for u in (data[0] or b"").split()] if typ == "OK" else []
            uids = [u for u in uids if u > since_uid][-limit:]
            if not uids:
                return []
            typ, data = c.uid("fetch", ",".join(map(str, uids)),
                              "(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS "
                              "(FROM TO CC SUBJECT DATE CONTENT-TYPE MESSAGE-ID)])")
            return self._check(typ, data, "Couldn't read messages")
        data = self.call(go)
        out, cur = [], None
        for item in data or []:
            if isinstance(item, tuple):
                cur = [item[0], item[1]]
                out.append(cur)
            elif isinstance(item, bytes) and cur is not None:
                cur[0] += item
        msgs = []
        for meta, hdr in out:
            m = re.search(rb"UID (\d+)", meta)
            if not m:
                continue
            flags = (re.search(rb"FLAGS \(([^)]*)\)", meta) or re.search(rb"()", b"")).group(1).decode().lower()
            size = re.search(rb"RFC822\.SIZE (\d+)", meta)
            idate = re.search(rb'INTERNALDATE "([^"]+)"', meta)
            h = email.message_from_bytes(hdr or b"", policy=POLICY)
            frm = _addr_list(h, "from")
            when = None
            try:
                when = parsedate_to_datetime(str(h["date"])).timestamp() if h["date"] else None
            except Exception:
                when = None
            if when is None and idate:
                try:
                    when = time.mktime(imaplib.Internaldate2tuple(b'INTERNALDATE "' + idate.group(1) + b'"'))
                except Exception:
                    when = None
            msgs.append({
                "uid": int(m.group(1)), "from": frm[0][0] or frm[0][1] if frm else "(no sender)",
                "from_addr": frm[0][1] if frm else "", "to": ", ".join(n or a for n, a in _addr_list(h, "to")),
                "subject": _hstr(h, "subject") or "(no subject)", "date": when or 0,
                "size": int(size.group(1)) if size else 0, "seen": "\\seen" in flags,
                "flagged": "\\flagged" in flags, "answered": "\\answered" in flags,
                "attach": "multipart/mixed" in _hstr(h, "content-type").lower(),
                "message_id": _hstr(h, "message-id"),
            })
        msgs.sort(key=lambda x: x["date"], reverse=True)
        return msgs

    def body(self, folder, uid, mark_seen=True):
        self.select(folder)

        def go(c):
            typ, data = c.uid("fetch", str(uid), "(BODY.PEEK[])")
            self._check(typ, data, "Couldn't open the message")
            raw = next((d[1] for d in data if isinstance(d, tuple)), None)
            if raw is None:
                raise RuntimeError("The message is no longer on the server.")
            if mark_seen:
                c.uid("store", str(uid), "+FLAGS", "(\\Seen)")
            return raw
        return self.call(go)

    def set_flag(self, folder, uids, flag, on):
        self.select(folder)
        self.call(lambda c: c.uid("store", ",".join(map(str, uids)), "+FLAGS" if on else "-FLAGS", f"({flag})"))

    def move(self, folder, uids, dest):
        self.select(folder)
        uidset = ",".join(map(str, uids))

        def go(c):
            if b"MOVE" in b" ".join(x.encode() if isinstance(x, str) else x for x in c.capabilities):
                typ, data = c.uid("MOVE", uidset, quote(dest))
                if typ == "OK":
                    return
            self._check(*c.uid("copy", uidset, quote(dest)), f"Couldn't move to {dest}")
            c.uid("store", uidset, "+FLAGS", "(\\Deleted)")
            c.expunge()
        self.call(go)

    def delete_forever(self, folder, uids):
        self.select(folder)

        def go(c):
            c.uid("store", ",".join(map(str, uids)), "+FLAGS", "(\\Deleted)")
            c.expunge()
        self.call(go)

    def append(self, folder, raw, flags="(\\Seen)"):
        self.call(lambda c: self._check(*c.append(quote(folder), flags, imaplib.Time2Internaldate(time.time()),
                                                  raw), f"Couldn't save to {folder}"))


def _hstr(h, name):
    try:
        v = h[name]
        return str(v).strip() if v is not None else ""
    except Exception:
        raw = h.get_all(name, failobj=[]) if hasattr(h, "get_all") else []
        return str(raw[0]) if raw else ""


def _addr_list(h, name):
    try:
        return [(n, a) for n, a in getaddresses([str(v) for v in h.get_all(name, [])]) if n or a]
    except Exception:
        return []


def test_login(acct, password):
    i = Imap(acct, password)
    i.connect()
    i.close()


# -------------------------------------------------------------------- SMTP ---
def send(acct, password, msg):
    s = acct["smtp"]
    ctx = ssl.create_default_context()
    if s["security"] == "ssl":
        c = smtplib.SMTP_SSL(s["host"], int(s["port"]), context=ctx, timeout=60)
    else:
        c = smtplib.SMTP(s["host"], int(s["port"]), timeout=60)
        if s["security"] == "starttls":
            c.starttls(context=ctx)
    try:
        if s.get("auth", True) and password:
            if c.has_extn("auth"):
                c.login(s.get("user") or acct.get("imap", {}).get("user") or acct["email"], password)
        c.send_message(msg)
    finally:
        try:
            c.quit()
        except Exception:
            pass


def build(acct, to, cc, bcc, subject, html_body, text_body, attachments=(), reply_to_msg=None):
    msg = EmailMessage()
    msg["From"] = formataddr((acct.get("name") or "", acct["email"]))
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=acct["email"].rsplit("@", 1)[-1])
    msg["X-Mailer"] = "Halcyon Mail"
    if reply_to_msg is not None and reply_to_msg.get("message_id"):
        msg["In-Reply-To"] = reply_to_msg["message_id"]
        refs = (reply_to_msg.get("references", "") + " " + reply_to_msg["message_id"]).strip()
        msg["References"] = refs
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    for path in attachments:
        p = Path(path)
        ctype = _guess_type(p)
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    return msg


def _guess_type(p):
    import mimetypes
    return mimetypes.guess_type(p.name)[0] or "application/octet-stream"


def recipients_ok(*fields):
    """At least one address across the fields, and every one of them well-formed."""
    addrs = []
    for text in fields:
        text = re.sub(r",\s*(?=,)", "", text.strip()).strip(", ")     # stray commas
        if not text:
            continue
        found = getaddresses([text])
        if not found or any(not a for _, a in found):
            return False
        addrs += [a for _, a in found]
    return bool(addrs) and all(re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", a) for a in addrs)


# ---------------------------------------------------------------- reading ---
REMOTE = re.compile(r"""(?:src|background|poster)\s*=\s*["']?\s*https?:|url\(\s*["']?\s*https?:""", re.I)


def parse(raw):
    """Everything the reading pane needs from a raw message."""
    m = email.message_from_bytes(raw, policy=POLICY)
    frm = _addr_list(m, "from")
    out = {
        "subject": _hstr(m, "subject") or "(no subject)",
        "from": frm[0] if frm else ("", ""),
        "to": _addr_list(m, "to"), "cc": _addr_list(m, "cc"),
        "reply_to": _addr_list(m, "reply-to"),
        "date": _hstr(m, "date"), "message_id": _hstr(m, "message-id"),
        "references": _hstr(m, "references"), "attachments": [], "html": None, "text": "",
    }
    try:
        out["timestamp"] = parsedate_to_datetime(out["date"]).timestamp()
    except Exception:
        out["timestamp"] = None
    try:
        body = m.get_body(preferencelist=("html", "plain"))
    except Exception:
        body = None
    if body is not None:
        content = _text(body)
        if body.get_content_type() == "text/html":
            out["html"] = _inline_cids(m, content)
            plain = m.get_body(preferencelist=("plain",))
            out["text"] = _text(plain) if plain is not None else ""
        else:
            out["text"] = content
    for part in m.iter_attachments() if m.is_multipart() else []:
        try:
            name = part.get_filename() or ("attachment." + part.get_content_subtype())
            data = part.get_payload(decode=True) or b""
            if part.get("Content-ID") and part.get_content_disposition() != "attachment" and \
                    part.get_content_maintype() == "image":
                continue                       # shown inline
            out["attachments"].append({"name": name, "type": part.get_content_type(), "data": data})
        except Exception:
            continue
    out["remote"] = bool(out["html"] and REMOTE.search(out["html"]))
    return out


def _text(part):
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def _inline_cids(m, html_text):
    """cid: pictures inside the message become data: URLs so they show without a server."""
    cids = {}
    for part in m.walk():
        cid = part.get("Content-ID")
        if cid and part.get_content_maintype() == "image":
            data = part.get_payload(decode=True) or b""
            cids[cid.strip("<> ")] = f"data:{part.get_content_type()};base64,{base64.b64encode(data).decode()}"
    if not cids:
        return html_text
    return re.sub(r"cid:([^\"'\s>)]+)", lambda mm: cids.get(mm.group(1), mm.group(0)), html_text)


LINK = re.compile(r"(https?://[^\s<>\"']+|www\.[^\s<>\"']+)")


def text_to_html(text):
    esc = html.escape(text or "")
    esc = LINK.sub(lambda mm: f'<a href="{mm.group(1) if mm.group(1).startswith("http") else "http://" + mm.group(1)}">'
                   f'{mm.group(1)}</a>', esc)
    return ("<div style=\"white-space: pre-wrap; font-family: 'Segoe UI', 'Noto Sans', sans-serif; "
            f"font-size: 10pt;\">{esc}</div>")


def human_size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.0f} {unit}"
        n /= 1024


def short_date(ts):
    if not ts:
        return ""
    t = time.localtime(ts)
    now = time.localtime()
    if t[:3] == now[:3]:
        return time.strftime("%-I:%M %p", t)
    if time.time() - ts < 6 * 86400:
        return time.strftime("%a %-I:%M %p", t)
    return time.strftime("%-m/%-d/%Y", t)


def long_date(ts):
    return time.strftime("%A, %B %-d, %Y %-I:%M %p", time.localtime(ts)) if ts else ""
