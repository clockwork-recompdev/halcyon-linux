#!/usr/bin/env bash
# Run inside the VM:  bash /mnt/tools/diag-keys.sh
# Prints why Halcyon shortcuts might not be working. Screenshot the output.
say() { printf '\n\e[1;36m== %s\e[0m\n' "$*"; }

say "package / login hook"
pacman -Q halcyon-branding 2>&1
ls -l /etc/xdg/plasma-workspace/env/ /usr/lib/halcyon/apply-shortcuts 2>&1 | sed 's/^/  /'
ls ~/.local/state/halcyon/ 2>&1 | sed 's/^/  state: /'

say "user shortcut file"
f=~/.config/kglobalshortcutsrc
grep -c . "$f" 2>&1 | sed 's/^/  lines: /'
grep -E '^(Window Close|Switch to Desktop 1|Lock Session)=' "$f" 2>&1 | sed 's/^/  /'
grep -A1 -E '^\[services\]\[(org.kde.konsole.desktop|halcyon-key-keys.desktop)\]' "$f" 2>&1 | sed 's/^/  /'

say "what KDE has actually registered (live, from the shortcut daemon)"
decode() {  # Qt key code -> "Meta+Shift+K"
    awk '{ for (i = 1; i <= NF; i++) { c = $i; s = ""
        if (c >= 268435456) { s = s "Meta+"; c -= 268435456 }
        if (c >= 134217728) { s = s "Alt+";  c -= 134217728 }
        if (c >= 67108864)  { s = s "Ctrl+"; c -= 67108864 }
        if (c >= 33554432)  { s = s "Shift+"; c -= 33554432 }
        if (c >= 32 && c < 127) s = s sprintf("%c", c); else s = s sprintf("0x%x", c)
        printf "%s ", s } }'
}
q() {  # q <component object path> <action>: active keys, or "(not registered)"
    local out
    out=$(dbus-send --session --print-reply=literal --dest=org.kde.kglobalaccel "/component/$1" \
        org.kde.kglobalaccel.Component.allShortcutInfos 2>&1 |
        awk -v a="$2" '$1 == a || index($0, " " a " ") { hit = 1 } hit && /int32/ { printf "%s ", $2 } hit && /\]/ { exit }')
    if [[ -z $out ]]; then echo "(not registered or no keys)"; else echo "$out" | decode; echo; fi
}
printf '  kwin/Window Close:        '; q kwin "Window Close"
printf '  kwin/Switch to Desktop 1: '; q kwin "Switch to Desktop 1"
printf '  konsole _launch:          '; q org_kde_konsole_desktop _launch
printf '  halcyon-key-keys _launch: '; q halcyon_key_keys_desktop _launch
echo "  components: $(dbus-send --session --print-reply=literal --dest=org.kde.kglobalaccel /kglobalaccel \
    org.kde.KGlobalAccel.allComponents 2>&1 | grep -oE '/component/[^ ]+' | sed 's|/component/||' | tr '\n' ' ' | cut -c1-300)"

say "commands"
for c in halcyon-keys halcyon-mode kwriteconfig6 yakuake; do printf '  %-14s %s\n' "$c" "$(command -v $c || echo MISSING)"; done

say "recent shortcut-related log lines"
journalctl --user -b --no-pager 2>/dev/null | grep -iE 'kglobalaccel|globalshortcut|shortcut' | tail -8 | cut -c1-160
