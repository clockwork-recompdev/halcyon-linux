"""Plex: sign in through plex.tv in the browser (PIN flow), pick a server,
list playlists, queue one into the player.

Only tokens are saved (~/.config/halcyon-player/plex.json, owner-only); the
password never touches the player. Streams carry the token in a header.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONFIG = os.path.expanduser("~/.config/halcyon-player/plex.json")
PLEX_TV = "https://plex.tv"
PRODUCT = "Halcyon Media Player"


class PlexError(Exception):
    pass


class Plex:
    def __init__(self):
        self.client_id = str(uuid.uuid4())
        self.token = None           # account token
        self.server = None          # {"name", "uri", "token"}
        self.pin = None
        self.load()

    # ---- persistence
    def load(self):
        try:
            with open(CONFIG) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        self.client_id = d.get("client_id", self.client_id)
        self.token, self.server = d.get("token"), d.get("server")

    def save(self):
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"client_id": self.client_id, "token": self.token, "server": self.server}, f)

    @property
    def signed_in(self):
        return bool(self.token)

    def sign_out(self):
        self.token = self.server = None
        try:
            os.remove(CONFIG)
        except OSError:
            pass

    # ---- HTTP
    def headers(self, token=None):
        h = {"Accept": "application/json", "X-Plex-Product": PRODUCT, "X-Plex-Version": "1.0",
             "X-Plex-Client-Identifier": self.client_id, "X-Plex-Platform": "Linux",
             "X-Plex-Device-Name": "Halcyon"}
        if token:
            h["X-Plex-Token"] = token
        return h

    def request(self, url, token=None, method="GET", timeout=15):
        req = urllib.request.Request(url, method=method, headers=self.headers(token))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise PlexError("Plex didn't accept the sign-in. Sign in again.")
            raise PlexError(f"Plex answered with an error ({e.code}).")
        except (urllib.error.URLError, OSError) as e:
            raise PlexError(f"Couldn't reach Plex: {getattr(e, 'reason', e)}")
        return json.loads(raw) if raw else {}

    # ---- sign in (PIN flow)
    def start_sign_in(self):
        """Returns the URL to open in the browser."""
        d = self.request(f"{PLEX_TV}/api/v2/pins?strong=true", method="POST")
        self.pin = (d["id"], d["code"])
        q = urllib.parse.urlencode({"clientID": self.client_id, "code": d["code"],
                                    "context[device][product]": PRODUCT})
        return f"https://app.plex.tv/auth#?{q}"

    def poll_sign_in(self):
        """True once the user approved in the browser."""
        if not self.pin:
            return False
        d = self.request(f"{PLEX_TV}/api/v2/pins/{self.pin[0]}")
        if d.get("authToken"):
            self.token, self.pin = d["authToken"], None
            self.save()
            return True
        return False

    # ---- servers
    def servers(self):
        d = self.request(f"{PLEX_TV}/api/v2/resources?includeHttps=1&includeRelay=1", self.token)
        out = []
        for r in d if isinstance(d, list) else []:
            if "server" not in (r.get("provides") or ""):
                continue
            conns = sorted(r.get("connections") or [],
                           key=lambda c: (c.get("relay", False), not c.get("local", False)))
            out.append({"name": r.get("name", "Plex"), "token": r.get("accessToken") or self.token,
                        "uris": [c["uri"] for c in conns if c.get("uri")]})
        return out

    def choose_server(self, srv):
        """Use the first connection that answers (local, then remote, then relay)."""
        for uri in srv["uris"]:
            try:
                self.request(f"{uri}/identity", srv["token"], timeout=4)
            except PlexError:
                continue
            self.server = {"name": srv["name"], "uri": uri.rstrip("/"), "token": srv["token"]}
            self.save()
            return True
        raise PlexError(f"Couldn't connect to {srv['name']} on any of its addresses.")

    # ---- playlists
    def playlists(self):
        if not self.server:
            raise PlexError("Pick a Plex server first.")
        d = self.request(f"{self.server['uri']}/playlists", self.server["token"])
        items = (d.get("MediaContainer") or {}).get("Metadata") or []
        return [{"id": i["ratingKey"], "name": i.get("title", "Playlist"), "count": i.get("leafCount"),
                 "kind": i.get("playlistType", "")} for i in items if i.get("playlistType") in ("audio", "video")]

    def playlist_items(self, pid):
        d = self.request(f"{self.server['uri']}/playlists/{pid}/items", self.server["token"])
        out = []
        for i in (d.get("MediaContainer") or {}).get("Metadata") or []:
            try:
                key = i["Media"][0]["Part"][0]["key"]
            except (KeyError, IndexError, TypeError):
                continue
            artist = i.get("grandparentTitle") or i.get("originalTitle") or ""
            title = f"{artist} – {i.get('title')}" if artist and i.get("type") == "track" else i.get("title", "")
            out.append({"url": f"{self.server['uri']}{key}", "title": title})
        return out

    def mpv_headers(self):
        return [f"X-Plex-Token: {self.server['token']}", f"X-Plex-Client-Identifier: {self.client_id}"]
