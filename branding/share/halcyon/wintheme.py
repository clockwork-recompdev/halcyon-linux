#!/usr/bin/env python3
"""Apply a Windows desktop theme (.theme, e.g. Microsoft Plus! XP) to Plasma.

    wintheme.py list                       themes installed in Halcyon's Windows prefix
    wintheme.py apply FILE.theme [--no-wallpaper] [--no-cursors] [--no-sounds] [--no-colors]
    wintheme.py restore                    back to Halcyon's own colours

Wallpaper -> Plasma wallpaper; .cur/.ani cursors -> an Xcursor theme
(animated cursors stay animated); system sounds -> Plasma notification
sounds where Plasma has an equivalent event. Windows colours and window
metrics are left alone: Halcyon keeps its own look.
"""
import configparser
import os
import re
import shutil
import struct
import subprocess
import sys

PREFIX = os.environ.get("HALCYON_WINEPREFIX") or os.path.expanduser("~/.local/share/halcyon/windows")
DATA = os.path.expanduser("~/.local/share")

# ------------------------------------------------------------------ paths ---
def win_to_unix(path, theme_dir):
    """Resolve a Windows path from a .theme file inside the Wine prefix."""
    if not path:
        return None
    p = path.strip().strip('"')
    env = {"windir": "C:/windows", "systemroot": "C:/windows", "programfiles": "C:/Program Files",
           "programfiles(x86)": "C:/Program Files (x86)", "systemdrive": "C:", "userprofile": "C:/users/Public",
           "themedir": theme_dir}
    p = re.sub(r"%([^%]+)%", lambda m: env.get(m.group(1).lower(), m.group(0)), p)
    p = p.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", p):
        drive, rest = p[0].lower(), p[2:]
        base = os.path.join(PREFIX, "dosdevices", f"{drive}:")
    elif p.startswith("/"):
        base, rest = "/", p
    else:
        base, rest = theme_dir, p
    return ifind(base, rest)


def ifind(base, rel):
    """Case-insensitive path lookup (Windows paths, Linux disks)."""
    cur = os.path.realpath(base)
    for part in [x for x in rel.split("/") if x and x != "."]:
        try:
            match = next((e for e in os.listdir(cur) if e.lower() == part.lower()), None)
        except OSError:
            return None
        if match is None:
            return None
        cur = os.path.join(cur, match)
    return cur if os.path.exists(cur) else None


# ---------------------------------------------------------------- cursors ---
def read_cur(data):
    """Windows .cur (or .ico) -> (QImage, xhot, yhot) for the largest image."""
    from PySide6.QtGui import QImage
    _, kind, count = struct.unpack_from("<HHH", data, 0)
    entries = []
    for i in range(count):
        w, h, _c, _r, a, b, size, off = struct.unpack_from("<BBBBHHII", data, 6 + i * 16)
        entries.append(((w or 256), (h or 256), a, b, size, off))
    w, h, xhot, yhot, size, off = max(entries, key=lambda e: e[0] * e[1])
    if kind != 2:                     # .ico: no hotspot, use the centre
        xhot, yhot = w // 2, h // 2
    blob = data[off:off + size]
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        img = QImage.fromData(blob, "PNG")
    else:
        # Rebuild a one-image .ico around the DIB so Qt can decode it.
        ico = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, size, 22) + blob
        img = QImage.fromData(ico, "ICO")
    if img.isNull():
        raise ValueError("unreadable cursor image")
    return img, xhot, yhot


def read_ani(data):
    """Windows .ani -> [(QImage, xhot, yhot, delay_ms)] in display order."""
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise ValueError("not an .ani file")
    frames, rates, seq, jiffies = [], None, None, 6
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"anih" and len(body) >= 36:
            jiffies = struct.unpack_from("<I", body, 28)[0] or 6
        elif cid == b"rate":
            rates = list(struct.unpack_from(f"<{size // 4}I", body))
        elif cid == b"seq ":
            seq = list(struct.unpack_from(f"<{size // 4}I", body))
        elif cid == b"LIST" and body[:4] == b"fram":
            q = 4
            while q + 8 <= len(body):
                sid, ssz = body[q:q + 4], struct.unpack_from("<I", body, q + 4)[0]
                if sid == b"icon":
                    frames.append(read_cur(body[q + 8:q + 8 + ssz]))
                q += 8 + ssz + (ssz & 1)
        pos += 8 + size + (size & 1)
    if not frames:
        raise ValueError("no frames in .ani")
    order = seq or list(range(len(frames)))
    out = []
    for step, idx in enumerate(order):
        j = rates[step] if rates and step < len(rates) else jiffies
        img, xh, yh = frames[idx % len(frames)]
        out.append((img, xh, yh, max(10, j * 1000 // 60)))
    return out


def write_xcursor(path, frames, sizes=(24, 32, 48)):
    """Write an Xcursor file: every frame at several sizes, hotspots scaled."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    chunks = []
    for size in sizes:
        for img, xh, yh, delay in frames:
            scale = size / max(img.width(), img.height())
            im = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
            if abs(scale - 1) > 0.01:
                im = im.scaled(max(1, round(img.width() * scale)), max(1, round(img.height() * scale)),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            w, h = im.width(), im.height()
            pixels = bytes(im.constBits())[: w * h * 4] if im.bytesPerLine() == w * 4 else \
                b"".join(bytes(im.constScanLine(y))[: w * 4] for y in range(h))
            hdr = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, size, 1, w, h,
                              min(w - 1, round(xh * scale)), min(h - 1, round(yh * scale)), delay)
            chunks.append((size, hdr + pixels))
    head = struct.pack("<4sIII", b"Xcur", 16, 0x10000, len(chunks))
    pos = 16 + 12 * len(chunks)
    toc, body = b"", b""
    for size, chunk in chunks:
        toc += struct.pack("<III", 0xFFFD0002, size, pos)
        pos += len(chunk)
        body += chunk
    with open(path, "wb") as f:
        f.write(head + toc + body)


# Windows cursor role -> the X cursor names applications ask for.
CURSOR_NAMES = {
    "arrow": ["left_ptr", "default", "arrow", "top_left_arrow"],
    "help": ["help", "question_arrow", "whats_this", "left_ptr_help"],
    "appstarting": ["progress", "left_ptr_watch", "half-busy"],
    "wait": ["wait", "watch"],
    "crosshair": ["crosshair", "cross", "tcross"],
    "ibeam": ["text", "xterm", "ibeam"],
    "nwpen": ["pencil"],
    "no": ["not-allowed", "crossed_circle", "forbidden", "no-drop"],
    "sizens": ["ns-resize", "size_ver", "sb_v_double_arrow", "v_double_arrow", "row-resize", "n-resize", "s-resize"],
    "sizewe": ["ew-resize", "size_hor", "sb_h_double_arrow", "h_double_arrow", "col-resize", "e-resize", "w-resize"],
    "sizenwse": ["nwse-resize", "size_fdiag", "bd_double_arrow", "nw-resize", "se-resize",
                 "top_left_corner", "bottom_right_corner"],
    "sizenesw": ["nesw-resize", "size_bdiag", "fd_double_arrow", "ne-resize", "sw-resize",
                 "top_right_corner", "bottom_left_corner"],
    "sizeall": ["move", "fleur", "size_all", "all-scroll"],
    "uparrow": ["up-arrow", "center_ptr"],
    "hand": ["pointer", "hand2", "hand1", "pointing_hand"],
}


def make_cursor_theme(name, cursors):
    """cursors: {role: path to .cur/.ani} -> ~/.local/share/icons/<name>."""
    root = os.path.join(DATA, "icons", name)
    cdir = os.path.join(root, "cursors")
    shutil.rmtree(cdir, ignore_errors=True)
    os.makedirs(cdir)
    done = []
    for role, path in cursors.items():
        names = CURSOR_NAMES.get(role)
        if not names or not path:
            continue
        with open(path, "rb") as f:
            data = f.read()
        try:
            frames = read_ani(data) if data[:4] == b"RIFF" else [(*read_cur(data), 0)]
        except (ValueError, struct.error) as e:
            print(f"  skipped {role}: {e}", file=sys.stderr)
            continue
        write_xcursor(os.path.join(cdir, names[0]), frames)
        for alias in names[1:]:
            os.symlink(names[0], os.path.join(cdir, alias))
        done.append(role)
    # Anything the theme doesn't define falls back to Halcyon's cursors.
    with open(os.path.join(root, "index.theme"), "w") as f:
        f.write(f"[Icon Theme]\nName={name}\nComment=Converted from a Windows theme\nInherits=Oxygen_Blue\n")
    return done


# ----------------------------------------------------------------- sounds ---
# Windows sound event -> Plasma notification (file, group).
SOUND_EVENTS = {
    "systemasterisk": ("plasma_workspace", "messageInformation"),
    "systemexclamation": ("plasma_workspace", "messageWarning"),
    "systemhand": ("plasma_workspace", "messageCritical"),
    "systemquestion": ("plasma_workspace", "messageQuestion"),
    "systemnotification": ("plasma_workspace", "notification"),
    ".default": ("plasma_workspace", "beep"),
    "emptyrecyclebin": ("kio", "Trash: emptied"),
}


def apply_sounds(name, theme_ini, theme_dir):
    dest = os.path.join(DATA, "sounds", name)
    os.makedirs(dest, exist_ok=True)
    applied = []
    for section in theme_ini.sections():
        m = re.match(r"appevents\\schemes\\apps\\\.default\\([^\\]+)\\\.current", section.lower())
        if not m or m.group(1) not in SOUND_EVENTS:
            continue
        src = win_to_unix(theme_ini[section].get("DefaultValue", ""), theme_dir)
        if not src:
            continue
        target = os.path.join(dest, os.path.basename(src))
        shutil.copy(src, target)
        rc, event = SOUND_EVENTS[m.group(1)]
        group = f"Event/{event}"
        for key, val in (("Action", "Sound|Popup" if rc == "plasma_workspace" else "Sound"), ("Sound", target)):
            subprocess.run(["kwriteconfig6", "--file", f"{rc}.notifyrc", "--group", group, "--key", key, val])
        applied.append(m.group(1))
    return applied


# ------------------------------------------------------------------- tint ---
# Halcyon's chrome is built around this blue; tinting rotates every colour by
# the difference to the target hue, so the glass keeps its light and shadow.
BASE_HUE = 205.0
SYS = os.environ.get("HALCYON_SHARE", "/usr/share")   # override for tests
TINTED = {
    "desktoptheme": ("plasma/desktoptheme/Halcyon", "plasma/desktoptheme/Halcyon-Tinted"),
    "aurorae": ("aurorae/themes/HalcyonAero", "aurorae/themes/HalcyonAeroTinted"),
    "colors": ("color-schemes/HalcyonAero.colors", "color-schemes/HalcyonAeroTinted.colors"),
    "orbs": ("plasma/plasmoids/org.halcyon.workspaces", "plasma/plasmoids/org.halcyon.workspaces"),
}


def _tint_rgb(r, g, b, dh, sat):
    import colorsys
    h, l, s_ = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if s_ < 0.06:                       # greys (text, shadows) stay grey
        return r, g, b
    h = (h + dh / 360.0) % 1.0
    s_ = max(0.0, min(1.0, s_ * sat))
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s_)
    return round(r2 * 255), round(g2 * 255), round(b2 * 255)


def tint_text(text, dh, sat):
    """Recolour #rrggbb, #aarrggbb and 'r,g,b' triplets in a text file."""
    def hex6(m):
        r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
        return "#%02x%02x%02x" % _tint_rgb(r, g, b, dh, sat)
    def hex8(m):
        a = m.group(1)
        r, g, b = (int(m.group(2)[i:i + 2], 16) for i in (0, 2, 4))
        return "#%s%02x%02x%02x" % (a, *_tint_rgb(r, g, b, dh, sat))
    def trip(m):
        return "%s=%d,%d,%d" % (m.group(1), *_tint_rgb(*(int(x) for x in m.group(2, 3, 4)), dh, sat))
    text = re.sub(r'#([0-9a-fA-F]{2})([0-9a-fA-F]{6})(?![0-9a-fA-F])', hex8, text)
    text = re.sub(r'#([0-9a-fA-F]{6})(?![0-9a-fA-F])', hex6, text)
    return re.sub(r'^(\w+)=(\d{1,3}),(\d{1,3}),(\d{1,3})(?=,|$)', trip, text, flags=re.M)


def theme_color(ini, lower, theme_dir, wallpaper_path):
    """The theme's main colour: its highlight/title colour, else the wallpaper's."""
    if "control panel\\colors" in lower:
        sec = ini[lower["control panel\\colors"]]
        for key in ("Hilight", "HotTrackingColor", "ActiveTitle", "GradientActiveTitle"):
            v = sec.get(key, "").split()
            if len(v) == 3 and all(x.isdigit() for x in v):
                return tuple(int(x) for x in v)
    if wallpaper_path:
        from PySide6.QtGui import QImage
        img = QImage(wallpaper_path)
        if not img.isNull():
            img = img.scaled(48, 48)
            import colorsys
            best, weight = None, 0.0
            buckets = {}
            for y in range(img.height()):
                for x in range(img.width()):
                    c = img.pixelColor(x, y)
                    h, l, s_ = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
                    if 0.15 < l < 0.85:
                        key = round(h * 24) % 24
                        buckets[key] = buckets.get(key, 0.0) + s_
            if buckets:
                key = max(buckets, key=buckets.get)
                total = sum(buckets.values()) / (48 * 48)
                r, g, b = colorsys.hls_to_rgb(key / 24, 0.5, min(1.0, 0.25 + total * 2))
                return round(r * 255), round(g * 255), round(b * 255)
    return None


def tint_halcyon(rgb):
    """Write hue-shifted copies of Halcyon's chrome to ~/.local/share and use them."""
    import colorsys
    h, l, s_ = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    dh = h * 360 - BASE_HUE
    sat = 0.15 if s_ < 0.12 else min(1.25, 0.45 + s_)      # colourless themes: grey glass
    for kind, (src, dst) in TINTED.items():
        src, dst = os.path.join(SYS, src), os.path.join(DATA, dst)
        if not os.path.exists(src):
            continue
        if os.path.isdir(src):
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
            files = [os.path.join(b, f) for b, _, fs in os.walk(dst) for f in fs]
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
            files = [dst]
        for f in files:
            if f.endswith((".svg", ".qml", ".colors", "rc", "colors")) or os.path.basename(f) == "colors":
                with open(f, encoding="utf-8") as fh:
                    text = tint_text(fh.read(), dh, sat)
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(text)
    # Give the copies their own names so Plasma treats them as separate themes.
    dt = os.path.join(DATA, TINTED["desktoptheme"][1])
    meta = os.path.join(dt, "metadata.json")
    if os.path.exists(meta):
        with open(meta) as f:
            text = f.read().replace('"Id": "Halcyon"', '"Id": "Halcyon-Tinted"').replace('"Name": "Halcyon"', '"Name": "Halcyon (tinted)"')
        with open(meta, "w") as f:
            f.write(text)
    au = os.path.join(DATA, TINTED["aurorae"][1])
    if os.path.exists(os.path.join(au, "HalcyonAerorc")):
        os.rename(os.path.join(au, "HalcyonAerorc"), os.path.join(au, "HalcyonAeroTintedrc"))
        md = os.path.join(au, "metadata.desktop")
        with open(md) as f:
            text = f.read().replace("Name=Halcyon Aero", "Name=Halcyon Aero (tinted)").replace(
                "X-KDE-PluginInfo-Name=HalcyonAero", "X-KDE-PluginInfo-Name=HalcyonAeroTinted")
        with open(md, "w") as f:
            f.write(text)
    cs = os.path.join(DATA, TINTED["colors"][1])
    if os.path.exists(cs):
        with open(cs) as f:
            text = f.read().replace("ColorScheme=HalcyonAero", "ColorScheme=HalcyonAeroTinted").replace(
                "Name=Halcyon Aero", "Name=Halcyon Aero (tinted)")
        with open(cs, "w") as f:
            f.write(text)
    use_chrome("Halcyon-Tinted", "__aurorae__svg__HalcyonAeroTinted", "HalcyonAeroTinted")
    return dh, sat


def use_chrome(desktop_theme, decoration, colors, restart_shell=True):
    run = lambda *a: subprocess.run(list(a), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run("plasma-apply-desktoptheme", desktop_theme)
    run("plasma-apply-colorscheme", colors)
    run("kwriteconfig6", "--file", "kwinrc", "--group", "org.kde.kdecoration2", "--key", "theme", decoration)
    run("dbus-send", "--session", "--dest=org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure")
    if restart_shell:      # the workspace orbs read their colours at start
        run("systemctl", "--user", "restart", "plasma-plasmashell.service")


def restore_halcyon():
    for _, dst in TINTED.values():
        path = os.path.join(DATA, dst)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    use_chrome("Halcyon", "__aurorae__svg__HalcyonAero", "HalcyonAero")


# ------------------------------------------------------------------ apply ---
def apply(theme_path, wallpaper=True, cursors=True, sounds=True, colors=True):
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    with open(theme_path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("cp1252")
    ini.read_string(text)
    theme_dir = os.path.dirname(theme_path)
    lower = {s.lower(): s for s in ini.sections()}
    name = os.path.splitext(os.path.basename(theme_path))[0]
    if "theme" in lower and ini[lower["theme"]].get("DisplayName"):
        dn = ini[lower["theme"]]["DisplayName"]
        if not dn.startswith("@"):
            name = dn
    safe = re.sub(r"[^\w .+-]", "_", name).strip() or "Windows Theme"
    report = {"name": name}

    if wallpaper and "control panel\\desktop" in lower:
        wp = win_to_unix(ini[lower["control panel\\desktop"]].get("Wallpaper", ""), theme_dir)
        if wp:
            dest = os.path.join(DATA, "wallpapers", safe)
            os.makedirs(dest, exist_ok=True)
            target = os.path.join(dest, os.path.basename(wp))
            shutil.copy(wp, target)
            subprocess.run(["plasma-apply-wallpaperimage", target], stdout=subprocess.DEVNULL)
            report["wallpaper"] = target

    if cursors and "control panel\\cursors" in lower:
        sec = ini[lower["control panel\\cursors"]]
        found = {k.lower(): win_to_unix(v, theme_dir) for k, v in sec.items() if k.lower() in CURSOR_NAMES}
        if any(found.values()):
            cname = f"{safe} (Windows)"
            report["cursors"] = make_cursor_theme(cname, found)
            if report["cursors"]:
                subprocess.run(["plasma-apply-cursortheme", cname], stdout=subprocess.DEVNULL)

    if sounds:
        report["sounds"] = apply_sounds(safe, ini, theme_dir)

    if colors:
        wp = None
        if "control panel\\desktop" in lower:
            wp = win_to_unix(ini[lower["control panel\\desktop"]].get("Wallpaper", ""), theme_dir)
        rgb = theme_color(ini, lower, theme_dir, wp)
        if rgb:
            dh, sat = tint_halcyon(rgb)
            report["tint"] = {"color": "#%02x%02x%02x" % rgb, "hue_shift": round(dh), "saturation": round(sat, 2)}
    return report


def list_themes():
    out = []
    for base, dirs, files in os.walk(os.path.join(PREFIX, "drive_c")):
        if "/windows/winsxs" in base.lower():
            dirs[:] = []
            continue
        out += [os.path.join(base, f) for f in files if f.lower().endswith(".theme")]
    return sorted(out)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "list":
        for t in list_themes():
            print(t)
    elif len(sys.argv) >= 3 and sys.argv[1] == "apply":
        flags = set(sys.argv[3:])
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication([])
        r = apply(sys.argv[2], "--no-wallpaper" not in flags, "--no-cursors" not in flags,
                  "--no-sounds" not in flags, "--no-colors" not in flags)
        print(r)
    elif len(sys.argv) >= 2 and sys.argv[1] == "restore":
        restore_halcyon()
    else:
        print(__doc__)
        sys.exit(2)
