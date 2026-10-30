#!/usr/bin/env python3
"""Generate Omarchy-style keyboard shortcuts for Plasma.

One table drives three outputs:
  branding/etc/xdg/kglobalshortcutsrc        system-wide shortcut defaults
  branding/share/applications/halcyon-*.desktop  launchers for command bindings
  branding/share/halcyon/keybindings.tsv     cheat sheet shown by Super+K

Keys follow Omarchy (github.com/omacom/omarchy, default/hypr/bindings/*.lua).
Hyprland-only features (groups, scratchpads, universal copy/paste) have no
Plasma equivalent and are left out.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BR = os.path.join(ROOT, "branding")

# (section, cheat-sheet category, keys, description, target)
#   target: ("kde", component, action)      bind an existing Plasma/KWin action
#           ("cmd", id, command)             launcher .desktop file we generate
#           ("app", desktop-file-id)         bind an installed app's _launch
# Several keys for one action are joined with \t, as kglobalaccel expects.
BINDINGS = [
    # --- applications -----------------------------------------------------
    ("Apps", ["Meta+Return"], "Terminal", ("app", "org.kde.konsole.desktop", ["Ctrl+Alt+T"])),
    ("Apps", ["Meta+Shift+Return", "Meta+Shift+B"], "Browser", ("app", "firefox.desktop", [])),
    ("Apps", ["Meta+Shift+Alt+B"], "Browser (private)", ("cmd", "browser-private", "firefox --private-window")),
    ("Apps", ["Meta+Shift+F"], "File manager", ("app", "org.kde.dolphin.desktop", ["Meta+E"])),
    ("Apps", ["Meta+Shift+N"], "Editor", ("app", "org.kde.kate.desktop", [])),
    ("Apps", ["Meta+Shift+Q"], "Calculator", ("app", "org.kde.kcalc.desktop", ["Calculator"])),
    ("Apps", ["Meta+Shift+M"], "Music (Halcyon Media Player)", ("app", "halcyon-player.desktop", [])),
    ("Apps", ["Meta+Shift+T"], "Activity (btop)", ("cmd", "activity", "konsole --hide-menubar -e btop")),
    ("Apps", ["Meta+Shift+A"], "ChatGPT", ("cmd", "web-chatgpt", "firefox --new-window https://chatgpt.com")),
    ("Apps", ["Meta+Shift+Alt+A"], "Grok", ("cmd", "web-grok", "firefox --new-window https://grok.com")),
    ("Apps", ["Meta+Shift+C"], "Calendar", ("cmd", "web-calendar", "firefox --new-window https://app.hey.com/calendar/weeks/")),
    ("Apps", ["Meta+Shift+E"], "Email", ("cmd", "web-email", "firefox --new-window https://app.hey.com")),
    ("Apps", ["Meta+Shift+Y"], "YouTube", ("cmd", "web-youtube", "firefox --new-window https://youtube.com/")),
    ("Apps", ["Meta+Shift+Alt+G"], "WhatsApp", ("cmd", "web-whatsapp", "firefox --new-window https://web.whatsapp.com/")),
    ("Apps", ["Meta+Shift+Ctrl+G"], "Google Messages", ("cmd", "web-messages", "firefox --new-window https://messages.google.com/web/conversations")),
    ("Apps", ["Meta+Shift+P"], "Google Photos", ("cmd", "web-photos", "firefox --new-window https://photos.google.com/")),
    ("Apps", ["Meta+Shift+S"], "Google Maps", ("cmd", "web-maps", "firefox --new-window https://maps.google.com/")),
    ("Apps", ["Meta+Shift+X"], "X", ("cmd", "web-x", "firefox --new-window https://x.com/")),
    ("Apps", ["Meta+Shift+Alt+X"], "X post", ("cmd", "web-x-post", "firefox --new-window https://x.com/compose/post")),

    # --- windows ----------------------------------------------------------
    ("Windows", ["Meta+W", "Meta+Q"], "Close window", ("kde", "kwin", "Window Close", ["Alt+F4"])),
    ("Windows", ["Meta+F"], "Full screen", ("kde", "kwin", "Window Fullscreen", [])),
    ("Windows", ["Meta+Alt+F"], "Full width (maximize)", ("kde", "kwin", "Window Maximize", ["Meta+PgUp"])),
    ("Windows", ["Meta+M"], "Minimize window", ("kde", "kwin", "Window Minimize", ["Meta+PgDown"])),
    ("Windows", ["Meta+O"], "Pop window out (keep on top)", ("kde", "kwin", "Window Above Other Windows", [])),
    ("Windows", ["Meta+Left"], "Focus window left", ("kde", "kwin", "Switch Window Left", [])),
    ("Windows", ["Meta+Right"], "Focus window right", ("kde", "kwin", "Switch Window Right", [])),
    ("Windows", ["Meta+Up"], "Focus window above", ("kde", "kwin", "Switch Window Up", [])),
    ("Windows", ["Meta+Down"], "Focus window below", ("kde", "kwin", "Switch Window Down", [])),
    ("Windows", ["Meta+Ctrl+Left"], "Snap window left", ("kde", "kwin", "Window Quick Tile Left", [])),
    ("Windows", ["Meta+Ctrl+Right"], "Snap window right", ("kde", "kwin", "Window Quick Tile Right", [])),
    ("Windows", ["Meta+Ctrl+Up"], "Snap window top", ("kde", "kwin", "Window Quick Tile Top", [])),
    ("Windows", ["Meta+Ctrl+Down"], "Snap window bottom", ("kde", "kwin", "Window Quick Tile Bottom", [])),
    ("Windows", ["Alt+Tab"], "Next window", ("kde", "kwin", "Walk Through Windows", [])),
    ("Windows", ["Meta+Ctrl+Alt+Up"], "Overview", ("kde", "kwin", "Overview", [])),

    # --- tiling mode (Krohnkite; only active in tiling mode) -----------------
    ("Tiling", ["Meta+T"], "Toggle window floating/tiling", ("kde", "kwin", "KrohnkiteToggleFloat", [])),
    ("Tiling", ["Meta+J"], "Toggle window split", ("kde", "kwin", "KrohnkiteRotate", [])),
    ("Tiling", ["Meta+L"], "Cycle workspace layout", ("kde", "kwin", "KrohnkiteNextLayout", [])),
    ("Tiling", ["Meta+Alt+L"], "Cycle workspace layout back", ("kde", "kwin", "KrohnkitePreviousLayout", [])),
    ("Tiling", ["Meta+Shift+Left"], "Swap window left", ("kde", "kwin", "KrohnkiteShiftLeft", [])),
    ("Tiling", ["Meta+Shift+Right"], "Swap window right", ("kde", "kwin", "KrohnkiteShiftRight", [])),
    ("Tiling", ["Meta+Shift+Up"], "Swap window up", ("kde", "kwin", "KrohnkiteShiftUp", [])),
    ("Tiling", ["Meta+Shift+Down"], "Swap window down", ("kde", "kwin", "KrohnkiteShiftDown", [])),
    ("Tiling", ["Meta+-"], "Shrink window width", ("kde", "kwin", "KrohnkiteShrinkWidth", [])),
    ("Tiling", ["Meta+="], "Grow window width", ("kde", "kwin", "KrohnkitegrowWidth", [])),
    ("Tiling", ["Meta+Shift+-"], "Shrink window height", ("kde", "kwin", "KrohnkiteShrinkHeight", [])),
    ("Tiling", ["Meta+Shift+="], "Grow window height", ("kde", "kwin", "KrohnkiteGrowHeight", [])),
    ("Tiling", ["Meta+Ctrl+Alt+Space"], "Switch Classic / Tiling mode", ("cmd", "mode-toggle", "halcyon-mode toggle")),
    ("Tiling", ["Meta+Shift+Space"], "Toggle top bar", ("cmd", "bar-toggle", "halcyon-mode bar")),

    # --- workspaces -------------------------------------------------------
    *[("Workspaces", [f"Meta+{n % 10}"], f"Switch to workspace {n}",
       ("kde", "kwin", f"Switch to Desktop {n}", [])) for n in range(1, 11)],
    *[("Workspaces", [f"Meta+Shift+{n % 10}"], f"Move window to workspace {n}",
       ("kde", "kwin", f"Window to Desktop {n}", [])) for n in range(1, 11)],
    ("Workspaces", ["Meta+Tab"], "Next workspace", ("kde", "kwin", "Switch to Next Desktop", [])),
    ("Workspaces", ["Meta+Shift+Tab"], "Previous workspace", ("kde", "kwin", "Switch to Previous Desktop", [])),

    # --- system -----------------------------------------------------------
    ("System", ["Meta+Space"], "Launcher / search", ("app", "org.kde.krunner.desktop", ["Alt+Space", "Alt+F2", "Search"])),
    ("System", ["Meta+Alt+Space"], "Apps menu", ("kde", "plasmashell", "activate application launcher", ["Meta", "Alt+F1"])),
    ("System", ["Meta+Escape"], "System menu (log out / power)", ("kde", "ksmserver", "Log Out", ["Ctrl+Alt+Del"])),
    ("System", ["Meta+Ctrl+L"], "Lock system", ("kde", "ksmserver", "Lock Session", ["Screensaver"])),
    ("System", ["Meta+K"], "Show keybindings", ("cmd", "keys", "halcyon-keys")),
    ("System", ["Meta+Ctrl+F"], "Search (programs, files, e-mail…)", ("cmd", "search", "halcyon-search")),
    ("System", ["Meta+`"], "Quake console", ("kde", "yakuake", "toggle-window-state", ["F12"])),
    ("System", ["Meta+Ctrl+V"], "Clipboard manager", ("kde", "plasmashell", "show-on-mouse-pos", ["Meta+V"])),
    ("System", ["Meta+Ctrl+E"], "Emojis", ("app", "org.kde.plasma.emojier.desktop", ["Meta+."])),
    ("System", ["Meta+Ctrl+A"], "Audio settings", ("cmd", "audio", "kcmshell6 kcm_pulseaudio")),
    ("System", ["Meta+Ctrl+B"], "Bluetooth settings", ("cmd", "bluetooth", "kcmshell6 kcm_bluetooth")),
    ("System", ["Meta+Ctrl+N"], "Network settings", ("cmd", "network", "kcmshell6 kcm_networkmanagement")),
    ("System", ["Meta+Ctrl+D"], "Display settings", ("cmd", "display", "kcmshell6 kcm_kscreen")),
    ("System", ["Meta+Ctrl+P"], "Power settings", ("cmd", "power", "kcmshell6 kcm_powerdevilprofilesconfig")),

    # --- capture ----------------------------------------------------------
    ("Capture", ["Print"], "Screenshot", ("kde", "services/org.kde.spectacle.desktop", "_launch", [])),
    ("Capture", ["Meta+Ctrl+Alt+C"], "Screenshot (region)", ("kde", "services/org.kde.spectacle.desktop", "RectangularRegionScreenShot", ["Meta+Shift+Print"])),
    ("Capture", ["Alt+Print"], "Screen recording", ("kde", "services/org.kde.spectacle.desktop", "RecordRegion", ["Meta+Shift+R"])),
]

# Plasma defaults whose keys we need: keep the action, give it other keys.
OVERRIDE = [
    ("kwin", "Walk Through Windows (Reverse)", "Alt+Shift+Tab"),                     # was also Meta+Shift+Tab
    ("kwin", "Walk Through Windows of Current Application", "Alt+`"),               # was also Meta+`
    ("kwin", "Walk Through Windows of Current Application (Reverse)", "Alt+~"),
]

# Plasma defaults that collide with the keys above and must be freed.
UNBIND = [
    ("kwin", "Switch One Desktop to the Left"),  # Meta+Ctrl+arrows
    ("kwin", "Switch One Desktop to the Right"),
    ("kwin", "Switch One Desktop Up"),
    ("kwin", "Switch One Desktop Down"),
    ("kwin", "Window to Next Screen"),           # Meta+Shift+Right
    ("kwin", "Window to Previous Screen"),       # Meta+Shift+Left
    ("kwin", "view_zoom_in"),                    # Meta+= / Meta++
    ("kwin", "view_zoom_out"),                   # Meta+-
    ("kwin", "view_actual_size"),                # Meta+0
    ("kwin", "Activate Window Demanding Attention"),  # Meta+Ctrl+A
    ("plasmashell", "manage activities"),        # Meta+Q
    ("plasmashell", "next activity"),            # Meta+A
    ("plasmashell", "previous activity"),        # Meta+Shift+A
    *[("plasmashell", f"activate task manager entry {n}") for n in range(1, 11)],  # Meta+1..0
    ("kwin", "Edit Tiles"),                      # Meta+T
    ("kwin", "Grid View"),                       # Meta+G
    ("kwin", "Window One Desktop to the Left"),
    ("kwin", "Window One Desktop to the Right"),
    # Krohnkite's own defaults (Meta+H/J/K/L/F/M/Return/...) that we don't use.
    *[("kwin", a) for a in (
        "KrohnkiteFocusNext", "KrohnkiteFocusPrev", "KrohnkiteFocusDown", "KrohnkiteFocusUp",
        "KrohnkiteFocusLeft", "KrohnkiteFocusRight", "KrohnkiteIncrease", "KrohnkiteDecrease",
        "KrohnkiteFloatAll", "KrohnkiteRotatePart", "KrohnkiteSetMaster", "KrohnkiteTileLayout",
        "KrohnkiteMonocleLayout")],
]


# Qt may report Shift+<digit/symbol> as the shifted character (US layout),
# so bind both spellings.
SHIFTED = dict(zip("1234567890-=`", "!@#$%^&*()_+~"))


def with_shifted(keys):
    out = list(keys)
    for k in keys:
        mods, _, base = k.rpartition("+") if not k.endswith("++") else (k[:-2], "+", "+")
        if "Shift" in mods.split("+") and base in SHIFTED:
            out.append("+".join(m for m in mods.split("+") if m != "Shift") + "+" + SHIFTED[base])
    return out


def main():
    groups = {}   # section header -> {action: value}

    def put(component, action, value):
        if component.startswith("services/"):
            header = f"[services][{component.split('/', 1)[1]}]"
        else:
            header = f"[{component}]"
        groups.setdefault(header, {})[action] = value

    apps_dir = os.path.join(BR, "share", "applications")
    os.makedirs(apps_dir, exist_ok=True)
    for f in os.listdir(apps_dir):
        if f.startswith("halcyon-key-"):
            os.remove(os.path.join(apps_dir, f))

    cheat = []
    for section, keys, desc, target in BINDINGS:
        kind = target[0]
        if kind == "kde":
            _, comp, action, extra = target
            put(comp, action, "\\t".join(with_shifted(keys) + extra) + ",none," + desc)
        elif kind == "app":
            _, desktop, extra = target
            put(f"services/{desktop}", "_launch", "\\t".join(keys + extra))
        else:
            _, cid, cmd = target
            desktop = f"halcyon-key-{cid}.desktop"
            with open(os.path.join(apps_dir, desktop), "w") as f:
                f.write("[Desktop Entry]\nType=Application\n"
                        f"Name={desc}\nExec={cmd}\nIcon=halcyon-logo\nNoDisplay=true\n"
                        f"X-KDE-Shortcuts={keys[0]}\n")
            put(f"services/{desktop}", "_launch", "\\t".join(keys))
        cheat.append(f"{section}\t{' / '.join(keys)}\t{desc}")

    for comp, action in UNBIND:
        groups.setdefault(f"[{comp}]", {}).setdefault(action, "none,none," + action)
    for comp, action, keys in OVERRIDE:
        groups.setdefault(f"[{comp}]", {})[action] = keys + ",none," + action

    # Plasma's shortcut daemon only reads ~/.config/kglobalshortcutsrc, so this
    # is merged into each user's file at login by /usr/lib/halcyon/apply-shortcuts.
    out = os.path.join(BR, "share", "halcyon", "kglobalshortcutsrc")
    with open(out, "w") as f:
        f.write("# Generated by tools/gen_keys.py: Omarchy-style shortcuts for Halcyon.\n")
        for header in sorted(groups):
            f.write(f"\n{header}\n")
            for action, value in sorted(groups[header].items()):
                f.write(f"{action}={value}\n")

    cheat_path = os.path.join(BR, "share", "halcyon", "keybindings.tsv")
    os.makedirs(os.path.dirname(cheat_path), exist_ok=True)
    with open(cheat_path, "w") as f:
        f.write("\n".join(cheat) + "\n")

    # Sanity check: no key bound twice.
    seen = {}
    for _, keys, desc, _ in BINDINGS:
        for k in keys:
            if k in seen:
                raise SystemExit(f"duplicate key {k}: {seen[k]} / {desc}")
            seen[k] = desc
    print(f"{len(BINDINGS)} bindings, {len(seen)} keys")


if __name__ == "__main__":
    main()
