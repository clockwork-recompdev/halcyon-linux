"""Game library: installed Steam games, Windows programs installed under Wine,
Linux games from the app menu, and programs the user added by hand."""
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "halcyon" / "games.json"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "halcyon" / "games"
# Shared Windows environment used by halcyon-disc-install.
WINEPREFIX = HOME / ".local/share/halcyon/windows"


@dataclass
class Game:
    id: str                    # steam:440, desktop:wine/Programs/X.desktop, custom:<hash>
    name: str
    source: str                # Steam | Windows | Linux | Lutris | Heroic | Added
    cmd: list
    cwd: str | None = None
    env: dict = field(default_factory=dict)
    icon: str | None = None    # file path or icon theme name
    header: str | None = None  # wide banner (460x215)
    hero: str | None = None    # large background
    logo: str | None = None    # transparent title logo
    folder: str | None = None  # install folder, if known
    last_played: int = 0
    favorite: bool = False
    hidden: bool = False


# ------------------------------------------------------------------ config ---
def load_config():
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("favorites", [])
    cfg.setdefault("hidden", [])
    cfg.setdefault("played", {})
    cfg.setdefault("custom", [])
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG)


# ------------------------------------------------------------------- steam ---
def parse_vdf(text):
    """Steam's text KeyValues format -> nested dicts (keys lower-cased)."""
    root = {}
    stack = [root]
    key = None
    for s, brace in re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text):
        if brace == "{":
            d = {}
            stack[-1][key] = d
            stack.append(d)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = s.lower()
        else:
            stack[-1][key] = s.replace("\\\\", "\\")
            key = None
    return root


STEAM_ROOTS = (HOME / ".local/share/Steam", HOME / ".steam/steam",
               HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam")
# Compatibility tools and runtimes Steam lists as apps.
STEAM_TOOLS = re.compile(r"^(proton\b|steam linux runtime|steamworks|steam controller|steamvr|"
                         r"steam audio|steam\.dll)", re.I)


def steam_root():
    for p in STEAM_ROOTS:
        if (p / "steamapps").is_dir():
            return p.resolve()
    return None


def _steam_art(cache, appid):
    """Library art from Steam's cache. Newer clients keep it in
    librarycache/<id>/<hash>/<name>, older ones as librarycache/<id>_<name>."""
    art = {}
    d = cache / appid
    if d.is_dir():
        for f in d.iterdir():
            if f.is_file() and f.suffix == ".jpg":
                art["icon"] = str(f)          # the small square icon sits at the top level
        for f in d.rglob("*"):
            if f.name in ("library_header.jpg", "header.jpg"):
                art.setdefault("header", str(f))
            elif f.name == "library_hero.jpg":
                art["hero"] = str(f)
            elif f.name == "logo.png":
                art["logo"] = str(f)
    for key, name in (("icon", "icon.jpg"), ("header", "header.jpg"),
                      ("hero", "library_hero.jpg"), ("logo", "logo.png")):
        f = cache / f"{appid}_{name}"
        if key not in art and f.is_file():
            art[key] = str(f)
    return art


def steam_games():
    root = steam_root()
    if not root:
        return []
    libs = [root]
    try:
        vdf = parse_vdf((root / "steamapps/libraryfolders.vdf").read_text(errors="replace"))
        for entry in vdf.get("libraryfolders", {}).values():
            if isinstance(entry, dict) and entry.get("path"):
                p = Path(entry["path"])
                if p.resolve() != root and (p / "steamapps").is_dir():
                    libs.append(p)
    except OSError:
        pass
    if ".var/app/" in str(root):
        launcher = ["flatpak", "run", "com.valvesoftware.Steam"]
    else:
        launcher = ["steam"]
    cache = root / "appcache/librarycache"
    games = []
    seen = set()
    for lib in libs:
        for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
            try:
                st = parse_vdf(acf.read_text(errors="replace")).get("appstate", {})
            except OSError:
                continue
            appid, name = st.get("appid", ""), st.get("name", "")
            if not appid or appid in seen or not name or STEAM_TOOLS.match(name):
                continue
            try:
                if not int(st.get("stateflags", "0")) & 4:   # 4 = fully installed
                    continue
            except ValueError:
                continue
            seen.add(appid)
            art = _steam_art(cache, appid)
            folder = lib / "steamapps/common" / st.get("installdir", "")
            games.append(Game(
                id=f"steam:{appid}", name=name, source="Steam",
                cmd=launcher + [f"steam://rungameid/{appid}"],
                icon=art.get("icon"), header=art.get("header"), hero=art.get("hero"), logo=art.get("logo"),
                folder=str(folder) if folder.is_dir() else None,
                last_played=int(st.get("lastplayed", "0") or 0)))
    return games


# ---------------------------------------------------------- desktop files ---
def data_dirs():
    home = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share"))
    rest = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    return [home] + [Path(d) for d in rest if d]


def read_desktop(path):
    """The [Desktop Entry] group of a .desktop file as a dict."""
    out = {}
    group = None
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            group = line
            continue
        if group == "[Desktop Entry]" and "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip())
    return out


_ESC = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}


def exec_to_argv(exec_line, entry=None, path=None):
    """Desktop-entry Exec= -> argv: unescape the string, split it like a
    shell, and drop the %f/%u-style field codes."""
    s = re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), "\\" + m.group(1)), exec_line)
    try:
        words = shlex.split(s)
    except ValueError:
        words = s.split()
    argv = []
    for w in words:
        if w in ("%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%v", "%m", "%i"):
            continue
        w = w.replace("%c", (entry or {}).get("Name", "")).replace("%k", str(path or ""))
        argv.append(w.replace("%%", "%"))
    return argv


# Shortcuts installers drop next to the game that aren't the game.
WINE_JUNK = re.compile(r"uninstall|un-install|deinstall|readme|read me|manual|\bhelp\b|website|web site|"
                       r"on the web|online|register|registration|support|license|eula|release notes|"
                       r"homepage|\bfaq\b|documentation|\bremove\b|\bsetup\b", re.I)


def desktop_games():
    games = []
    seen = set()
    for base in data_dirs():
        apps = base / "applications"
        if not apps.is_dir():
            continue
        for f in sorted(apps.rglob("*.desktop")):
            rel = str(f.relative_to(apps))
            if rel in seen:                   # earlier data dirs win, as in menus
                continue
            seen.add(rel)
            e = read_desktop(f)
            if e.get("Type", "Application") != "Application" or not e.get("Exec"):
                continue
            if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true":
                continue
            name = e.get("Name", f.stem)
            cats = e.get("Categories", "").split(";")
            ex = e["Exec"]
            wine = rel.startswith("wine/Programs/")
            if f.name.startswith("halcyon-") or "steam://rungameid" in ex:
                continue                       # our own tools; Steam games come from Steam
            if wine:
                if WINE_JUNK.search(name):
                    continue
                source = "Windows"
            elif "Game" in cats:
                source = "Lutris" if "lutris:" in ex else "Heroic" if "heroic://" in ex else "Linux"
            else:
                continue
            games.append(Game(id=f"desktop:{rel}", name=name, source=source,
                              cmd=exec_to_argv(ex, e, f), cwd=e.get("Path") or None,
                              icon=e.get("Icon") or None))
    return games


# ----------------------------------------------------------- added by hand ---
def exe_icon(exe):
    """Icon of a Windows .exe as a cached PNG (icoutils + ImageMagick), or None."""
    key = hashlib.sha1(str(exe).encode()).hexdigest()[:16]
    out = CACHE / "icons" / f"{key}.png"
    if out.exists():
        return str(out) if out.stat().st_size else None
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".d")
    try:
        tmp.mkdir(exist_ok=True)
        subprocess.run(["wrestool", "-x", "-t", "14", "-o", str(tmp), str(exe)],
                       capture_output=True, timeout=20)
        icos = sorted(tmp.glob("*.ico"), key=lambda p: p.stat().st_size, reverse=True)
        if icos:
            # Use the largest image in the .ico.
            sizes = subprocess.run(["magick", "identify", "-format", "%p %w\n", str(icos[0])],
                                   capture_output=True, text=True, timeout=20).stdout.split("\n")
            best = max((l.split() for l in sizes if l.strip()), key=lambda p: int(p[1]), default=["0"])[0]
            subprocess.run(["magick", f"{icos[0]}[{best}]", "-resize", "64x64", str(out)],
                           capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    finally:
        for p in tmp.glob("*"):
            p.unlink(missing_ok=True)
        tmp.rmdir() if tmp.exists() else None
    if not out.exists():
        out.write_bytes(b"")                   # remember that there's no icon
        return None
    return str(out)


def custom_entry(path, name, args=""):
    return {"id": "custom:" + hashlib.sha1(f"{path}\0{time.time()}".encode()).hexdigest()[:12],
            "name": name, "path": str(path), "args": args}


def custom_games(cfg):
    games = []
    for c in cfg["custom"]:
        p = Path(c["path"])
        args = shlex.split(c.get("args", ""))
        env = {}
        icon = None
        if p.suffix.lower() in (".exe", ".bat", ".msi", ".lnk"):
            # A program inside some other Wine prefix runs in that prefix.
            parts = p.parts
            prefix = Path(*parts[:parts.index("drive_c")]) if "drive_c" in parts else WINEPREFIX
            env["WINEPREFIX"] = str(prefix)
            cmd = ["wine", str(p)] + args
            icon = exe_icon(p) if p.suffix.lower() == ".exe" else None
            source = "Windows"
        else:
            cmd = [str(p)] + args
            source = "Linux"
        games.append(Game(id=c["id"], name=c["name"], source=source, cmd=cmd, cwd=str(p.parent),
                          env=env, icon=icon or c.get("icon"), folder=str(p.parent)))
    return games


# -------------------------------------------------------------------- all ---
def load(cfg=None):
    cfg = cfg or load_config()
    games = steam_games() + desktop_games() + custom_games(cfg)
    fav, hidden, played = set(cfg["favorites"]), set(cfg["hidden"]), cfg["played"]
    for g in games:
        g.favorite = g.id in fav
        g.hidden = g.id in hidden
        g.last_played = max(g.last_played, int(played.get(g.id, 0)))
    games.sort(key=lambda g: g.name.casefold())
    return games
