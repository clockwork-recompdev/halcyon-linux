"""Movie discs for Media Center: find a DVD or Blu-ray in the drive, play it
full screen in mpv (main title), open its menus in VLC, eject it."""
import glob
import os
import shutil
import subprocess


def _mount_of(dev):
    try:
        out = subprocess.run(["findmnt", "-n", "-o", "TARGET", "--source", dev], capture_output=True, text=True,
                             timeout=3).stdout.strip().splitlines()
        return out[0] if out else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _label(dev, mount):
    try:
        lab = subprocess.run(["lsblk", "-no", "LABEL", dev], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        lab = ""
    lab = lab or (os.path.basename(mount) if mount else "")
    # STAR_WARS_EP4 -> Star Wars Ep4
    pretty = " ".join(w.capitalize() if w.isupper() or w.islower() else w for w in lab.replace("_", " ").split())
    return pretty or "Movie disc"


def _has(mount, name):
    try:
        return any(e.lower() == name.lower() for e in os.listdir(mount))
    except OSError:
        return False


def detect():
    """[{device, mount, label, kind: 'dvd'|'bluray'}] for video discs in the drives."""
    out = []
    for dev in sorted(glob.glob("/dev/sr[0-9]*")):
        mount = _mount_of(dev)
        if not mount:
            continue
        if _has(mount, "BDMV"):
            kind = "bluray"
        elif _has(mount, "VIDEO_TS"):
            kind = "dvd"
        else:
            continue
        out.append({"device": dev, "mount": mount, "label": _label(dev, mount), "kind": kind})
    return out


def play(disc):
    """Main title full screen. Returns the mpv process."""
    if disc["kind"] == "bluray":
        cmd = ["mpv", "--fs", "--force-window=immediate", f"--bluray-device={disc['mount']}", "bd://"]
    else:
        cmd = ["mpv", "--fs", "--force-window=immediate", f"--dvd-device={disc['device']}", "dvd://"]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def can_show_menu():
    return shutil.which("vlc") is not None


def show_menu(disc):
    url = ("bluray://" if disc["kind"] == "bluray" else "dvd://") + (disc["mount"] if disc["kind"] == "bluray"
                                                                      else disc["device"])
    return subprocess.Popen(["vlc", "--fullscreen", "--play-and-exit", url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def eject(disc):
    subprocess.Popen(["eject", disc["device"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
