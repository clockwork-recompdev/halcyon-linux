#!/usr/bin/env bash
# Try Halcyon in a QEMU/KVM virtual machine (UEFI, 3D-accelerated display).
#
#   ./vm.sh            boot the newest ISO in out/ (live session; can install to the VM disk)
#   ./vm.sh --disk     boot the installed system from the VM disk
#   ./vm.sh --reset    delete the VM disk and UEFI settings, then boot the ISO
#
# Needs: sudo pacman -S --needed qemu-desktop edk2-ovmf
#
# A second, empty CD drive takes game/movie discs: insert one while the VM runs
# with ./vm-disc.sh <image.iso> (and ./vm-disc.sh eject).
#
# The keyboard is grabbed while the mouse is over the VM so Super-key shortcuts
# reach Halcyon instead of the host desktop (Ctrl+Alt+G toggles the grab).
set -euo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
VM=$HERE/vm
DISK=$VM/halcyon.qcow2
VARS=$VM/OVMF_VARS.4m.fd
OVMF=/usr/share/edk2/x64

command -v qemu-system-x86_64 >/dev/null && [[ -f $OVMF/OVMF_CODE.4m.fd ]] ||
    { echo "Install QEMU first: sudo pacman -S --needed qemu-desktop edk2-ovmf" >&2; exit 1; }

[[ ${1:-} == --reset ]] && rm -rf -- "$VM"
mkdir -p "$VM"
[[ -f $DISK ]] || qemu-img create -q -f qcow2 "$DISK" 40G
[[ -f $VARS ]] || cp "$OVMF/OVMF_VARS.4m.fd" "$VARS"

boot=()
if [[ ${1:-} != --disk ]]; then
    iso=$(ls -t "$HERE"/out/halcyon-*.iso 2>/dev/null | head -1)
    [[ -n $iso ]] || { echo "No ISO in out/; run ./build.sh first." >&2; exit 1; }
    echo "Booting $(basename "$iso")"
    boot=(-drive "file=$iso,media=cdrom,readonly=on" -boot order=d)
fi

exec qemu-system-x86_64 \
    -name Halcyon -enable-kvm -machine q35 -cpu host -smp 4 -m 6G \
    -drive "if=pflash,format=raw,readonly=on,file=$OVMF/OVMF_CODE.4m.fd" \
    -drive "if=pflash,format=raw,file=$VARS" \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    "${boot[@]}" \
    -device virtio-vga-gl -display gtk,gl=on,zoom-to-fit=on,grab-on-hover=on \
    -audiodev pipewire,id=snd -device intel-hda -device hda-duplex,audiodev=snd \
    -nic user,model=virtio-net-pci \
    -virtfs "local,path=$HERE,mount_tag=halcyon,security_model=none,readonly=on" \
    -drive if=none,id=disc,media=cdrom,readonly=on -device ide-cd,drive=disc,bus=ide.4 \
    -monitor "unix:$VM/monitor.sock,server,nowait" \
    -device qemu-xhci -device usb-tablet
