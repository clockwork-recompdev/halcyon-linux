"""Windows Sidebar gadget packages (.gadget): a folder (or ZIP/CAB archive of
one) with gadget.xml and HTML pages, often split into locale folders such as
en-US/. Windows looks files up case-insensitively and falls back across
locale folders, so we do the same."""
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HOME = Path.home()
USER_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share")) / "halcyon" / "gadgets"
SYSTEM_DIR = Path(os.environ.get("HALCYON_GADGETS_DIR") or "/usr/share/halcyon-gadgets/gadgets")


def _locales():
    lang = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "en_US")
    lang = lang.split(".")[0].replace("_", "-")
    out = []
    for loc in (lang, lang.split("-")[0], "en-US", "en"):
        if loc and loc.lower() not in [o.lower() for o in out]:
            out.append(loc)
    return out


LOCALES = _locales()
LOCALE_DIR = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,4})*$", re.I)


def _child(d, name):
    """Case-insensitive lookup of one path segment."""
    p = d / name
    if p.exists():
        return p
    low = name.lower()
    try:
        for c in d.iterdir():
            if c.name.lower() == low:
                return c
    except OSError:
        pass
    return None


def find_ci(root, rel):
    cur = Path(root)
    for seg in rel.replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            cur = cur.parent
            continue
        cur = _child(cur, seg)
        if cur is None:
            return None
    return cur


def _xml_text(data):
    """Manifest bytes -> str. Windows tools wrote encoding="Unicode" (UTF-16)
    and other names Python doesn't know, so decode by BOM/content and drop the
    declaration."""
    if data.startswith(b"\xef\xbb\xbf"):
        text = data[3:].decode("utf-8", "replace")
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    elif len(data) > 1 and data[1:2] == b"\0":
        text = data.decode("utf-16le", "replace")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", "replace")
    return re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("\ufeff"))


class Gadget:
    def __init__(self, root):
        self.root = Path(root)
        self.user = USER_DIR in self.root.parents
        self.name = self.root.stem
        self.version = self.author = self.copyright = self.description = self.website = ""
        self.icon = self.base = self.logo = None
        self.ok = False
        mf = self.resolve("gadget.xml")
        if not mf:
            return
        try:
            x = ET.fromstring(_xml_text(mf.read_bytes()))
        except Exception:                       # unreadable or malformed manifest: not a usable gadget
            return

        def text(path):
            e = x.find(path)
            return (e.text or "").strip() if e is not None and e.text else ""
        self.name = text("name") or self.name
        self.version = text("version")
        self.description = text("description")
        self.copyright = text("copyright")
        a = x.find("author")
        if a is not None:
            self.author = a.get("name", "")
            info = a.find("info")
            if info is not None:
                self.website = info.get("url", "")
            logo = a.find("logo")
            if logo is not None and logo.get("src"):
                self.logo = self.resolve(logo.get("src"))
        icons = [i for i in x.iter("icon") if i.get("src")]
        icons.sort(key=lambda i: int(i.get("width") or 0), reverse=True)
        for i in icons:
            self.icon = self.resolve(i.get("src"))
            if self.icon:
                break
        for host in x.iter("host"):
            b = host.find("base")
            if b is not None and b.get("src"):
                self.base = b.get("src")
                break
        self.ok = bool(self.base and self.resolve(self.base))

    @property
    def key(self):
        return str(self.root)

    def resolve(self, rel):
        """A file in the package, trying the locale folders first."""
        rel = rel.replace("\\", "/").lstrip("/")
        segs = [s for s in rel.split("/") if s]
        # A path already inside a locale folder may really live in another one or at the root.
        if len(segs) > 1 and LOCALE_DIR.match(segs[0]):
            tails = [rel, "/".join(segs[1:])]
        else:
            tails = [rel]
        # Locale folders this package actually has, after the preferred ones:
        # some gadgets only ship ja-JP/ or de-DE/.
        extra = []
        try:
            extra = sorted(d.name for d in self.root.iterdir() if d.is_dir() and LOCALE_DIR.match(d.name)
                           and d.name.lower() not in [l.lower() for l in LOCALES])
        except OSError:
            pass
        for tail in tails:
            for loc in LOCALES + [""] + extra:
                base = find_ci(self.root, loc) if loc else self.root
                if base is None:
                    continue
                f = find_ci(base, tail)
                if f and f.is_file():
                    return f
        f = find_ci(self.root, rel)
        return f if f and f.exists() else None

    def base_url_path(self):
        """URL path of the main page under the gadget. Pages always sit one folder
        down (in a locale folder, real or not) so "../images/x.png" reaches the
        package root, as it did on Windows."""
        f = self.resolve(self.base)
        rel = str(f.relative_to(self.root)) if f else self.base
        if "/" not in rel:
            rel = f"{LOCALES[0]}/{rel}"
        return rel


def all_gadgets():
    seen = {}
    for d in (SYSTEM_DIR, USER_DIR):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_dir():
                g = Gadget(p)
                if g.ok:
                    seen[g.name.lower()] = g          # user copies override built-in ones
    return sorted(seen.values(), key=lambda g: g.name.casefold())


def install(path):
    """Unpack a .gadget file (ZIP or CAB) or copy a gadget folder into the
    user's gadget folder. Returns the installed Gadget."""
    src = Path(path)
    tmp = Path(tempfile.mkdtemp(prefix="gadget-"))
    try:
        if src.is_dir():
            shutil.copytree(src, tmp / "g")
        else:
            (tmp / "g").mkdir()
            r = subprocess.run(["bsdtar", "-xf", str(src), "-C", str(tmp / "g")], capture_output=True, text=True)
            if r.returncode:
                raise ValueError(f"Not a gadget package: {r.stderr.strip()}")
        root = tmp / "g"
        # Some packages wrap everything in one extra folder.
        if not Gadget(root).ok:
            kids = [k for k in root.iterdir() if k.is_dir()]
            if len(kids) == 1:
                root = kids[0]
        g = Gadget(root)
        if not g.ok:
            raise ValueError("This file doesn't contain a Windows gadget (no gadget.xml).")
        name = src.name if src.suffix.lower() == ".gadget" else f"{src.name}.gadget"
        safe = "".join(c for c in name if c not in "/\0") or "Gadget.gadget"
        dest = USER_DIR / safe
        USER_DIR.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(root), dest)
        return Gadget(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def uninstall(g):
    if g.user:
        shutil.rmtree(g.root, ignore_errors=True)


# ------------------------------------------------------------ gadget packs ---
PACK_SUFFIXES = (".msi", ".7z", ".zip", ".cab", ".rar")
# Parts of 8GadgetPack that only exist to run its own Sidebar on Windows.
PACK_SKIP = {"sidebar7.gadget"}


def _needs_native(root):
    """8GadgetPack's own gadgets drive a compiled Windows helper (LoadNative);
    they can't run here."""
    marks = (b"LoadNative", "LoadNative".encode("utf-16-le"))
    for f in Path(root).rglob("*"):
        if f.suffix.lower() in (".js", ".html", ".htm") and f.is_file():
            try:
                data = f.read_bytes()
            except OSError:
                continue
            if any(m in data for m in marks):
                return True
    return False


def _find_gadgets(top):
    out = []
    for p in sorted(Path(top).rglob("*")):
        if p.is_dir() and p.name.lower().endswith(".gadget") and not any(
                q.name.lower().endswith(".gadget") for q in p.parents if q != Path(top)):
            out.append(p)
        elif p.is_file() and p.suffix.lower() == ".gadget":
            out.append(p)
    return out


def import_pack(path, progress=None):
    """Install every gadget inside a gadget pack: an installer such as
    8GadgetPack's .msi, or an archive of .gadget folders/files.
    Returns (installed Gadgets, [(name, reason) skipped])."""
    src = Path(path)
    tmp = Path(tempfile.mkdtemp(prefix="gadgetpack-"))
    installed, skipped = [], []
    try:
        if src.suffix.lower() == ".msi":
            if not shutil.which("msiextract"):
                raise ValueError("Reading .msi installers needs msiextract (the msitools package).")
            r = subprocess.run(["msiextract", "-C", str(tmp), str(src)], capture_output=True, text=True)
            if r.returncode:
                raise ValueError(f"Couldn't unpack the installer: {r.stderr.strip()[:300]}")
        else:
            r = subprocess.run(["bsdtar", "-xf", str(src), "-C", str(tmp)], capture_output=True, text=True)
            if r.returncode:
                raise ValueError(f"Couldn't unpack {src.name}: {r.stderr.strip()[:300]}")
        # Installers often carry the gadgets in archives of their own.
        for arc in list(tmp.rglob("*")):
            if arc.is_file() and arc.suffix.lower() in (".7z", ".zip", ".cab") and arc.suffix.lower() != ".gadget":
                dest = arc.with_name(arc.name + ".d")
                dest.mkdir()
                subprocess.run(["bsdtar", "-xf", str(arc), "-C", str(dest)], capture_output=True)
        found = _find_gadgets(tmp)
        if not found:
            raise ValueError(f"No gadgets found in {src.name}.")
        for i, g in enumerate(found):
            if progress:
                progress(i, len(found), g.name)
            if g.name.lower() in PACK_SKIP:
                continue
            if g.is_dir() and _needs_native(g):
                skipped.append((Gadget(g).name or g.stem, "needs 8GadgetPack's Windows helper"))
                continue
            try:
                installed.append(install(g))
            except Exception as e:
                skipped.append((g.stem, str(e)))
        return installed, skipped
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
