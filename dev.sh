#!/usr/bin/env bash
# Fast theme iteration: rebuild only the halcyon-branding package (~10 s).
# Then, inside the VM (started with ./vm.sh), run:  halcyon-dev
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
W=$HERE/work
mkdir -p "$W/dev" "$W/sources"
rm -f "$W"/dev/halcyon-branding-*.pkg.tar.zst
(cd "$HERE/branding" && BUILDDIR="$W/makepkg-dev" SRCDEST="$W/sources" PKGDEST="$W/dev" \
    makepkg -f --nodeps --noconfirm >/dev/null)
echo "Built $(ls "$W"/dev/*.pkg.tar.zst). In the VM run: halcyon-dev"
