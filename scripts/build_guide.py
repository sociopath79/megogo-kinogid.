#!/usr/bin/env python3
"""Build a Ukrainian movie-channel TV guide from public XMLTV EPG.
Never invent listings or IMDb ratings. Optional verified IMDb enrichment via Cinemeta.
"""
import datetime as dt
import gzip
from html.parser import HTMLParser
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo
from imdb_enrich import enrich

EPG_URL = os.environ.get("EPG_URL", "https://iptv-epg.org/files/epg-ua.xml")
KYIV = ZoneInfo("Europe/Kyiv")
OUT = Path("data/guide.json")
CONFIG = Path("config/megogo_channels.json")
SECONDARY_EPG = os.environ.get("SECONDARY_EPG", "https://iptvx.one/EPG")
SECONDARY_IDS = {
    "Viasat Kino HD": "tv1000-cee",
    "Viasat Kino Action HD": "tv1000-action-cee",
    "Viasat Kino Comedy HD": "vip-comedy-cee",
    "Viasat Kino World": "tv1000-worldkino",
    "Viasat Serial HD": "viasat-serial",
}
def fetch(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "MegogoKinoGuide/1.0 (+public EPG viewer)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        # Read the complete response: a fixed byte limit truncated large gzip EPGs.
        chunks = []
        total = 0
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 250_000_000:
                raise ValueError("EPG download exceeds 250 MB safety limit")
            chunks.append(chunk)
        raw = b"".join(chunks)
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

def year_of(text):
    m = re.search(r"(?<!\d)(19\d{2}|20[0-3]\d)(?!\d)", text)
    return int(m.group(1)) if m else None

def is_placeholder(title):
    return (title or "").strip().casefold() in {"no data", "no information", "no programme information", "no program information", "немає даних", "нет данных", "без назви", "n/a", "tba", "-"}


# Optional third source: HTML schedule, never overwrite existing XMLTV slots.
TELE_URL = "https://teleprograma.com.ua/cat/movies/date/{day}/period/allday"
TELE_NAMES = {
    "FILMUADRAMA": "FILMUADRAMA HD",
    "Star Cinema": "Star Cinema HD",
    "Cine+": "Cine+ HD",
    "Cine+ Hit": "Cine+ Hit HD",
    "Cine+ Legend": "Cine+ Legend",
    "Enter-фільм": "Enter-фільм HD",
    "BOLT": "BOLT HD",
    "FilmBox": "FilmBox",
    "AMC": "AMC",
    "Viasat Kino": "Viasat Kino HD",
    "Viasat Kino Action": "Viasat Kino Action HD",
    "Viasat Kino Comedy": "Viasat Kino Comedy HD",
    "Viasat Kino World": "Viasat Kino World",
    "Viasat Serial": "Viasat Serial HD",
    "Твоє Кіно HIT": "Твоє кіно Хіт HD",
    "Твоє Кіно Action": "Твоє кіно Action HD",
    "Твоє Кіно Relax": "Твоє кіно Relax HD",
}
class ScheduleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("a", "div", "li", "p", "tr", "td", "h2", "h3", "br"):
            self.parts.append("\\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        if tag in ("a", "div", "li", "p", "tr", "td", "h2", "h3"):
            self.parts.append("\\n")
    def handle_data(self, value):
        if not self.hidden and value.strip():
            self.parts.append(value.strip())

def supplement_teleprograma(by_date):
    # Limit requests to the date range actually shown by the provider.
    count = 0
    for day in sorted(by_date):
        try:
            page = fetch(TELE_URL.format(day=day), timeout=20).decode("utf-8", errors="replace")
            parser = ScheduleText()
            parser.feed(page)
            tokens = [t.strip() for t in "".join(parser.parts).splitlines() if t.strip()]
            channel = None
            current_time = None
            occupied = {(p["channel"], p["start_time"]) for p in by_date[day]}
            for token in tokens:
                if token in TELE_NAMES:
                    channel = TELE_NAMES[token]
                    current_time = None
                    continue
                if re.fullmatch(r"[0-2]\\d:[0-5]\\d", token):
                    current_time = token if channel else None
                    continue
                if channel and current_time:
                    if channel in {n for g in json.loads(CONFIG.read_text(encoding="utf-8"))["groups"].values() for n in g} and (channel, current_time) not in occupied and not is_placeholder(token):
                        by_date[day].append({"channel": channel, "start_time": current_time,
                            "title": token, "year": None, "genre": None,
                            "imdb_rating": None, "imdb_id": None})
                        occupied.add((channel, current_time))
                        count += 1
                    current_time = None
        except Exception as exc:
            print("Teleprograma skipped", day, type(exc).__name__, str(exc)[:120])
    print("Teleprograma supplemental rows:", count)

def main():
    today = dt.datetime.now(KYIV).date()
    allowed = {(today + dt.timedelta(days=i)).isoformat() for i in range(-7, 8)}
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    groups = config["groups"]
    aliases = config["aliases"]
    names = [name for group in groups.values() for name in group]
    root = ET.fromstring(fetch(EPG_URL))
    epg_channels = {}
    for ch in root.findall("channel"):
        name = child_text(ch, "display-name")
        if name:
            epg_channels.setdefault(name.casefold(), []).append(ch.get("id"))
    channel_ids = {}
    available = []
    missing = []
    for name in names:
        candidates = aliases.get(name, []) + [name]
        ids = next((epg_channels[c.casefold()] for c in candidates if c.casefold() in epg_channels), [])
        if ids:
            available.append(name)
            for cid in ids:
                channel_ids[cid] = name
        else:
            missing.append(name)
    by_date = {day: [] for day in sorted(allowed)}
    # Preserve past listings when upstream sources stop serving older dates.
    if OUT.exists():
        try:
            previous = json.loads(OUT.read_text(encoding="utf-8"))
            for old_day in previous.get("days", []):
                date = old_day.get("date")
                if date in by_date and date < today.isoformat():
                    by_date[date].extend(old_day.get("programmes", []))
        except (OSError, ValueError, TypeError) as exc:
            print("Historical EPG cache unavailable:", exc)

    for p in root.findall("programme"):
        ch = channel_ids.get(p.get("channel"))
        if not ch:
            continue
        start = parse_time(p.get("start"))
        if not start or start.date().isoformat() not in allowed:
            continue
        title = child_text(p, "title")
        if not title or is_placeholder(title):
            continue
        desc = child_text(p, "desc")
        cats = [n.text.strip() for n in p.findall("category") if n.text]
        yr = child_text(p, "date")
        by_date[start.date().isoformat()].append({
            "channel": ch, "start_time": start.strftime("%H:%M"),
            "title": title, "year": year_of(yr) or year_of(desc),
            "genre": ", ".join(cats[:2]) if cats else None,
            "imdb_rating": None, "imdb_id": None,
        })
    # Supplement only channels with explicitly verified regional XMLTV IDs.
    try:
        secondary = ET.fromstring(fetch(SECONDARY_EPG, timeout=45))
        id_to_name = {v: k for k, v in SECONDARY_IDS.items()}
        # Add only programmes not already provided by the primary source.
        primary_slots = {(p["channel"], day, p["start_time"]) for day, rows in by_date.items() for p in rows}
        for p in secondary.findall("programme"):
            ch = id_to_name.get(p.get("channel"))
            if not ch:
                continue
            start = parse_time(p.get("start"))
            if not start or start.date().isoformat() not in allowed:
                continue
            title = child_text(p, "title")
            if not title or is_placeholder(title):
                continue
            desc = child_text(p, "desc")
            cats = [n.text.strip() for n in p.findall("category") if n.text]
            yr = child_text(p, "date")
            by_date[start.date().isoformat()].append({
                "channel": ch, "start_time": start.strftime("%H:%M"),
                "title": title, "year": year_of(yr) or year_of(desc),
                "genre": ", ".join(cats[:2]) if cats else None,
                "imdb_rating": None, "imdb_id": None,
            })
            if ch not in available:
                available.append(ch)
                if ch in missing:
                    missing.remove(ch)
        print("Secondary EPG checked:", SECONDARY_EPG)
    except Exception as exc:
        print("Secondary EPG unavailable:", type(exc).__name__, str(exc)[:180])
    if os.environ.get('ENABLE_TELEPROGRAMA', '0') == '1':
        supplement_teleprograma(by_date)
    # Availability means actual programme rows, not merely a matching channel name.
    covered = {p["channel"] for rows in by_date.values() for p in rows}
    available = [name for name in names if name in covered]
    missing = [name for name in names if name not in covered]
    for rows in by_date.values():
        unique = {(p["channel"], p["start_time"]): p for p in rows[::-1]}
        rows[:] = sorted(unique.values(), key=lambda p: (p["start_time"], p["channel"]))
    enrich(by_date)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "coverage_note": "IMDb додається лише за однозначним збігом назви та року; непідтверджені рейтинги не показуються.",
        "updated": dt.datetime.now(KYIV).isoformat(timespec="minutes"),
        "source": EPG_URL,
        "additional_source": SECONDARY_EPG,
        "channel_groups": groups,
        "epg_available": available,
        "epg_missing": missing,
        "days": [{"date": day, "programmes": rows} for day, rows in by_date.items()],
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Channels requested: {len(names)}; matched: {len(available)}; without EPG: {len(missing)}")
    print(f"Saved {sum(len(x) for x in by_date.values())} programmes")

if __name__ == "__main__":
    main()
