"""Audio CDs: read the disc's table of contents, look the album up on
MusicBrainz, play tracks through mpv and rip them with cdparanoia + ffmpeg.
"""
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.parse
import urllib.request

CDROMREADTOCHDR, CDROMREADTOCENTRY, CDROM_LBA, CDROM_LEADOUT = 0x5305, 0x5306, 0x01, 0xAA
MUSICBRAINZ = "https://musicbrainz.org/ws/2/discid/"
USER_AGENT = "HalcyonMediaPlayer/1.0 ( https://github.com/halcyon-linux )"
FORMATS = {   # name: (extension, ffmpeg codec args)
    "FLAC (lossless)": ("flac", ["-c:a", "flac"]),
    "MP3 (V2, ~190 kbps)": ("mp3", ["-c:a", "libmp3lame", "-q:a", "2"]),
    "Opus (160 kbps)": ("opus", ["-c:a", "libopus", "-b:a", "160k"]),
}


def find_drive():
    for d in sorted(os.listdir("/dev")):
        if re.fullmatch(r"sr\d+", d):
            return "/dev/" + d
    return None


def read_toc(device):
    """[(track_no, lba, is_data)] plus the lead-out LBA, via Linux CD ioctls."""
    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    try:
        hdr = fcntl.ioctl(fd, CDROMREADTOCHDR, bytes(2))
        first, last = hdr[0], hdr[1]
        tracks = []
        for t in list(range(first, last + 1)) + [CDROM_LEADOUT]:
            # struct cdrom_tocentry: u8 track, u8 adr:4|ctrl:4, u8 format, pad, int lba, u8 datamode
            buf = struct.pack("BBBxiBxxx", t, 0, CDROM_LBA, 0, 0)
            out = fcntl.ioctl(fd, CDROMREADTOCENTRY, buf)
            track, adr_ctrl, _fmt, lba, _mode = struct.unpack("BBBxiBxxx", out)
            ctrl = (adr_ctrl >> 4) & 0xF
            tracks.append((track, lba, bool(ctrl & 0x04)))
        leadout = tracks.pop()[1]
        return tracks, leadout
    finally:
        os.close(fd)


def musicbrainz_toc(tracks, leadout):
    first, last = tracks[0][0], tracks[-1][0]
    return first, last, leadout + 150, [lba + 150 for _, lba, _ in tracks]


def musicbrainz_id(tracks, leadout):
    """MusicBrainz disc ID: SHA-1 of the TOC, in MusicBrainz's base64 flavour."""
    first, last, lead, offsets = musicbrainz_toc(tracks, leadout)
    text = f"{first:02X}{last:02X}{lead:08X}" + "".join(f"{o:08X}" for o in (offsets + [0] * 99)[:99])
    digest = base64.b64encode(hashlib.sha1(text.encode()).digest()).decode()
    return digest.replace("+", ".").replace("/", "_").replace("=", "-")


def lookup(tracks, leadout):
    """{'artist','album','year','titles':{track_no: title}} or None."""
    disc = musicbrainz_id(tracks, leadout)
    first, last, lead, offsets = musicbrainz_toc(tracks, leadout)
    toc = "+".join(str(x) for x in [first, last, lead] + offsets)
    url = f"{MUSICBRAINZ}{disc}?toc={toc}&inc=recordings+artist-credits&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except (OSError, ValueError):
        return None
    releases = data.get("releases") or []
    if not releases:
        return None
    rel = releases[0]
    # Pick the medium (disc) of that release whose track count matches ours.
    media = rel.get("media") or []
    medium = next((m for m in media if any(d.get("id") == disc for d in m.get("discs") or [])), None) \
        or next((m for m in media if m.get("track-count") == len(tracks)), media[0] if media else {})
    credit = "".join(a.get("name", "") + a.get("joinphrase", "") for a in rel.get("artist-credit") or [])
    titles = {}
    for t in medium.get("tracks") or []:
        try:
            titles[int(t.get("number") or t.get("position"))] = t.get("title", "")
        except (TypeError, ValueError):
            pass
    return {"artist": credit, "album": rel.get("title", ""), "year": (rel.get("date") or "")[:4],
            "titles": titles, "disc_id": disc}


def audio_tracks(tracks):
    return [t for t in tracks if not t[2]]


def play_entries(device, tracks, info=None):
    """Playlist entries for the player: one per audio track, using mpv's cdda
    chapters (chapter k = k-th audio track)."""
    out = []
    for k, (no, _, _) in enumerate(audio_tracks(tracks), start=1):
        title = (info or {}).get("titles", {}).get(no) or f"Track {no:02d}"
        out.append({"url": f"cdda://{device}", "title": title, "chapter": k})
    return out


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s).strip(" .") or "Unknown"


def rip(device, jobs, fmt, dest_dir, meta, progress=lambda done, total, msg: None, cancelled=lambda: False):
    """Rip jobs [(track_no, title)] into dest_dir. Returns written file paths."""
    ext, codec = FORMATS[fmt]
    os.makedirs(dest_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="halcyon-rip-")
    written = []
    try:
        for i, (no, title) in enumerate(jobs):
            if cancelled():
                break
            progress(i, len(jobs), f"Reading track {no}: {title}")
            wav = os.path.join(tmp, f"{no:02d}.wav")
            r = subprocess.run(["cdparanoia", "-q", "-d", device, str(no), wav],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if r.returncode != 0 or not os.path.exists(wav):
                raise RuntimeError(f"Couldn't read track {no}: {r.stderr.strip()[-200:]}")
            out = os.path.join(dest_dir, f"{no:02d} - {safe_name(title)}.{ext}")
            tags = ["-metadata", f"title={title}", "-metadata", f"track={no}",
                    "-metadata", f"artist={meta.get('artist', '')}",
                    "-metadata", f"album_artist={meta.get('artist', '')}",
                    "-metadata", f"album={meta.get('album', '')}", "-metadata", f"date={meta.get('year', '')}"]
            progress(i, len(jobs), f"Encoding track {no}: {title}")
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, *codec, *tags, out],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"Couldn't encode track {no}: {r.stderr.strip()[-200:]}")
            os.remove(wav)
            written.append(out)
        progress(len(jobs), len(jobs), "Done")
        return written
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
