"""Jellyfin: sign in once, list playlists, queue one into the player.

Only the access token is saved (~/.config/halcyon-player/jellyfin.json), never
the password. Streams are fetched by mpv with the token in an HTTP header, so
it never appears in URLs or logs.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONFIG = os.path.expanduser("~/.config/halcyon-player/jellyfin.json")
CLIENT, VERSION = "Halcyon Media Player", "1.0"


class JellyfinError(Exception):
    pass


class Jellyfin:
    def __init__(self):
        self.server = self.token = self.user_id = self.user_name = None
        self.device_id = str(uuid.uuid4())
        self.load()

    # ---- persistence
    def load(self):
        try:
            with open(CONFIG) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        self.server, self.token = d.get("server"), d.get("token")
        self.user_id, self.user_name = d.get("user_id"), d.get("user_name")
        self.device_id = d.get("device_id", self.device_id)

    def save(self):
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        data = {"server": self.server, "token": self.token, "user_id": self.user_id,
                "user_name": self.user_name, "device_id": self.device_id}
        fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # token: owner-only
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)

    @property
    def connected(self):
        return bool(self.server and self.token and self.user_id)

    def sign_out(self):
        self.token = self.user_id = None
        try:
            os.remove(CONFIG)
        except OSError:
            pass

    # ---- HTTP
    def auth_header(self):
        h = f'MediaBrowser Client="{CLIENT}", Device="Halcyon", DeviceId="{self.device_id}", Version="{VERSION}"'
        if self.token:
            h += f', Token="{self.token}"'
        return h

    def request(self, path, params=None, body=None):
        url = self.server.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Authorization", self.auth_header())
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise JellyfinError("The server didn't accept the sign-in. Check your username and password.")
            raise JellyfinError(f"The server answered with an error ({e.code}).")
        except (urllib.error.URLError, OSError) as e:
            raise JellyfinError(f"Couldn't reach the server: {getattr(e, 'reason', e)}")
        return json.loads(raw) if raw else {}

    # ---- API
    def sign_in(self, server, username, password):
        server = server.strip()
        if not server.startswith(("http://", "https://")):
            server = "http://" + server
        self.server, self.token = server.rstrip("/"), None
        d = self.request("/Users/AuthenticateByName", body={"Username": username, "Pw": password})
        self.token = d.get("AccessToken")
        self.user_id = (d.get("User") or {}).get("Id")
        self.user_name = (d.get("User") or {}).get("Name") or username
        if not self.connected:
            raise JellyfinError("Signed in, but the server didn't return a session.")
        self.save()

    def playlists(self):
        q = {"IncludeItemTypes": "Playlist", "Recursive": "true", "SortBy": "SortName",
             "Fields": "ChildCount"}
        d = self.request(f"/Users/{self.user_id}/Items", q)
        return [{"id": i["Id"], "name": i.get("Name", "Playlist"), "count": i.get("ChildCount")}
                for i in d.get("Items", [])]

    def playlist_items(self, playlist_id):
        d = self.request(f"/Playlists/{playlist_id}/Items", {"UserId": self.user_id})
        out = []
        for i in d.get("Items", []):
            kind = "Audio" if i.get("MediaType") == "Audio" or i.get("Type") == "Audio" else "Videos"
            artist = i.get("AlbumArtist") or ", ".join(i.get("Artists") or [])
            title = f"{artist} – {i.get('Name')}" if artist else i.get("Name", "")
            out.append({"url": f"{self.server}/{kind}/{i['Id']}/stream?static=true", "title": title})
        return out

    def mpv_headers(self):
        """Headers mpv sends when streaming (token stays out of URLs)."""
        return [f"Authorization: {self.auth_header()}", f"X-Emby-Token: {self.token}"]
