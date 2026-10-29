"""Local HTTP server behind every gadget page.

    /<token>/<instance>/<path>   files from the gadget package (case-insensitive, locale fallback)
    /<token>/api                 synchronous System.* calls (the gadget API is synchronous, so
                                 pages call it with blocking XMLHttpRequest)
    /<token>/proxy?u=<url>       fetches for gadgets: on Windows they could read any site

Serving over http (rather than file://) means old backslash paths such as
System.Gadget.path + "\\images\\bg.png" are normalised by the browser."""
import json
import mimetypes
import os
import re
import secrets
import shlex
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import sysinfo, vbscript

TOKEN = secrets.token_hex(8)
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/x-icon", ".ico")
mimetypes.add_type("text/xml", ".xml")


class Server:
    def __init__(self, host):
        self.host = host                 # host.instance(id) -> Instance; host.bridge for UI requests
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, inst_id, rel=""):
        return f"{self.origin}/{TOKEN}/{inst_id}/{rel.replace(os.sep, '/')}"

    # -------------------------------------------------------------- paths ---
    def any_path(self, p):
        """local_path() for a path that may belong to any gadget instance."""
        m = re.match(rf"^{re.escape(self.origin)}/{TOKEN}/([^/]+)/", p.replace("\\", "/"))
        inst = self.host.instance(m.group(1)) if m else None
        if inst is None:
            class _Any:                              # paths not inside a gadget
                id = "-"
                gadget = None
            inst = _Any()
        return self.local_path(inst, p)

    def local_path(self, inst, p):
        """A path a gadget handed us -> a real path (gadget URLs, C:\\ paths, Linux paths)."""
        if not p:
            return None
        p = str(p).replace("\\", "/")
        fs = f"/{TOKEN}/fs/"
        if p.startswith(self.origin + fs) or p.startswith(fs):
            return "/" + urllib.parse.unquote(p.split(fs, 1)[1])
        prefix = f"{self.origin}/{TOKEN}/{inst.id}"
        if p.lower().startswith(prefix.lower()):
            rel = urllib.parse.unquote(p[len(prefix):]).lstrip("/")
            return str(inst.gadget.resolve(rel) or inst.gadget.root / rel)
        if p.startswith("file:"):
            return urllib.parse.unquote(urllib.parse.urlparse(p).path)
        m = re.match(r"^([A-Za-z]):/?(.*)$", p)
        if m:
            letter, rest = m.group(1).upper(), m.group(2)
            if letter == "C":
                # Sample Pictures/Music lived under Public; point them at the user's own folders.
                pub = re.match(r"(?i)^users/public/(pictures|music|videos)(?:/sample \w+)?/?(.*)$", rest)
                if pub:
                    kind = {"pictures": "PICTURES", "music": "MUSIC", "videos": "VIDEOS"}[pub.group(1).lower()]
                    return str(Path(sysinfo.xdg_dir(kind)) / pub.group(2))
                u = re.match(r"(?i)^(users|documents and settings)/[^/]+/?(.*)$", rest)
                if u:
                    sub = u.group(2)
                    ad = re.match(r"(?i)^(appdata|application data|local settings/application data)(?:/(roaming|local|locallow))?/?(.*)$", sub)
                    if ad:
                        kind = "Local" if "local" in ad.group(1).lower() else (ad.group(2) or "Roaming").capitalize()
                        return str(Path(sysinfo.appdata(kind)) / ad.group(3))
                    return str(Path.home() / sub)
                return None
            mnt = sysinfo.drive_path(letter)
            return str(Path(mnt) / rest) if mnt else None
        return p if p.startswith("/") else None

    # -------------------------------------------------------------- api ---
    def call(self, inst, f, a):
        h = self.host
        if f == "state":
            return inst.state()
        if f == "settings.read":
            return inst.settings.get(str(a[0]))
        if f == "settings.write":
            inst.settings[str(a[0])] = a[1]
            h.bridge.save.emit()
            return None
        if f == "size":
            h.bridge.resize.emit(inst.id, a[0], int(a[1]), int(a[2]))
            return None
        if f == "popup":
            inst.pending_popup = a[0]          # read by the page's createWindow right after
            return None
        if f == "drag":
            h.bridge.drag.emit(inst.id)
            return None
        if f == "close":
            h.bridge.close.emit(inst.id)
            return None
        if f == "opacity":
            return inst.opacity
        if f == "machine":
            return sysinfo.machine()
        if f == "power":
            return sysinfo.power()
        if f == "wireless":
            return sysinfo.wireless()
        if f == "tz.current":
            return sysinfo.current_zone()
        if f == "tz.all":
            return sysinfo.zones()
        if f == "tz.info":
            return sysinfo.zone_info(a[0])
        if f == "tz.local":
            return sysinfo.local_time(a[0])
        if f == "env":
            name = str(a[0]).upper()
            alias = {"USERNAME": os.environ.get("USER", ""), "USERPROFILE": f"C:\\Users\\{os.environ.get('USER', 'user')}",
                     "COMPUTERNAME": os.uname().nodename, "OS": "Windows_NT", "WINDIR": "C:\\Windows",
                     "SYSTEMROOT": "C:\\Windows", "PROCESSOR_ARCHITECTURE": "AMD64",
                     "NUMBER_OF_PROCESSORS": str(os.cpu_count() or 1),
                     "PUBLIC": "C:\\Users\\Public", "ALLUSERSPROFILE": "C:\\ProgramData",
                     "PROGRAMFILES": "C:\\Program Files",
                     "APPDATA": f"C:\\Users\\{os.environ.get('USER', 'user')}\\AppData\\Roaming",
                     "LOCALAPPDATA": f"C:\\Users\\{os.environ.get('USER', 'user')}\\AppData\\Local", "TEMP": "/tmp", "TMP": "/tmp",
                     "HOMEDRIVE": "C:", "SYSTEMDRIVE": "C:"}
            return alias.get(name, os.environ.get(name, os.environ.get(a[0], "")))
        if f == "debug":
            print(f"[{inst.gadget.name}]", *a, flush=True)
            return None
        if f == "sound":
            p = self.local_path(inst, a[0])
            if p and os.path.exists(p):
                subprocess.Popen(["pw-play", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return None
        if f == "shell.execute":
            return self._execute(inst, *a)
        if f == "shell.item":
            p = self.local_path(inst, a[0])
            return _item(p) if p and os.path.exists(p) else None
        if f == "shell.list":
            p = self.local_path(inst, a[0])
            try:
                names = sorted(os.listdir(p), key=str.casefold)
            except (OSError, TypeError):
                return []
            return [_item(os.path.join(p, n)) for n in names if not n.startswith(".")]
        if f == "shell.known":
            return sysinfo.known_folder(a[0])
        if f == "shell.choose":
            return h.ask(inst, *a)                     # blocks this request thread until the dialog closes
        if f == "wmi":
            return sysinfo.wmi_query(a[0])
        if f == "wmi.class":
            return sysinfo.wmi(a[0])
        if f == "fs.mkdir":
            p = self.local_path(inst, a[0])
            try:
                os.makedirs(p, exist_ok=True)
                return True
            except (OSError, TypeError):
                return False
        if f == "fs.delete":
            p = self.local_path(inst, a[0])
            try:
                os.remove(p) if os.path.isfile(p) else os.rmdir(p)
                return True
            except (OSError, TypeError):
                return False
        if f == "drives":
            return [{"letter": l, "path": m} for l, m in sysinfo.drives()]
        if f == "trash":
            return sysinfo.trash()
        if f == "trash.empty":
            subprocess.run(["gio", "trash", "--empty"], capture_output=True)
            return None
        if f == "fs.exists":
            p = self.local_path(inst, a[0])
            return bool(p) and (os.path.isdir(p) if a[1] == "dir" else os.path.isfile(p))
        if f == "fs.read":
            p = self.local_path(inst, a[0])
            try:
                return Path(p).read_bytes().decode("utf-8", "replace")
            except (OSError, TypeError):
                return None
        if f == "fs.write":
            p = self.local_path(inst, a[0])
            if not p:
                return False
            try:
                mode = "a" if len(a) > 2 and a[2] else "w"
                with open(p, mode, encoding="utf-8") as fh:
                    fh.write(a[1])
                return True
            except OSError:
                return False
        raise KeyError(f"unknown call {f}")

    def _execute(self, inst, target, args="", directory="", _op=""):
        p = self.local_path(inst, target) if target else None
        try:
            if p and os.path.exists(p) and not os.access(p, os.X_OK) or p and os.path.isdir(p):
                subprocess.Popen(["xdg-open", p], start_new_session=True)
            elif re.match(r"^(https?|mailto|ftp):", str(target), re.I):
                subprocess.Popen(["xdg-open", target], start_new_session=True)
            elif p and os.path.exists(p):
                subprocess.Popen([p] + shlex.split(args or ""), cwd=self.local_path(inst, directory) or None,
                                 start_new_session=True)
            else:
                exe = str(target).replace("\\", "/").split("/")[-1].lower()
                # Common Windows programs gadgets open.
                mapped = {"explorer.exe": ["xdg-open", str(Path.home())], "notepad.exe": ["kate"],
                          "calc.exe": ["kcalc"], "taskmgr.exe": ["plasma-systemmonitor"],
                          "control.exe": ["systemsettings"]}.get(exe)
                if mapped:
                    subprocess.Popen(mapped, start_new_session=True)
                else:
                    return False
            return True
        except OSError:
            return False

    # ---------------------------------------------------------- handler ---
    def _handler(srv):
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _parts(self):
                u = urllib.parse.urlsplit(self.path)
                segs = u.path.split("/", 3)          # ['', token, inst|api|proxy, rest]
                if len(segs) < 3 or segs[1] != TOKEN:
                    return None, None, None, u
                return segs[2], (segs[3] if len(segs) > 3 else ""), u.query, u

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                kind, rest, query, u = self._parts()
                if os.environ.get("HALCYON_GADGETS_TRACE"):
                    print("GET", self.path[:160], flush=True)
                if kind is None:
                    return self._send(404)
                if kind == "proxy":
                    return self._proxy(query)
                if kind == "fs":
                    f = Path("/" + urllib.parse.unquote(rest))
                    if not f.is_file():
                        return self._send(404)
                    try:
                        return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                    except OSError:
                        return self._send(403)
                inst = srv.host.instance(kind)
                if not inst:
                    return self._send(404)
                f = inst.gadget.resolve(urllib.parse.unquote(rest))
                if not f:
                    if os.environ.get("HALCYON_GADGETS_DEBUG"):
                        print(f"[{inst.gadget.name}] 404 {urllib.parse.unquote(rest)}", flush=True)
                    return self._send(404, b"not found")
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                try:
                    data = f.read_bytes()
                except OSError:
                    return self._send(404)
                if ctype.startswith("text/") or ctype.endswith(("javascript", "xml")):
                    cs = _charset(data)
                    if ctype == "text/html":
                        text = data.decode(cs, "replace")
                        if "vbscript" in text.lower():
                            data, cs = vbscript.rewrite_html(text).encode("utf-8"), "utf-8"
                    ctype += "; charset=" + cs
                self._send(200, data, ctype)

            def do_POST(self):
                kind, rest, query, u = self._parts()
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                if kind == "proxy":
                    return self._proxy(query, body)
                if kind != "api":
                    return self._send(404)
                try:
                    req = json.loads(body or b"{}")
                    if os.environ.get("HALCYON_GADGETS_TRACE"):
                        print("API", req.get("f"), str(req.get("a"))[:120], flush=True)
                    inst = srv.host.instance(req.get("i"))
                    if not inst:
                        raise KeyError("no such gadget")
                    out = {"r": srv.call(inst, req.get("f"), req.get("a") or [])}
                except Exception as e:                # report to the page, keep serving
                    out = {"e": f"{e.__class__.__name__}: {e}"}
                self._send(200, json.dumps(out).encode(), "application/json")

            def _proxy(self, query, body=None):
                q = urllib.parse.parse_qs(query)
                target = (q.get("u") or [""])[0]
                if not re.match(r"^https?://", target, re.I):
                    return self._send(400, b"bad url")
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 6.1; Halcyon Gadgets) like Gecko"}
                for k in ("Content-Type", "Accept", "SOAPAction", "Authorization"):
                    if self.headers.get(k):
                        headers[k] = self.headers[k]
                req = urllib.request.Request(target, data=body if self.command == "POST" else None,
                                             headers=headers, method=self.command if self.command != "HEAD" else "GET")
                try:
                    with urllib.request.urlopen(req, timeout=20) as r:
                        data = r.read()
                        self._send(r.status, data, r.headers.get("Content-Type", "application/octet-stream"))
                except urllib.error.HTTPError as e:
                    self._send(e.code, e.read() or b"", e.headers.get("Content-Type", "text/plain"))
                except Exception as e:
                    self._send(502, str(e).encode())
        return H


def _item(p):
    st = None
    try:
        st = os.stat(p)
    except OSError:
        pass
    name = os.path.basename(p.rstrip("/")) or p
    isdir = os.path.isdir(p)
    ext = os.path.splitext(name)[1].lower()
    mime = mimetypes.guess_type(name)[0] or ""
    kind = "File folder" if isdir else ("JPEG image" if ext in (".jpg", ".jpeg") else
                                        (mime.split("/")[0].capitalize() + " file") if mime else
                                        (ext[1:].upper() + " File" if ext else "File"))
    return {"path": p, "name": name, "isFolder": isdir, "isLink": os.path.islink(p),
            "isFileSystem": True, "type": kind, "size": st.st_size if st and not isdir else 0,
            "modifyDate": int(st.st_mtime * 1000) if st else 0}


def _charset(data):
    """The real encoding of a gadget text file. Old gadgets often say
    charset=Unicode (UTF-16) in a <meta> tag while the file is plain ASCII or
    UTF-8; IE coped, so send the actual encoding in the header, which wins."""
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if len(data) > 1 and data[1:2] == b"\0" and data[0:1] != b"\0":
        return "utf-16le"                        # UTF-16 without a byte-order mark
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "windows-1252"
