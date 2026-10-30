"""The Messenger look comes from the user's own copy of Windows Live Messenger
2009 (wlsetup-all.exe / the standalone installer): images, scenes, user tiles,
sounds and fonts are unpacked into ~/.local/share/halcyon/messenger/skin.
Nothing Microsoft made ships with Halcyon; without a skin the app runs plain."""
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "messenger"
SKIN = DATA / "skin"
# The 2009 English installer, as archived by the Messenger revival community.
INSTALLER_URL = "https://archive.org/download/wlm-14.0.8117.0416-en/wlm-14.0.8117.0416-en.exe"
BANNER_URL = "https://archive.org/download/wlm200912img/default-messenger-skinny.jpg"

# msgsres.dll resources -> skin names
RES = {
    "4000/606": "frame_small.png",       # 66x66 display picture frame (contact list header)
    "4000/583": "frame_large.png",       # 139x139 frame (conversation window)
    "4000/605": "frame_tiny.png",        # 50x50
    "4000/582": "usertile_default.png",  # default display picture
    "4000/161": "state_online.png",      # 42x42 status figures
    "4000/162": "state_away.png",
    "4000/163": "state_busy.png",
    "4000/164": "state_offline.png",
    "4000/1075": "logo.png",             # Messenger wordmark
    "ICON/92": "dot_online.ico",         # 16px contact list status squares
    "ICON/93": "dot_away.ico",
    "ICON/94": "dot_busy.ico",
    "ICON/95": "dot_offline.ico",
    "ICON/96": "dot_blocked.ico",
    "ICON/32": "app.ico",
}
SOUNDS = ("online", "type", "nudge", "newalert", "newemail", "outgoing", "phone", "vimdone")


def installed():
    return (SKIN / "frame_small.png").exists()


def find_installer():
    """A Messenger 2009 installer the user already has."""
    names = ("wlsetup-all*.exe", "wlm-14*.exe", "*messenger*2009*.exe", "Messenger.msi")
    for d in (DATA / "import" / "dl", Path.home() / "Downloads", Path.home()):
        for pat in names:
            for p in sorted(d.glob(pat)):
                if p.is_file():
                    return p
        for p in d.glob("wlm.exe"):
            return p
    return None


def download_installer(progress=None):
    dest = DATA / "import" / "dl" / "wlm-14.0.8117.0416-en.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(INSTALLER_URL, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(int(done * 100 / total))
    tmp.replace(dest)
    return dest


def _run(*cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _payload(exe_dir, name):
    """The installer keeps each MSI in a one-file cabinet; find the one holding `name`."""
    for p in Path(exe_dir).rglob("*"):
        if p.is_file() and p.stat().st_size > 50_000:
            with open(p, "rb") as f:
                head = f.read(512)
            if head.startswith(b"MSCF") and name.encode() in head:
                return p
    return None


def import_from(installer, step=None):
    """Unpack the installer (never run it) and build the skin. Returns the skin dir."""
    step = step or (lambda _m: None)
    work = Path(tempfile.mkdtemp(prefix="hm-import-"))
    try:
        installer = Path(installer)
        msi = None
        if installer.suffix.lower() == ".msi":
            msi = installer
        else:
            step("Unpacking the installer…")
            _run("7z", "x", "-y", f"-o{work / 'exe'}", str(installer))
            cab = _payload(work / "exe", "Messenger.msi")
            if not cab:
                raise RuntimeError("This installer doesn't contain Messenger.msi.")
            _run("cabextract", "-q", "-d", str(work / "msi"), str(cab))
            msi = work / "msi" / "Messenger.msi"
        step("Extracting Messenger…")
        (work / "files").mkdir()
        _run("msiextract", str(msi), cwd=work / "files")
        files = {p.name.lower(): p for p in (work / "files").rglob("*") if p.is_file()}
        if "msgsres.dll" not in files:
            raise RuntimeError("Messenger's resources (msgsres.dll) weren't found.")
        _run("7z", "x", "-y", f"-o{work / 'res'}", str(files["msgsres.dll"]))

        new = SKIN.with_name("skin.new")
        shutil.rmtree(new, ignore_errors=True)
        new.mkdir(parents=True)
        step("Copying images…")
        rs = work / "res" / ".rsrc"
        for src, dst in RES.items():
            kind, num = src.split("/")
            cands = list((rs / kind).glob(f"{num}")) + list((rs / kind).glob(f"{num}.*"))
            if cands:
                shutil.copy(cands[0], new / dst)
        # Scenes and user tiles are cabinets ("content packs").
        for pack in ("scenes", "usertiles"):
            mct = files.get(f"{pack}.mct")
            if mct:
                _run("cabextract", "-q", "-d", str(new / pack), str(mct))
        step("Converting sounds…")
        (new / "sounds").mkdir()
        for s in SOUNDS:
            src = files.get(f"{s}.wma")
            if src:
                _run("ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", "2", "-ar", "44100",
                     str(new / "sounds" / f"{s}.wav"))
        # Segoe UI, used only inside the app.
        font_cab = _payload(work / "exe", "SegoeFont.msi") if installer.suffix.lower() != ".msi" else None
        if font_cab:
            step("Extracting fonts…")
            _run("cabextract", "-q", "-d", str(work / "fontmsi"), str(font_cab))
            (work / "fonts").mkdir()
            _run("msiextract", str(work / "fontmsi" / "SegoeFont.msi"), cwd=work / "fonts")
            (new / "fonts").mkdir()
            for p in (work / "fonts").rglob("*.ttf"):
                shutil.copy(p, new / "fonts" / p.name.lower())
        # The default blue scene isn't in the installer (Messenger downloaded it).
        try:
            step("Fetching the default scene…")
            with urllib.request.urlopen(BANNER_URL, timeout=20) as r:
                (new / "scene_default.jpg").write_bytes(r.read())
        except OSError:
            pass
        shutil.rmtree(SKIN, ignore_errors=True)
        new.rename(SKIN)
        return SKIN
    finally:
        shutil.rmtree(work, ignore_errors=True)


def path(name):
    p = SKIN / name
    return str(p) if p.exists() else None


def scenes():
    d = SKIN / "scenes"
    out = []
    if (SKIN / "scene_default.jpg").exists():
        out.append(("Default", str(SKIN / "scene_default.jpg")))
    xml = d / "content.xml"
    if xml.exists():
        import re
        for m in re.finditer(r'file="([^"]+)"[^>]*scene:displayname="([^"]+)"', xml.read_text("utf-8", "replace")):
            if (d / m.group(1)).exists():
                out.append((m.group(2), str(d / m.group(1))))
    return out
