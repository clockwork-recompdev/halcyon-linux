#!/usr/bin/env bash
# Build the Halcyon Linux live ISO.
#
#   ./build.sh            build branding package + ISO into ./out
#   ./build.sh --clean    also wipe the previous work directory first
#
# Run as your normal user; it uses sudo for the steps that need root.
set -euo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
WORK=${WORK:-$HERE/work}
OUT=${OUT:-$HERE/out}
PROFILE=$WORK/profile
REPO=$WORK/repo

msg() { printf '\e[38;2;30;155;224m==>\e[0m \e[1m%s\e[0m\n' "$*"; }
die() { printf '\e[31mError:\e[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -ne 0 ]] || die "Run as a normal user (makepkg refuses root); sudo is used where needed."

# An interrupted mkarchiso leaves the host's /dev, /proc, /sys (and efivars!)
# mounted inside $WORK/build. A plain "rm -rf" then deletes the host's real
# device nodes and UEFI boot entries. So: unmount first, refuse if anything
# is still mounted, and never let rm cross a filesystem boundary.
safe_wipe() {
    local dir=$1 m
    [[ -e $dir ]] || return 0
    dir=$(readlink -f -- "$dir")
    while read -r m; do
        sudo umount -- "$m" || sudo umount -l -- "$m"
    done < <(findmnt -rn -o TARGET | awk -v p="$dir/" 'index($0, p) == 1' | sort -r)
    if findmnt -rn -o TARGET | awk -v p="$dir/" 'index($0, p) == 1' | grep -q .; then
        die "Something is still mounted inside $dir; not deleting it. Reboot, then run ./build.sh again."
    fi
    sudo rm -rf --one-file-system -- "$dir"
}

if pgrep -f "mkarchiso .*-w $WORK/build" >/dev/null; then
    die "A Halcyon build is already running. Wait for it to finish (or stop it) first."
fi

# ------------------------------------------------------------ preflight ---
assets=$HERE/branding/assets
[[ -f $assets/logo.png || -f $assets/logo.svg ]] || die "Put your logo at branding/assets/logo.png (or .svg)."
compgen -G "$assets/wallpapers/*.*" >/dev/null || die "Put at least one wallpaper in branding/assets/wallpapers/."

need=()
for p in archiso librsvg imagemagick python; do pacman -Q "$p" &>/dev/null || need+=("$p"); done
if ((${#need[@]})); then
    msg "Installing build tools: ${need[*]}"
    sudo pacman -S --needed --noconfirm "${need[@]}"
fi

if [[ ${1:-} == --clean ]]; then
    msg "Cleaning $WORK"
    safe_wipe "$WORK"
fi
mkdir -p "$WORK/sources" "$OUT"

# ---------------------------------------------------- branding package ---
msg "Building halcyon-branding package"
rm -rf "$REPO" && mkdir -p "$REPO"
(cd "$HERE/branding" && BUILDDIR="$WORK/makepkg" SRCDEST="$WORK/sources" PKGDEST="$REPO" makepkg -f --nodeps --noconfirm)
pkg=$(ls "$REPO"/halcyon-branding-*.pkg.tar.zst)
repo-add -q "$REPO/halcyon.db.tar.gz" "$pkg"

# ------------------------------------------------------------- profile ---
msg "Assembling archiso profile"
safe_wipe "$PROFILE"
cp -a "${RELENG:-/usr/share/archiso/configs/releng}" "$PROFILE"
air=$PROFILE/airootfs

# Identity
sed -i \
    -e 's/^iso_name=.*/iso_name="halcyon"/' \
    -e 's/^iso_label=.*/iso_label="HALCYON_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"/' \
    -e 's|^iso_publisher=.*|iso_publisher="Halcyon Linux"|' \
    -e 's|^iso_application=.*|iso_application="Halcyon Linux Live"|' \
    -e 's/^install_dir=.*/install_dir="halcyon"/' \
    "$PROFILE/profiledef.sh"
# zstd instead of xz: a bit bigger, but unpacks several times faster, which is
# most of the installer's runtime (and speeds up the live session too).
sed -i "s/^airootfs_image_tool_options=.*/airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '15' '-b' '1M')/" \
    "$PROFILE/profiledef.sh"
echo halcyon > "$air/etc/hostname"

# Local repo with the branding package
cat >> "$PROFILE/pacman.conf" <<EOF

[halcyon]
SigLevel = Optional TrustAll
Server = file://$REPO
EOF

# Packages: releng base minus server-ish bits, plus the desktop
sed -i -E '/^(cloud-init|iwd|irssi|lftp|lynx|mc|vpnc|wvdial|xl2tpd|pptpclient|clonezilla|partimage|darkhttpd)$/d' \
    "$PROFILE/packages.x86_64"
{ echo; grep -Ev '^\s*(#|$)' "$HERE/branding/desktop-packages.txt"; echo halcyon-branding; } \
    >> "$PROFILE/packages.x86_64"

# Services: NetworkManager + SDDM instead of networkd/iwd; graphical boot
wants=$air/etc/systemd/system
rm -f "$wants"/multi-user.target.wants/{systemd-networkd,iwd}.service \
      "$wants"/sockets.target.wants/systemd-networkd.socket \
      "$wants"/network-online.target.wants/systemd-networkd-wait-online.service \
      "$wants"/dbus-org.freedesktop.network1.service
rm -rf "$wants"/cloud-init.target.wants "$wants"/getty@tty1.service.d
ln -sf /usr/lib/systemd/system/NetworkManager.service "$wants/multi-user.target.wants/NetworkManager.service"
ln -sf /usr/lib/systemd/system/NetworkManager-dispatcher.service "$wants/dbus-org.freedesktop.nm-dispatcher.service"
ln -sf /usr/lib/systemd/system/sddm.service "$wants/display-manager.service"
ln -sf /usr/lib/systemd/system/graphical.target "$wants/default.target"

# Overlay our live-session files (live user, autologin, installer icon)
cp -a "$HERE/profile/airootfs/." "$air/"
echo 'liveuser:x:1000:100:Live User:/home/liveuser:/bin/bash' >> "$air/etc/passwd"
echo 'liveuser::14871::::::' >> "$air/etc/shadow"
install -Dm644 "$pkg" "$air/usr/share/halcyon/pkgs/$(basename "$pkg")"
sed -i '${/^)$/d}' "$PROFILE/profiledef.sh"
cat >> "$PROFILE/profiledef.sh" <<'EOF'
  ["/etc/sudoers.d"]="0:0:750"
  ["/etc/sudoers.d/liveuser"]="0:0:440"
  ["/etc/skel/Desktop/halcyon-install.desktop"]="0:0:755"
)
EOF

# Plymouth in the live initramfs
sed -i 's/\budev\b/udev plymouth/' "$air/etc/mkinitcpio.conf.d/archiso.conf"

# Boot menus: names, splash params, theme
for f in "$PROFILE"/syslinux/*.cfg "$PROFILE"/efiboot/loader/entries/*.conf "$PROFILE"/grub/*.cfg; do
    sed -i -e 's/Arch Linux install medium/Halcyon Linux live medium/g' \
           -e 's/Arch Linux/Halcyon Linux/g' "$f"
done
sed -i -E '/archisobasedir=/ { /accessibility=on/! s/%KERNEL_PARAMS%/quiet splash cow_spacesize=4G %KERNEL_PARAMS%/ }' \
    "$PROFILE"/syslinux/*.cfg "$PROFILE"/efiboot/loader/entries/*.conf "$PROFILE"/grub/*.cfg
sed -i -E \
    -e 's/^(MENU COLOR border\s+\S+\s+)\S+ \S+/\1#80c8f0ff #00000000/' \
    -e 's/^(MENU COLOR title\s+\S+\s+)\S+ \S+/\1#ffffffff #00000000/' \
    -e 's/^(MENU COLOR sel\s+\S+\s+)\S+ \S+/\1#ffffffff #c01e9be0/' \
    -e 's/^(MENU COLOR unsel\s+\S+\s+)\S+ \S+/\1#e0ffffff #600a3456/' \
    "$PROFILE/syslinux/archiso_head.cfg"

# BIOS boot menu background: plain sky gradient (no wallpaper)
magick -size 640x480 gradient:'#0a5fa8-#8cd9f8' -depth 8 "PNG24:$PROFILE/syslinux/splash.png"

# ----------------------------------------------------------------- iso ---
msg "Running mkarchiso (this takes a while)"
# mkarchiso skips steps it has finished before; start fresh so changes land.
safe_wipe "$WORK/build"
sudo mkarchiso -v -w "$WORK/build" -o "$OUT" "$PROFILE"
msg "Done: $(ls -t "$OUT"/halcyon-*.iso | head -1)"
