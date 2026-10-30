"""Whether a game needs the controller mapper, and with which layout.

Asks PCGamingWiki (the game's page: "controller support" and its genres),
caches the answer in the launcher's config, and honours the user's choice
from the Controller… dialog: auto / action / mouse / off."""
import os
import re
import shlex
import subprocess
import sys
import threading
import time

from . import patches

MOUSE_GENRES = re.compile(r"rts|strategy|tbs|4x|tactic|point.and.click|adventure|arpg|mmo|simulation|"
                          r"city.build|management|puzzle|card|visual novel|rpg", re.I)
CHOICES = ("auto", "action", "mouse", "off")


def lookup(name):
    """{'support': 'true'|'false'|'limited'|None, 'genres': [...], 'page': title}."""
    page = patches.find_page(name)
    if not page:
        return {"support": None, "genres": [], "page": None}
    d = patches._get({"action": "parse", "page": page, "prop": "wikitext", "redirects": 1})
    text = d.get("parse", {}).get("wikitext", {}).get("*", "")
    m = re.search(r"\|\s*controller support\s*=\s*([^\n|]*)", text)
    support = (m.group(1).strip().lower() or None) if m else None
    g = re.search(r"\{\{Infobox game/row/taxonomy/genres\s*\|\s*([^}]*)\}\}", text)
    genres = [x.strip() for x in g.group(1).split(",") if x.strip()] if g else []
    return {"support": support, "genres": genres, "page": d.get("parse", {}).get("title", page)}


def auto_layout(info):
    """None when the game handles controllers itself (or we don't know)."""
    if not info or info.get("support") in (None, "true", "unknown", "hackable"):
        return None
    return "mouse" if any(MOUSE_GENRES.search(g) for g in info.get("genres", [])) else "action"


def effective(game, cfg):
    """The layout to run for this game now, or None."""
    choice = cfg.get("controller", {}).get(game.id, "auto")
    if choice == "off":
        return None
    if choice in ("action", "mouse"):
        return choice
    if game.source == "Steam":          # Steam Input already does this for Steam games
        return None
    return auto_layout(cfg.get("controller_info", {}).get(game.id))


def wine_prefix(game):
    if game.env.get("WINEPREFIX"):
        return game.env["WINEPREFIX"]
    for i, a in enumerate(game.cmd):
        if a.startswith("WINEPREFIX="):
            return a.split("=", 1)[1]
    if game.source == "Windows":
        return os.path.expanduser("~/.local/share/halcyon/windows")
    return None


def start_for(game, cfg, pid, save):
    """After launching a game: look it up if needed (in the background) and run
    the mapper when it needs one. `save` persists cfg."""
    def work():
        info = cfg.setdefault("controller_info", {})
        choice = cfg.get("controller", {}).get(game.id, "auto")
        if choice == "auto" and game.source != "Steam" and game.id not in info:
            try:
                info[game.id] = lookup(game.name)
            except Exception:
                info[game.id] = {"support": None, "genres": [], "page": None, "failed": time.time()}
            save()
        layout = effective(game, cfg)
        if not layout:
            return
        cmd = [sys.executable, "-m", "halcyon_games.padmap", "--layout", layout]
        prefix = wine_prefix(game) if game.source == "Windows" else None
        if prefix:
            cmd += ["--prefix", prefix]
        elif pid:
            cmd += ["--pid", str(pid)]
        env = dict(os.environ)
        subprocess.Popen(cmd, env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=work, daemon=True).start()
