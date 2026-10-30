"""Music playback for Media Center: one background mpv (no window) driven over
its IPC socket, so songs keep playing while you browse."""
import json
import os
import socket
import subprocess
import tempfile


class MusicPlayer:
    def __init__(self):
        self.proc = None
        self.sock_path = os.path.join(tempfile.gettempdir(), f"halcyon-mc-music-{os.getpid()}.sock")
        self.conn = None
        self.queue = []            # track dicts
        self.index = -1
        self.position = 0.0
        self.duration = 0.0
        self.paused = False
        self.headers = None
        self.extra = os.environ.get("HALCYON_MC_MPV_ARGS", "").split()   # e.g. --ao=null for tests

    # --- process
    def _start(self, headers):
        self.stop()
        cmd = ["mpv", "--idle=yes", "--no-video", "--no-terminal", "--keep-open=no",
               f"--input-ipc-server={self.sock_path}"] + self.extra
        if headers:
            cmd.append("--http-header-fields=" + ",".join(h.replace(",", r"\,") for h in headers))
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.headers = headers
        self.conn = None

    def _send(self, *command, rid=0):
        msg = {"command": list(command)}
        if rid:
            msg["request_id"] = rid
        for _ in range(40):          # mpv takes a moment to open its socket
            try:
                if self.conn is None:
                    self.conn = socket.socket(socket.AF_UNIX)
                    self.conn.settimeout(0.5)
                    self.conn.connect(self.sock_path)
                    self.buf = b""
                self.conn.sendall((json.dumps(msg) + "\n").encode())
                return True
            except OSError:
                self.conn = None
                import time
                time.sleep(0.05)
        return False

    def _get(self, prop):
        """Ask mpv for a property; skip the event lines it interleaves."""
        self.rid = getattr(self, "rid", 0) + 1
        if not self._send("get_property", prop, rid=self.rid):
            return None
        try:
            for _ in range(50):
                while b"\n" not in self.buf:
                    chunk = self.conn.recv(4096)
                    if not chunk:
                        raise OSError("closed")
                    self.buf += chunk
                line, self.buf = self.buf.split(b"\n", 1)
                d = json.loads(line.decode(errors="replace"))
                if d.get("request_id") == self.rid:
                    return d.get("data") if d.get("error") == "success" else None
        except (OSError, ValueError):
            self.conn = None
        return None

    # --- control
    @property
    def active(self):
        return self.proc is not None and self.proc.poll() is None and self.index >= 0

    @property
    def current(self):
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def play(self, tracks, start=0):
        """Replace the queue with tracks and start at tracks[start]."""
        src = tracks[0]["_src"]
        urls = []
        headers = None
        for t in tracks:
            url, headers = src.audio_stream(t)
            urls.append(url)
        if self.proc is None or self.proc.poll() is not None or headers != self.headers:
            self._start(headers)
        self.queue = list(tracks)
        self._send("playlist-clear")
        self._send("loadfile", urls[0], "replace")
        for u in urls[1:]:
            self._send("loadfile", u, "append")
        if start:
            self._send("set_property", "playlist-pos", start)
        self._send("set_property", "pause", False)
        self.index, self.paused = start, False

    def toggle_pause(self):
        if self.active:
            self.paused = not self.paused
            self._send("set_property", "pause", self.paused)

    def next(self):
        if self.active and self.index + 1 < len(self.queue):
            self._send("playlist-next", "force")

    def previous(self):
        if not self.active:
            return
        if self.position > 4 or self.index == 0:
            self._send("seek", 0, "absolute")
        else:
            self._send("playlist-prev", "force")

    def poll(self):
        """Refresh position/track; returns True if anything changed."""
        if not self.active:
            return False
        before = (self.index, int(self.position), self.paused)
        pos = self._get("playlist-pos")
        if isinstance(pos, int) and not isinstance(pos, bool):
            if pos < 0:                     # finished the queue
                self.index = -1
                return True
            self.index = pos
        t = self._get("time-pos")
        self.position = float(t) if isinstance(t, (int, float)) else 0.0
        d = self._get("duration")
        self.duration = float(d) if isinstance(d, (int, float)) else (self.current or {}).get("duration", 0.0)
        p = self._get("pause")
        if isinstance(p, bool):
            self.paused = p
        return before != (self.index, int(self.position), self.paused)

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.proc, self.conn, self.index = None, None, -1
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass
