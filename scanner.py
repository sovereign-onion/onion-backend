from __future__ import annotations

import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
import feedparser
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from config import (
    AU_NZ_LOCATION_TERMS,
    CLASSICAL_TERMS,
    CONFIRMED_TERMS,
    MOVEMENT_TERMS,
    REQUEST_TIMEOUT_SECONDS,
    RSS_SOURCES,
    UNCONFIRMED_TERMS,
    USER_AGENT,
)
from db import list_watchlist, upsert_feed_item

def fetch_bachtrack():
    url = "https://bachtrack.com/rss/events"
    feed = feedparser.parse(url)

    results = []

    for entry in feed.entries:
        results.append({
            "name": entry.get("title", ""),
            "location": "",
            "event_date": "",
            "status": "live",
            "link": entry.get("link", ""),
            "fingerprint": entry.get("id", entry.get("link", ""))
        })

    return results

def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        return resp.read()


def clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalise_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def extract_location(text: str) -> str | None:
    lowered = text.lower()
    for term in AU_NZ_LOCATION_TERMS:
        if term in lowered:
            return term.title()
    return None


def is_classical(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in CLASSICAL_TERMS)


def has_movement_signal(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in MOVEMENT_TERMS)


def matches_watchlist(text: str, watchlist: list[dict[str, Any]]) -> bool:
    lowered = text.lower()

    for item in watchlist:
        name = str(item.get("name") or "").strip().lower()
        watch_type = str(item.get("watch_type") or "").strip().lower()
        alias = str(item.get("alias") or "").strip().lower()

        name_match = bool(name and name in lowered)
        alias_match = bool(alias and alias in lowered)

        if not (name_match or alias_match):
            continue

        if watch_type and watch_type not in lowered:
            continue

        return True

    return False


def derive_status(text: str, official: bool) -> str:
    lowered = text.lower()

    if official:
        return "Confirmed"

    if any(term in lowered for term in UNCONFIRMED_TERMS):
        return "Unconfirmed"

    if any(term in lowered for term in CONFIRMED_TERMS):
        return "Confirmed"

    return "Unconfirmed"


def derive_name(title: str) -> str:
    parts = re.split(r"\s[—\-:|]\s|[—:|]", title, maxsplit=1)
    candidate = normalise_spaces(parts[0]) if parts else normalise_spaces(title)
    if not candidate:
        return normalise_spaces(title)
    return candidate[:160]


def parse_date(raw_value: str) -> str:
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return "Unknown"

    try:
        dt = parsedate_to_datetime(raw_value)
        return dt.strftime("%d %b %Y")
    except Exception:
        pass

    known_formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    ]

    for fmt in known_formats:
        try:
            dt = datetime.strptime(raw_value, fmt)
            return dt.strftime("%d %b %Y")
        except Exception:
            continue

    return raw_value[:40]


def fingerprint_for(name: str, location: str, event_date: str, link: str) -> str:
    raw = f"{name}|{location}|{event_date}|{link}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()


def text_of(element: ET.Element | None, tag_names: list[str]) -> str:
    if element is None:
        return ""

    for tag_name in tag_names:
        child = element.find(tag_name)
        if child is not None and child.text:
            return clean_text(child.text)

    return ""


def parse_rss_items(root: ET.Element) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    for node in root.findall(".//channel/item"):
        title = text_of(node, ["title"])
        summary = text_of(node, ["description"])
        link = text_of(node, ["link"])
        pub_date = text_of(node, ["pubDate"])
        items.append(
            {
                "title": title,
                "summary": summary,
                "link": link,
                "pub_date": pub_date,
            }
        )

    if items:
        return items

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
    }

    for node in root.findall(".//atom:entry", ns):
        title = text_of(node, ["{http://www.w3.org/2005/Atom}title"])
        summary = text_of(
            node,
            [
                "{http://www.w3.org/2005/Atom}summary",
                "{http://www.w3.org/2005/Atom}content",
            ],
        )
        pub_date = text_of(
            node,
            [
                "{http://www.w3.org/2005/Atom}updated",
                "{http://www.w3.org/2005/Atom}published",
            ],
        )

        link = ""
        for link_node in node.findall("{http://www.w3.org/2005/Atom}link"):
            href = link_node.attrib.get("href", "").strip()
            rel = link_node.attrib.get("rel", "alternate").strip()
            if href and rel in {"alternate", ""}:
                link = href
                break

        items.append(
            {
                "title": title,
                "summary": summary,
                "link": link,
                "pub_date": pub_date,
            }
        )

    return items


def classify_item(
    source: dict[str, Any],
    raw_item: dict[str, str],
    watchlist: list[dict[str, Any]],
) -> dict[str, str] | None:
    title = normalise_spaces(raw_item.get("title", ""))
    summary = normalise_spaces(raw_item.get("summary", ""))
    link = raw_item.get("link", "").strip()
    pub_date = raw_item.get("pub_date", "").strip()

    if not title or not link:
        return None

    joined_text = f"{title} {summary}".strip()

    # HARD FILTER: remove opinion / admin / internal / non-event content
    noise_terms = [
        "boss", "editor", "who", "what", "why", "how",
        "analysis", "opinion", "interview", "says", "question",
        "debate", "explains", "review", "reaction"
    ]

    lowered = joined_text.lower()
    if any(term in lowered for term in noise_terms):
        return None

    classical_match = is_classical(joined_text)
    watchlist_match = matches_watchlist(joined_text, watchlist)

    location = extract_location(joined_text) or "Unknown"

    name = derive_name(title)
    event_date = parse_date(pub_date)
    status = derive_status(joined_text, official=bool(source.get("official", False)))
    fingerprint = fingerprint_for(name, location, event_date, link)

    return {
        "fingerprint": fingerprint,
        "name": name,
        "location": location,
        "event_date": event_date,
        "status": status,
        "link": link,
        "source_name": source["name"],
        "title": title,
        "summary": summary,
    }


def scan_source(source: dict[str, Any], watchlist: list[dict[str, Any]]) -> int:
    payload = fetch_bytes(source["url"])
    root = ET.fromstring(payload)
    raw_items = parse_rss_items(root)

    saved = 0
    for raw_item in raw_items:
        item = classify_item(source, raw_item, watchlist)
        if not item:
            continue
        upsert_feed_item(item)
        saved += 1

    return saved


def scan_all_sources() -> dict[str, int]:
    processed_sources = 0
    matched_items = 0
    errors = 0
    watchlist = list_watchlist()

    # ADD THIS BLOCK
    for item in fetch_bachtrack():
        upsert_feed_item(item)
        matched_items += 1

    for source in RSS_SOURCES:
        try:
            matched_items += scan_source(source, watchlist)
            processed_sources += 1
        except Exception as e:
            print(f"ERROR in {source['name']}: {e}")
            errors += 1

    return {
        "processed_sources": processed_sources,
        "matched_items": matched_items,
        "errors": errors,
    }
