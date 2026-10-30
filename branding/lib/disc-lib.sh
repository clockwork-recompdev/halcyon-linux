# Shared helpers for Halcyon's disc tools. Source, don't run.

disc_find() {  # disc_find ROOT REL/PATH: case-insensitive lookup on a disc
    local cur=$1 part parts
    IFS='/' read -ra parts <<<"${2//\\//}"
    for part in "${parts[@]}"; do
        [[ -z $part || $part == . ]] && continue
        cur=$(find "$cur" -mindepth 1 -maxdepth 1 -iname "$part" -print -quit 2>/dev/null)
        [[ -n $cur ]] || return 1
    done
    echo "$cur"
}

disc_inf_value() {  # disc_inf_value ROOT KEY: value of KEY= in autorun.inf
    local inf
    inf=$(disc_find "$1" autorun.inf) || return 1
    tr -d '\r' < "$inf" | sed -n "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*//Ip" | head -1
}

disc_title() {  # disc_title ROOT VOLUME_LABEL: best human name for the disc
    local root=$1 vol=${2:-} t=""
    if [[ -n $root ]]; then
        t=$(disc_inf_value "$root" label)
        # Installers often leave ProductName=/AppName= in a root .ini (Wise, InstallShield).
        [[ -n $t ]] || t=$(find "$root" -maxdepth 1 -iname '*.ini' -size -64k -exec \
            sed -n 's/^[[:space:]]*\(ProductName\|AppName\)[[:space:]]*=[[:space:]]*//Ip' {} + 2>/dev/null | tr -d '\r' | head -1)
    fi
    [[ -n $t ]] || t=${vol//_/ }
    echo "${t:-Disc}"
}

disc_protection() {  # disc_protection ROOT: prints "SafeDisc"/"SecuROM"/"StarForce" if found
    local root=$1
    if find "$root" -maxdepth 2 \( -iname '00000001.tmp' -o -iname 'clcd32.dll' -o -iname 'clokspl.exe' \
            -o -iname 'dplayerx.dll' -o -iname 'drvmgt.dll' -o -iname 'secdrv.sys' \) -print -quit | grep -q .; then
        echo SafeDisc; return
    fi
    if find "$root" -maxdepth 2 \( -iname 'protect.dll' -o -iname 'protect.exe' -o -iname 'sfdrvup*' \) -print -quit | grep -q .; then
        echo StarForce; return
    fi
    # SecuROM leaves its name in the installer or game executables' strings.
    if find "$root" -maxdepth 1 -type f \( -iname '*.exe' -o -iname '*.msi' -o -iname '*.dll' \) -size -64M \
            -exec grep -lai -m1 'securom' {} + 2>/dev/null | grep -q .; then
        echo SecuROM; return
    fi
}

disc_icon_png() {  # disc_icon_png ROOT OUT.png: autorun icon (.ico, .exe or .dll) as PNG
    local root=$1 out=$2 spec file idx tmp
    spec=$(disc_inf_value "$root" icon) || return 1
    [[ -n $spec ]] || return 1
    file=${spec%%,*}; idx=0
    [[ $spec == *,* ]] && idx=${spec##*,}
    file=$(disc_find "$root" "$file") || return 1
    tmp=$(mktemp -d)
    case ${file,,} in
        *.exe|*.dll)
            # Pull icon group resources out of the program, then convert.
            wrestool -x -t 14 -o "$tmp" "$file" 2>/dev/null
            file=$(ls "$tmp"/*.ico 2>/dev/null | sed -n "$((${idx#-} + 1))p")
            [[ -n $file ]] || file=$(ls "$tmp"/*.ico 2>/dev/null | head -1)
            ;;
    esac
    local ok=1 best
    if [[ -n $file ]]; then
        # .ico files hold several sizes; use the largest.
        best=$(magick identify -format '%p %w\n' "$file" 2>/dev/null | sort -k2 -n | tail -1 | cut -d' ' -f1)
        magick "${file}[${best:-0}]" -resize 64x64 "$out" 2>/dev/null && ok=0
    fi
    rm -rf "$tmp"
    return $ok
}

disc_point_d() {  # disc_point_d CDLINK: aim the Windows environment's D: drive at the current disc
    # dosdevices/d: is the disc's files; d:: is the raw device, which is where
    # Wine reads the volume label and serial games check for ("insert disc").
    # Real drives are /dev/srN; a mounted ISO is a loop device, which works too.
    local cdlink=$1 prefix=${HALCYON_WINEPREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/halcyon/windows} dev fstype
    [[ -d $prefix/dosdevices ]] || return 0
    ln -sfn "$cdlink" "$prefix/dosdevices/d:"
    read -r dev fstype < <(findmnt -n -o SOURCE,FSTYPE --target "$(readlink -f "$cdlink")" 2>/dev/null)
    if [[ -b $dev && ( $dev == /dev/sr* || $dev == /dev/loop* || $fstype == iso9660 || $fstype == udf ) ]]; then
        ln -sfn "$dev" "$prefix/dosdevices/d::"
    else
        rm -f "$prefix/dosdevices/d::"
    fi
}

# ------------------------------------------------- old installer quirks ---
# Some 2001-era MSI installers refuse to run for reasons that don't apply
# under Wine. For those, run a copy of the MSI with the offending UI checks
# switched off. Keyed by the MSI's ProductName; values are InstallUISequence
# actions to skip.
declare -A MSI_SKIP=(
    # "can't be installed on Windows XP 64-bit Edition" (Wine is 64-bit), and a
    # video memory warning: Wine stores 4 GB+ cards' memory in a 32-bit value as 0.
    ["Microsoft Plus! for Windows XP"]="CheckCPUType VidChkScript"
)

msi_quirk_fix() {  # msi_quirk_fix DISC_ROOT WORKDIR: prints the patched MSI's path, if one is needed
    local root=$1 work=$2 msi name skip e
    msi=$(find "$root" -maxdepth 1 -iname '*.msi' -print -quit)
    [[ -n $msi ]] && command -v msiinfo >/dev/null || return 1
    name=$(msiinfo export "$msi" Property 2>/dev/null | awk -F'\t' '$1 == "ProductName" { sub(/\r$/, "", $2); print $2 }')
    skip=${MSI_SKIP[$name]:-}
    [[ -n $skip ]] || return 1
    rm -rf "$work" && mkdir -p "$work"
    # The MSI finds its files next to itself: link the disc's contents in.
    for e in "$root"/*; do
        [[ $e -ef $msi ]] || ln -s "$e" "$work/$(basename "$e")"
    done
    cp "$msi" "$work/setup.msi" && chmod u+w "$work/setup.msi"
    msiinfo export "$msi" InstallUISequence > "$work/InstallUISequence.idt" 2>/dev/null || return 1
    awk -F'\t' -v OFS='\t' -v skip=" $skip " 'index(skip, " " $1 " ") { $2 = "0" } { print }' \
        "$work/InstallUISequence.idt" > "$work/seq.tmp" && mv "$work/seq.tmp" "$work/InstallUISequence.idt"
    # Wine's own msidb writes the table (msitools mangles this MSI's string pool).
    (cd "$work" && wine msidb -d "setup.msi" -f "." -i InstallUISequence.idt) >/dev/null 2>&1 || return 1
    wineserver -w
    echo "$work/setup.msi"
}

msi_packed() {  # msi_packed {GUID}: Windows Installer's "packed" form used in registry keys
    python3 -c '
import sys
g = sys.argv[1].strip("{}").replace("-", "")
parts = [g[0:8][::-1], g[8:12][::-1], g[12:16][::-1]] + [g[i:i + 2][::-1] for i in range(16, 32, 2)]
print("".join(parts).upper())' "$1"
}

msi_registered() {  # msi_registered {GUID} PACKED: is the product registered as installed?
    # 32-bit installers register under Wow6432Node; "wine uninstaller --list" can miss them.
    wine reg query "HKLM\\Software\\Classes\\Installer\\Products\\$2" >/dev/null 2>&1 ||
        wine reg query "HKLM\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\$1" >/dev/null 2>&1 ||
        wine reg query "HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\$1" >/dev/null 2>&1
}

msi_clear_partial() {  # msi_clear_partial MSI: remove an earlier (possibly half-finished) install of this product
    local msi=$1 code upgrade p u key
    code=$(msiinfo export "$msi" Property 2>/dev/null | awk -F'\t' '$1 == "ProductCode" { sub(/\r$/, "", $2); print $2 }')
    upgrade=$(msiinfo export "$msi" Property 2>/dev/null | awk -F'\t' '$1 == "UpgradeCode" { sub(/\r$/, "", $2); print $2 }')
    [[ -n $code ]] || return 0
    p=$(msi_packed "$code")
    msi_registered "$code" "$p" || return 0
    # A clean uninstall first; a half-finished install often can't (its cached
    # package is what's broken), so then drop its registration by hand.
    wine msiexec /x "$code" /qn >/dev/null 2>&1
    wineserver -w
    msi_registered "$code" "$p" || return 0
    for key in "HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\$code" \
               "HKLM\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\$code" \
               "HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Installer\\UserData\\S-1-5-18\\Products\\$p" \
               "HKLM\\Software\\Classes\\Installer\\Products\\$p" \
               "HKCU\\Software\\Microsoft\\Installer\\Products\\$p"; do
        wine reg delete "$key" /f >/dev/null 2>&1
    done
    if [[ -n $upgrade ]]; then
        u=$(msi_packed "$upgrade")
        wine reg delete "HKLM\\Software\\Classes\\Installer\\UpgradeCodes\\$u" /f >/dev/null 2>&1
    fi
    wineserver -w
}
