from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

from config import DB_PATH, ITEM_TTL_DAYS


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def expiry_iso(days: int = ITEM_TTL_DAYS) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_conn()) as conn:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS feed_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                event_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('Confirmed', 'Unconfirmed')),
                link TEXT NOT NULL,
                source_name TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS muted_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                muted_at TEXT NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                watch_type TEXT NOT NULL DEFAULT '',
                alias TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )

        conn.commit()

def upsert_feed_item(item: dict[str, Any]) -> None:
    now_iso = utcnow_iso()
    expires = expiry_iso()

    with closing(get_conn()) as conn:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO feed_items (
                fingerprint, name, location, event_date, status, link,
                source_name, title, summary, detected_at, last_seen_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                name=excluded.name,
                location=excluded.location,
                event_date=excluded.event_date,
                status=excluded.status,
                link=excluded.link,
                source_name=excluded.source_name,
                title=excluded.title,
                summary=excluded.summary,
                last_seen_at=excluded.last_seen_at,
                expires_at=excluded.expires_at
            """,
            (
                item["fingerprint"],
                item["name"],
                item["location"],
                item["event_date"],
                item["status"],
                item["link"],
                item["source_name"],
                item["title"],
                item["summary"],
                now_iso,
                now_iso,
                expires,
            ),
        )

        conn.commit()


def expire_old_items() -> int:
    now_iso = utcnow_iso()

    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM feed_items WHERE expires_at < ?", (now_iso,))
        deleted = cur.rowcount
        conn.commit()
        return deleted


def list_active_items() -> list[dict[str, Any]]:
    now_iso = utcnow_iso()

    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                f.fingerprint,
                f.name,
                f.location,
                f.event_date,
                f.status,
                f.link
            FROM feed_items f
            LEFT JOIN muted_items m
                ON m.fingerprint = f.fingerprint
            WHERE m.fingerprint IS NULL
              AND f.expires_at >= ?
            ORDER BY f.last_seen_at DESC
            """,
            (now_iso,),
        )
        rows = cur.fetchall()
        return [dict(row) for row in rows]


def mute_item(fingerprint: str) -> bool:
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO muted_items (fingerprint, muted_at)
            VALUES (?, ?)
            """,
            (fingerprint, utcnow_iso()),
        )
        changed = cur.rowcount > 0
        conn.commit()
        return changed


def list_watchlist() -> list[dict[str, Any]]:
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, watch_type, alias, created_at
            FROM watchlist
            ORDER BY created_at DESC, id DESC
            """
        )
        rows = cur.fetchall()
        return [dict(row) for row in rows]


def add_watch_item(name: str, watch_type: str = "", alias: str = "") -> dict[str, Any]:
    clean_name = str(name or "").strip()
    clean_type = str(watch_type or "").strip()
    clean_alias = str(alias or "").strip()

    if not clean_name:
        raise ValueError("name is required")

    created_at = utcnow_iso()

    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO watchlist (name, watch_type, alias, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (clean_name, clean_type, clean_alias, created_at),
        )
        watch_id = cur.lastrowid
        conn.commit()

    return {
        "id": int(watch_id),
        "name": clean_name,
        "watch_type": clean_type,
        "alias": clean_alias,
        "created_at": created_at,
    }


def delete_watch_item(watch_id: int) -> bool:
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM watchlist WHERE id = ?", (watch_id,))
        changed = cur.rowcount > 0
        conn.commit()
        return changed