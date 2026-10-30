"""Movies and TV from Jellyfin and Plex, in one shape.

Sign-in is shared with Halcyon Media Player (its Jellyfin/Plex classes and
their saved tokens). An item is a dict:
    id, source ("jellyfin"/"plex"), kind (movie/series/season/episode),
    title, year, overview, rating, minutes, poster, backdrop, resume (s),
    duration (s), played, number (episode/season number), series (title)
"""
import urllib.parse

from halcyon_player.jellyfin import Jellyfin, JellyfinError
from halcyon_player.plex import Plex, PlexError

TICKS = 10_000_000          # Jellyfin time unit per second


class SourceError(Exception):
    pass


# ---------------------------------------------------------------- Jellyfin ---
class JellyfinSource:
    name = "jellyfin"

    def __init__(self):
        self.c = Jellyfin()

    @property
    def ready(self):
        return self.c.connected

    def _req(self, path, params=None, body=None):
        try:
            return self.c.request(path, params, body)
        except JellyfinError as e:
            raise SourceError(str(e))

    def image_headers(self):
        return {"X-Emby-Token": self.c.token or ""}

    def _img(self, iid, kind="Primary", h=450, tag=None):
        q = {"maxHeight": h, "quality": 90}
        if tag:
            q["tag"] = tag
        return f"{self.c.server}/Items/{iid}/Images/{kind}?" + urllib.parse.urlencode(q)

    def _item(self, i):
        ud = i.get("UserData") or {}
        kind = {"Movie": "movie", "Series": "series", "Season": "season", "Episode": "episode"}.get(i.get("Type"), "")
        tags = i.get("ImageTags") or {}
        poster_id = i["Id"] if "Primary" in tags else (i.get("SeriesId") if kind in ("season", "episode") else None)
        backdrop_id = i["Id"] if i.get("BackdropImageTags") else (i.get("ParentBackdropItemId") or i.get("SeriesId"))
        return {
            "id": i["Id"], "source": self.name, "kind": kind, "title": i.get("Name", ""),
            "year": i.get("ProductionYear") or "", "overview": i.get("Overview", ""),
            "rating": i.get("OfficialRating") or "", "score": i.get("CommunityRating"),
            "minutes": round((i.get("RunTimeTicks") or 0) / TICKS / 60),
            "poster": self._img(poster_id) if poster_id else None,
            "thumb": self._img(i["Id"], "Primary", 300) if kind == "episode" and "Primary" in tags else None,
            "backdrop": self._img(backdrop_id, "Backdrop", 1080) if backdrop_id else None,
            "resume": (ud.get("PlaybackPositionTicks") or 0) / TICKS,
            "duration": (i.get("RunTimeTicks") or 0) / TICKS,
            "played": bool(ud.get("Played")), "number": i.get("IndexNumber"),
            "season_number": i.get("ParentIndexNumber"), "series": i.get("SeriesName", ""),
            "series_id": i.get("SeriesId"),
        }

    FIELDS = "Overview,ProductionYear,RunTimeTicks,CommunityRating,OfficialRating,UserData,ImageTags,BackdropImageTags"

    def _items(self, params):
        d = self._req(f"/Users/{self.c.user_id}/Items", dict(params, Fields=self.FIELDS))
        return [self._item(i) for i in d.get("Items", [])]

    def movies(self):
        return self._items({"IncludeItemTypes": "Movie", "Recursive": "true", "SortBy": "SortName"})

    def shows(self):
        return self._items({"IncludeItemTypes": "Series", "Recursive": "true", "SortBy": "SortName"})

    def recent(self, kind):
        t = "Movie" if kind == "movie" else "Episode"
        d = self._req(f"/Users/{self.c.user_id}/Items/Latest", {"IncludeItemTypes": t, "Limit": 16,
                                                                 "Fields": self.FIELDS})
        return [self._item(i) for i in d] if isinstance(d, list) else []

    def resume(self):
        d = self._req(f"/Users/{self.c.user_id}/Items/Resume", {"MediaTypes": "Video", "Limit": 16,
                                                                 "Fields": self.FIELDS})
        return [self._item(i) for i in d.get("Items", [])]

    def seasons(self, series):
        d = self._req(f"/Shows/{series['id']}/Seasons", {"UserId": self.c.user_id, "Fields": self.FIELDS})
        return [self._item(i) for i in d.get("Items", [])]

    def episodes(self, series, season):
        d = self._req(f"/Shows/{series['id']}/Episodes", {"UserId": self.c.user_id, "SeasonId": season["id"],
                                                         "Fields": self.FIELDS})
        return [self._item(i) for i in d.get("Items", [])]

    def stream(self, item):
        return f"{self.c.server}/Videos/{item['id']}/stream?static=true", self.c.mpv_headers()

    def report(self, item, state, seconds):
        """state: start / progress / stopped."""
        path = {"start": "/Sessions/Playing", "progress": "/Sessions/Playing/Progress",
                "stopped": "/Sessions/Playing/Stopped"}[state]
        try:
            self.c.request(path, body={"ItemId": item["id"], "PositionTicks": int(seconds * TICKS),
                                       "CanSeek": True, "PlayMethod": "DirectStream"})
        except JellyfinError:
            pass


# -------------------------------------------------------------------- Plex ---
class PlexSource:
    name = "plex"

    def __init__(self):
        self.c = Plex()

    @property
    def ready(self):
        return bool(self.c.signed_in and self.c.server)

    def _get(self, path, params=None):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        try:
            d = self.c.request(f"{self.c.server['uri']}{path}{q}", self.c.server["token"])
        except PlexError as e:
            raise SourceError(str(e))
        return (d.get("MediaContainer") or {})

    def image_headers(self):
        return {"X-Plex-Token": self.c.server["token"] if self.c.server else ""}

    def _img(self, thumb, w=300, h=450):
        if not thumb:
            return None
        q = urllib.parse.urlencode({"width": w, "height": h, "minSize": 1, "upscale": 1, "url": thumb})
        return f"{self.c.server['uri']}/photo/:/transcode?{q}"

    def _item(self, i):
        kind = {"movie": "movie", "show": "series", "season": "season", "episode": "episode"}.get(i.get("type"), "")
        poster = i.get("thumb") if kind in ("movie", "series", "season") else (
            i.get("grandparentThumb") or i.get("parentThumb"))
        return {
            "id": i["ratingKey"], "source": self.name, "kind": kind, "title": i.get("title", ""),
            "year": i.get("year") or "", "overview": i.get("summary", ""),
            "rating": i.get("contentRating") or "", "score": i.get("audienceRating") or i.get("rating"),
            "minutes": round((i.get("duration") or 0) / 60000),
            "poster": self._img(poster), "thumb": self._img(i.get("thumb"), 400, 225) if kind == "episode" else None,
            "backdrop": self._img(i.get("art") or i.get("grandparentArt"), 1920, 1080),
            "resume": (i.get("viewOffset") or 0) / 1000, "duration": (i.get("duration") or 0) / 1000,
            "played": bool(i.get("viewCount")), "number": i.get("index"),
            "season_number": i.get("parentIndex"), "series": i.get("grandparentTitle", ""),
            "series_id": i.get("grandparentRatingKey"),
            "_part": ((i.get("Media") or [{}])[0].get("Part") or [{}])[0].get("key"),
        }

    def _sections(self, kind):
        return [s["key"] for s in self._get("/library/sections").get("Directory", []) if s.get("type") == kind]

    def _all(self, kind):
        out = []
        for key in self._sections(kind):
            out += [self._item(i) for i in self._get(f"/library/sections/{key}/all").get("Metadata", [])]
        return sorted(out, key=lambda x: x["title"].lower())

    def movies(self):
        return self._all("movie")

    def shows(self):
        return self._all("show")

    def recent(self, kind):
        out = []
        for key in self._sections("movie" if kind == "movie" else "show"):
            path = f"/library/sections/{key}/recentlyAdded"
            out += [self._item(i) for i in self._get(path, {"X-Plex-Container-Size": 16,
                                                             "X-Plex-Container-Start": 0}).get("Metadata", [])]
        return out[:16]

    def resume(self):
        return [self._item(i) for i in self._get("/library/onDeck").get("Metadata", [])][:16]

    def seasons(self, series):
        return [self._item(i) for i in self._get(f"/library/metadata/{series['id']}/children").get("Metadata", [])]

    def episodes(self, series, season):
        return [self._item(i) for i in self._get(f"/library/metadata/{season['id']}/children").get("Metadata", [])]

    def stream(self, item):
        part = item.get("_part")
        if not part:
            meta = self._get(f"/library/metadata/{item['id']}").get("Metadata", [{}])[0]
            part = self._item(meta).get("_part")
        if not part:
            raise SourceError("Plex didn't say where this video's file is.")
        return f"{self.c.server['uri']}{part}", self.c.mpv_headers()

    def report(self, item, state, seconds):
        st = {"start": "playing", "progress": "playing", "stopped": "stopped"}[state]
        q = {"ratingKey": item["id"], "key": f"/library/metadata/{item['id']}", "state": st,
             "time": int(seconds * 1000), "duration": int(item.get("duration", 0) * 1000)}
        try:
            self._get("/:/timeline", q)
            if state == "stopped" and item.get("duration") and seconds > item["duration"] * 0.92:
                self._get("/:/scrobble", {"key": item["id"], "identifier": "com.plexapp.plugins.library"})
        except SourceError:
            pass


def sources():
    """Signed-in sources, Jellyfin first."""
    out = []
    for cls in (JellyfinSource, PlexSource):
        try:
            s = cls()
        except Exception:
            continue
        if s.ready:
            out.append(s)
    return out
