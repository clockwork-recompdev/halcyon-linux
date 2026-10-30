"""Artwork from SteamGridDB for games without their own: the wide banner
(header), the big background (hero) and the transparent title logo.

Needs the user's free SteamGridDB API key (steamgriddb.com → Preferences →
API), saved in Halcyon Games' config. Downloads are cached per game in
~/.cache/halcyon-games/sgdb, so each game is looked up once."""
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://www.steamgriddb.com/api/v2"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "halcyon-games" / "sgdb"
KINDS = {
    # field: (endpoint, extra query)
    "header": ("grids", {"dimensions": "460x215,920x430", "types": "static", "nsfw": "false"}),
    "hero": ("heroes", {"types": "static", "nsfw": "false"}),
    "logo": ("logos", {"types": "static", "nsfw": "false"}),
}


class SGDBError(Exception):
    pass


def _key_dir(game):
    return CACHE / re.sub(r"[^A-Za-z0-9._-]+", "_", game.id)[:120]


def apply_cached(games):
    """Fill missing art from earlier downloads. Returns games still worth a lookup."""
    todo = []
    for g in games:
        d = _key_dir(g)
        missing = False
        for field in KINDS:
            if getattr(g, field):
                continue
            f = next(iter(sorted(d.glob(f"{field}.*"))), None) if d.is_dir() else None
            if f and f.stat().st_size > 0:
                setattr(g, field, str(f))
            else:
                missing = True
        # A "none" marker means SteamGridDB had nothing; don't ask again.
        if missing and not (d / "none").exists() and not (d / "done").exists():
            todo.append(g)
    return todo


def _get(key, path, params=None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}",
                                               "User-Agent": "HalcyonGames/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise SGDBError("SteamGridDB didn't accept the API key.")
        if e.code == 404:
            return []
        raise SGDBError(f"SteamGridDB answered with an error ({e.code}).")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SGDBError(f"Couldn't reach SteamGridDB: {getattr(e, 'reason', e)}")
    return d.get("data") or []


def _game_ref(key, game):
    """('steam', appid) for Steam games, else ('game', sgdb id) from a name search."""
    if game.id.startswith("steam:"):
        return "steam", game.id.split(":", 1)[1]
    name = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", game.name).strip()
    hits = _get(key, "/search/autocomplete/" + urllib.parse.quote(name))
    if not hits:
        return None
    want = re.sub(r"[^a-z0-9]+", "", name.lower())
    exact = [h for h in hits if re.sub(r"[^a-z0-9]+", "", h.get("name", "").lower()) == want]
    return "game", (exact or hits)[0]["id"]


def fetch(key, game):
    """Download missing art for one game; returns {field: path}."""
    d = _key_dir(game)
    d.mkdir(parents=True, exist_ok=True)
    ref = _game_ref(key, game)
    if ref is None:
        (d / "none").touch()
        return {}
    kind, ident = ref
    got = {}
    for field, (endpoint, q) in KINDS.items():
        if getattr(game, field):
            continue
        items = _get(key, f"/{endpoint}/{kind}/{ident}", q)
        if not items:
            continue
        # Highest-voted first (the API's default order is by score).
        url = items[0].get("url")
        if not url:
            continue
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".png"
        dest = d / f"{field}{ext}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "HalcyonGames/1.0"}),
                                        timeout=30) as r:
                dest.write_bytes(r.read())
            got[field] = str(dest)
        except (urllib.error.URLError, OSError):
            continue
    (d / "done").touch()
    return got
