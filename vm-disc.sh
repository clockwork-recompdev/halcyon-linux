#!/usr/bin/env bash
# Insert or eject a disc in the running Halcyon VM's second CD drive.
#   ./vm-disc.sh "Dawn of War Disc 1.iso"
#   ./vm-disc.sh eject
#   ./vm-disc.sh drive      hand this PC's real CD/DVD drive to the VM
#   ./vm-disc.sh release    give it back
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOCK=$HERE/vm/monitor.sock
[[ -S $SOCK ]] || { echo "VM isn't running (start it with ./vm.sh)." >&2; exit 1; }
[[ $# -eq 1 ]] || { echo "usage: $0 <disc image> | eject | drive | release" >&2; exit 2; }

if [[ $1 == drive ]]; then
    sr=$(ls -d /sys/class/block/sr* 2>/dev/null | head -1)
    [[ -n $sr ]] || { echo "No CD/DVD drive found on this PC." >&2; exit 1; }
    dev=$(readlink -f "$sr/device")
    usb=$dev
    while [[ $usb != / && ! -f $usb/busnum ]]; do usb=$(dirname "$usb"); done
    if [[ -f $usb/busnum ]]; then
        # USB drive: pass the whole device through (audio CDs, tray, burning all work).
        bus=$(<"$usb/busnum") addr=$(<"$usb/devnum")
        node=$(printf '/dev/bus/usb/%03d/%03d' "$bus" "$addr")
        if [[ ! -w $node ]]; then
            echo "Allowing QEMU to use the USB drive ($node) — needs your password once:"
            sudo setfacl -m "u:$USER:rw" "$node"
        fi
        echo "Passing $(<"$usb/manufacturer" 2>/dev/null) $(<"$usb/product" 2>/dev/null) to the VM…"
        cmd="device_add usb-host,hostbus=$bus,hostaddr=$addr,id=hostcd"
    else
        # Internal (SATA) drive: share it as the VM's second CD drive.
        cmd="change disc /dev/${sr##*/} host_cdrom"
    fi
elif [[ $1 == release ]]; then
    cmd="device_del hostcd"
elif [[ $1 == eject ]]; then
    cmd="eject -f disc"
else
    img=$(readlink -f -- "$1")
    [[ -f $img ]] || { echo "No such file: $1" >&2; exit 1; }
    case ${img,,} in
        *.cue|*.bin) echo "Use the .iso version (QEMU can't read raw .bin/.cue images)." >&2; exit 1 ;;
    esac
    cmd="change disc \"$img\" raw read-only"
fi

python3 -I - "$SOCK" "$cmd" <<'PY'
import socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
time.sleep(0.2); s.recv(4096)            # monitor banner
s.sendall((sys.argv[2] + "\n").encode())
time.sleep(0.5)
out = s.recv(65536).decode(errors="replace")
import re
out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)   # monitor echoes keystrokes with escapes
bad = [l for l in out.splitlines() if re.search(r"(?i)could not|error|not found|failed", l)]
msg = {"eject": "Ejected.", "device_add": "Drive connected to the VM.", "device_del": "Drive given back to this PC."}
print("\n".join(bad) if bad else next((v for k, v in msg.items() if sys.argv[2].startswith(k)), "Disc inserted."))
sys.exit(1 if bad else 0)
PY
