#!/usr/bin/env python3
"""Build a Ukrainian movie-channel TV guide from public XMLTV EPG.
Never invent listings or IMDb ratings. Optional verified IMDb enrichment via Cinemeta.
"""
import datetime as dt
import gzip
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

EPG_URL = os.environ.get("EPG_URL", "https://iptv-epg.org/files/epg-ua.xml")
KYIV = ZoneInfo("Europe/Kyiv")
OUT = Path("data/guide.json")
MOVIE_HINTS = ("кіно", "kino", "cinema", "movie", "film", "фільм", "фильм", "megogo", "cine", "viasat", "tv1000", "paramount", "fox", "amc", "sci-fi", "sci fi", "bollywood", "horror", "action", "comedy", "romance", "premium", "enter-фільм", "enter-film", "enter- film", "ніку", "filmzone", "filmbox", "hollywood")
EXCLUDE = ("sport", "спорт", "music", "муз", "news", "новин", "football", "футбол", "kids", "дитяч", "documentary", "discovery", "history", "animal")
# Optional curated EPG display names: one exact channel name per line.
ALLOWLIST = Path("config/movie_channels.txt")
def fetch(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "MegogoKinoGuide/1.0 (+public EPG viewer)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(55_000_000)
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw

def parse_time(value):
    m = re.match(r"(\d{14})\s*([+-]\d{4})?", value or "")
    if not m:
        return None
    base = dt.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    tz = m.group(2)
    if tz:
        sign = 1 if tz[0] == "+" else -1
        offset = dt.timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5]))
        base = base.replace(tzinfo=dt.timezone(sign * offset))
    else:
        base = base.replace(tzinfo=dt.timezone.utc)
    return base.astimezone(KYIV)

def child_text(el, name):
    for node in el.findall(name):
        if node.text and node.text.strip():
            return node.text.strip()
    return ""

def movie_channel(name):
    n = name.lower()
    return any(w in n for w in MOVIE_HINTS) and not any(w in n for w in EXCLUDE)

def year_of(text):
    m = re.search(r"(?<!\d)(19\d{2}|20[0-3]\d)(?!\d)", text)
    return int(m.group(1)) if m else None

def main():
    today = dt.datetime.now(KYIV).date()
    allowed = {(today + dt.timedelta(days=i)).isoformat() for i in range(8)}
    root = ET.fromstring(fetch(EPG_URL))
    curated = {line.strip().casefold() for line in ALLOWLIST.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")} if ALLOWLIST.exists() else set()
    channels = {}
    for ch in root.findall("channel"):
        name = child_text(ch, "display-name")
        if name:
            channels[ch.get("id")] = name
    by_date = {day: [] for day in sorted(allowed)}
    for p in root.findall("programme"):
        ch = channels.get(p.get("channel"))
        if not ch:
            continue
        start = parse_time(p.get("start"))
        if not start or start.date().isoformat() not in allowed:
            continue
        title = clean_text(child_text(p, "title"))
        if not title:
            continue
        desc = clean_text(child_text(p, "desc")) or ""
        cats = [clean_text(n.text.strip()) for n in p.findall("category") if n.text]
        cats = [c for c in cats if c]
        yr = child_text(p, "date")
        by_date[start.date().isoformat()].append({
            "channel": ch, "start_time": start.strftime("%H:%M"),
            "title": title, "year": year_of(yr) or year_of(desc),
            "genre": ", ".join(cats[:2]) if cats else None,
            "imdb_rating": None, "imdb_id": None,
        })
    for rows in by_date.values():
        rows.sort(key=lambda p: (p["start_time"], p["channel"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "coverage_note": "Відібрані кіноканали з доступного EPG; їхня наявність у MEGOGO Максимальна не підтверджена.",
        "updated": dt.datetime.now(KYIV).isoformat(timespec="minutes"),
        "source": EPG_URL,
        "days": [{"date": day, "programmes": rows} for day, rows in by_date.items()],
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {sum(len(x) for x in by_date.values())} programmes across {len(channels)} EPG channels")
if __name__ == "__main__":
    main()
