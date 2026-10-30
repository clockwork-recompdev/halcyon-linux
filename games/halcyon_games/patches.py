"""Find the best patch for an installed Windows game.

PCGamingWiki keeps, for most PC games, a "Patches" / "Essential improvements"
section saying which official patch to use, with download links. We look the
game up there, collect the downloadable patches, and rank them for this
computer: Windows over Mac, the user's language, the newest version, and the
expansion variant that matches what's installed. Only links the wiki lists
are offered; nothing is fetched from anywhere else."""
import json
import locale
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://www.pcgamingwiki.com/w/api.php"
WIKI = "https://www.pcgamingwiki.com/wiki/"
UA = {"User-Agent": "HalcyonGames/1.0 (Halcyon Linux; patch finder)"}
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "halcyon-games" / "patches"
SECTION = re.compile(r"patch|essential improvements|unofficial|fixes?\b|updates?\b", re.I)
FILE_EXT = re.compile(r"\.(exe|zip|7z|rar|msi)(\?|$)", re.I)
FILE_HOSTS = ("community.pcgamingwiki.com/files/", "moddb.com/", "github.com/", "archive.org/")
LANGS = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pl": "Polish",
         "ru": "Russian", "pt": "Portuguese", "cs": "Czech", "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
         "nl": "Dutch", "sv": "Swedish"}
VENDORS = ("blizzard.com", "ea.com", "ubi.com", "ubisoft.com", "microsoft.com", "valvesoftware.com",
           "bethsoft.com", "activision.com", "sega.com", "thq.com", "cdprojekt", "bioware.com", "eidos")


def _get(params):
    params = dict(params, format="json")
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("™", "").replace("®", "")).strip()


def find_page(name):
    """The PCGamingWiki page title for a game name, or None."""
    q = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", name).strip()
    titles = _get({"action": "opensearch", "search": q, "limit": 8})[1]
    if not titles:
        # "Warcraft III Frozen Throne" finds nothing; try fewer words.
        words = q.split()
        while len(words) > 1 and not titles:
            words.pop()
            titles = _get({"action": "opensearch", "search": " ".join(words), "limit": 8})[1]
    if not titles:
        return None
    want = _norm(q)
    best = max(titles, key=lambda t: (_norm(t) == want, len(set(_norm(t).split()) & set(want.split())),
                                      -abs(len(_norm(t)) - len(want))))
    return best


def page_patch_text(title):
    """(final page title, wikitext of its patch-related sections)."""
    d = _get({"action": "parse", "page": title, "prop": "sections", "redirects": 1})
    if "parse" not in d:
        return title, ""
    final = d["parse"]["title"]
    texts = []
    for s in d["parse"]["sections"]:
        if SECTION.search(s["line"]) and s.get("index", "").isdigit():
            w = _get({"action": "parse", "page": final, "prop": "wikitext", "section": s["index"]})
            texts.append(w.get("parse", {}).get("wikitext", {}).get("*", ""))
    return final, "\n".join(texts)


def _clean(s):
    s = re.sub(r"\{\{(?:ii|\+\+|--|mm|--)\}\}", "", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"'''?|<br\s*/?>|<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip(" :*|")


def parse_links(wikitext):
    """Links in the patch sections, with the context needed to rank them."""
    out = []
    headers, row, col, heading, para = [], [], 0, "", ""
    for raw in wikitext.splitlines():
        line = raw.strip()
        if line.startswith("="):
            heading = _clean(line.strip("="))
            continue
        if line.startswith("{|"):
            headers, row = [], []
            continue
        if line.startswith("|-") or line.startswith("|}"):
            row, col = [], 0
            continue
        if line.startswith("!"):
            for h in re.split(r"!!", line[1:]):
                headers.append(_clean(h.split("|")[-1]))
            continue
        cells = None
        if line.startswith("|"):
            cells = [c.split("|", 1)[-1] if "style=" in c.split("|", 1)[0] else c for c in line[1:].split("||")]
        else:
            para = _clean(line)
        for cell in cells or [line]:
            if cells is not None:
                row.append(_clean(cell))
                col = len(row) - 1
            for m in re.finditer(r"\[(https?://[^\s\]]+)\s+([^\]]+)\]", cell):
                out.append({
                    "url": m.group(1), "text": _clean(m.group(2)), "heading": heading,
                    "row": row[0] if cells is not None and row else "",
                    "column": headers[col] if cells is not None and col < len(headers) else "",
                    "context": para if cells is None else "",
                })
    return out


def _version(url):
    """Sortable version from a patch file name: War3TFT_127b_English.exe -> (127, 'b')."""
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    m = re.findall(r"(\d+(?:[._]\d+)*)([a-z]?)(?=[_.\-\s]|$)", name, re.I)
    if not m:
        return (0,)
    nums, letter = m[-1]
    return tuple(int(x) for x in re.split(r"[._]", nums)) + (ord(letter.lower()) if letter else 0,)


def _installed_words(folder, other_names):
    words = set()
    if folder and os.path.isdir(folder):
        for f in os.listdir(folder):
            words |= set(_norm(os.path.splitext(f)[0]).split())
    for n in other_names:
        words |= set(_norm(n).split())
    return words


def rank(links, folder=None, other_names=(), lang=None, title=""):
    """Score downloadable patches for this system; returns them best first (one per URL).
    Direct files can be installed; other links (download pages) are opened in a browser."""
    lang = lang or LANGS.get((locale.getlocale()[0] or "en")[:2], "English")
    have = _installed_words(folder, other_names)
    title_words = set(_norm(title).split()) - {"the", "of", "and"}
    langs = set(LANGS.values())
    scored = []
    for l in links:
        url, blob = l["url"], " ".join((l["text"], l["row"], l["column"], l["heading"], l["context"]))
        downloadable = bool(FILE_EXT.search(url)) or any(h in url for h in FILE_HOSTS)
        if not downloadable:
            continue
        low = blob.lower()
        s = 0
        if re.search(r"\bmac\b|os x|macos|linux", l["text"].lower()) or url.lower().endswith(".dmg"):
            s -= 100
        if "windows" in l["text"].lower() or url.lower().endswith((".exe", ".msi")):
            s += 10
        named = [x for x in langs if x.lower() in low]
        if named:
            s += 20 if lang in named else -40
        if any(v in url.lower() for v in VENDORS) or "official" in low:
            s += 8
        if "unofficial" in low or "community" in low:
            s += 2
        # Expansion variants: the column matching what's installed wins; otherwise
        # the base game (the column named like the page itself).
        col = set(_norm(l["column"]).split()) - {"the", "of", "and"}
        if col:
            extra = col - title_words          # what makes this variant different (e.g. the expansion)
            s += 15 if extra and extra & have else 5 if not extra or col <= title_words else 0
        direct = bool(FILE_EXT.search(url))
        scored.append(dict(l, score=s + (3 if direct else 0), version=_version(url), direct=direct,
                           official=any(v in url.lower() for v in VENDORS)))
    scored.sort(key=lambda x: (x["direct"], x["score"], x["version"]), reverse=True)
    seen, out = set(), []
    for x in scored:
        if x["url"] not in seen:
            seen.add(x["url"])
            out.append(x)
    return out


def lookup(name, folder=None, other_names=()):
    """Everything the UI needs: page, recommended patch, other links, notes."""
    page = find_page(name)
    if not page:
        return {"page": None}
    title, text = page_patch_text(page)
    links = parse_links(text)
    ranked = rank(links, folder, other_names, title=title)
    notes = [_clean(l) for l in text.splitlines() if l.strip().startswith("{{ii}}")][:6]
    others = [l for l in links if l not in ranked and not FILE_EXT.search(l["url"])]
    return {"page": title, "url": WIKI + urllib.parse.quote(title.replace(" ", "_")), "best": ranked[:1],
            "alternatives": ranked[1:6], "links": others[:8], "notes": notes}


def download(url, progress=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    name = urllib.parse.unquote(url.split("?")[0].rsplit("/", 1)[-1]) or "patch.exe"
    dest = CACHE / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    req = urllib.request.Request(url, headers=UA)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(int(done * 100 / total))
    tmp.replace(dest)
    return dest


def wine_env(game_env):
    env = dict(os.environ)
    env.update(game_env or {})
    env.setdefault("WINEPREFIX", str(Path.home() / ".local/share/halcyon/windows"))
    return env


def apply(path, folder, game_env=None):
    """Run a downloaded patch for the game in folder. Returns a Popen."""
    env = wine_env(game_env)
    cwd = folder if folder and os.path.isdir(folder) else str(Path.home())
    if path.suffix.lower() == ".msi":
        return subprocess.Popen(["wine", "msiexec", "/i", str(path)], cwd=cwd, env=env)
    if path.suffix.lower() in (".zip", ".7z", ".rar"):
        # Archives of files that go into the game folder (that's how fixes ship).
        return subprocess.Popen(["bsdtar", "-xf", str(path), "-C", cwd], cwd=cwd)
    return subprocess.Popen(["wine", str(path)], cwd=cwd, env=env)
