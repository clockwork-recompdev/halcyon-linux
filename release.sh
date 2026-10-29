#!/usr/bin/env bash
# Publish a Halcyon update: bump the version, build halcyon-branding, and
# upload it as a pacman repo on a GitHub release. Installed systems get it
# with their normal update (pacman -Syu / halcyon-update).
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$HERE"
VER=$(date +%Y.%m.%d.%H%M)
sed -i "s/^pkgver=.*/pkgver=$VER/" branding/PKGBUILD
./dev.sh
R=work/release
rm -rf "$R" && mkdir -p "$R"
cp work/dev/halcyon-branding-*.pkg.tar.zst "$R/"
repo-add -q "$R/halcyon.db.tar.gz" "$R"/halcyon-branding-*.pkg.tar.zst
# GitHub releases can't hold symlinks: make halcyon.db/.files real files.
for f in halcyon.db halcyon.files; do cp --remove-destination "$(readlink -f "$R/$f")" "$R/$f"; done
git add -A
git commit -qm "Release $VER" || true
git push -q
gh release create "v$VER" "$R"/* --title "Halcyon $VER" \
    --notes "Halcyon package update. Installed systems pick it up with halcyon-update (or pacman -Syu)."
echo "Released v$VER"
