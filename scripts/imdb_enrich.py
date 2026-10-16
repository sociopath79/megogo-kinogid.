"""Conservative IMDb enrichment. Only exact title + year matches; no guessed ratings."""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

CACHE = Path("data/imdb_cache.json")
LIMIT = 90

def norm(s):
    s = re.sub(r"^(?:х/ф|т/с|м/ф|фільм|серіал)\\s*[:. -]*", "", s or "", flags=re.I)
    s = re.sub(r"\\s*(?:\\||[-–])\\s*(?:серія|серия|епізод|эпизод)\\s*\\d+.*$", "", s, flags=re.I)
    return re.sub(r"[^\\w]+", " ", s.casefold()).strip()

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MovieGuide/1.0)"})
    with urllib.request.urlopen(req, timeout=9) as response:
        return json.load(response)

def lookup(title, year):
    if not year or len(norm(title)) < 4:
        return None
    q = norm(title)
    url = "https://v3.sg.media-imdb.com/suggestion/" + urllib.parse.quote(q[0]) + "/" + urllib.parse.quote(q) + ".json"
    candidates = get_json(url).get("d", [])
    exact = [x for x in candidates if x.get("id", "").startswith("tt") and norm(x.get("l")) == q and str(x.get("y")) == str(year)]
    if len(exact) != 1:
        return None
    imdb_id = exact[0]["id"]
    meta = {}
    for kind in ("movie", "series"):
        try:
            meta = get_json("https://v3-cinemeta.strem.io/meta/" + kind + "/" + imdb_id + ".json").get("meta") or {}
        except Exception:
            continue
        if meta:
            break
    rating = meta.get("imdbRating")
    try:
        rating = float(rating)
    except (TypeError, ValueError):
        return None
    if not 1 <= rating <= 10 or meta.get("imdb_id", imdb_id) != imdb_id:
        return None
    return {"imdb_id": imdb_id, "imdb_rating": rating}

def enrich(by_date):
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    except (ValueError, OSError):
        cache = {}
    tried = 0
    matched = 0
    for rows in by_date.values():
        for row in rows:
            year = row.get("year")
            if not year:
                continue
            key = json.dumps([norm(row.get("title")), year], ensure_ascii=False)
            if key not in cache and tried < LIMIT:
                tried += 1
                try:
                    cache[key] = lookup(row["title"], year)
                except Exception:
                    cache[key] = None
                time.sleep(0.08)
            value = cache.get(key)
            if value:
                row.update(value)
                matched += 1
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"IMDb: {tried} new lookups; {matched} programme rows enriched")
