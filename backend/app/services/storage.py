"""SQLite-Persistenz für Such-Agent-Listings und Tarif-Beobachtungen.

Löst das In-Memory-Problem: Daten überleben Backend-Restarts.
Schema simpel gehalten (eine Tabelle je Domain), sync via check_same_
thread=False (FastAPI async — Schreibzugriffe sind kurz und selten).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "app.db"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            provider TEXT DEFAULT 'rss',
            city TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seen_listings (
            profile_id INTEGER NOT NULL,
            dedup_key TEXT NOT NULL,
            listing_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY (profile_id, dedup_key)
        );
        CREATE TABLE IF NOT EXISTS tariff_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portal TEXT NOT NULL,
            product TEXT NOT NULL,
            postal_code TEXT NOT NULL,
            tariff_name TEXT NOT NULL,
            ct_per_kwh REAL,
            base_fee_eur REAL DEFAULT 0,
            bonus_eur REAL DEFAULT 0,
            observed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_obs_product_plz
            ON tariff_observations (product, postal_code);
        CREATE INDEX IF NOT EXISTS idx_obs_portal_time
            ON tariff_observations (portal, observed_at);
        """
    )
    conn.commit()


# ----------------------------------------------------------------------
# Suchprofile
# ----------------------------------------------------------------------

def save_profile(name: str, url: str, provider: str = "rss", city: str | None = None) -> int:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO search_profiles (name, url, provider, city, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, url, provider, city, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def list_profiles(active_only: bool = True) -> list[dict]:
    conn = _get_conn()
    q = "SELECT id, name, url, provider, city, active FROM search_profiles"
    if active_only:
        q += " WHERE active = 1"
    rows = conn.execute(q + " ORDER BY id").fetchall()
    return [
        {"id": r[0], "name": r[1], "url": r[2], "provider": r[3],
         "city": r[4], "active": bool(r[5])}
        for r in rows
    ]


def get_profile(profile_id: int) -> dict | None:
    conn = _get_conn()
    r = conn.execute(
        "SELECT id, name, url, provider, city, active FROM search_profiles "
        "WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "url": r[2], "provider": r[3],
            "city": r[4], "active": bool(r[5])}


# ----------------------------------------------------------------------
# Seen-Listings (Dedup + Archiv)
# ----------------------------------------------------------------------

def is_seen(profile_id: int, dedup_key: str) -> bool:
    conn = _get_conn()
    r = conn.execute(
        "SELECT 1 FROM seen_listings WHERE profile_id = ? AND dedup_key = ?",
        (profile_id, dedup_key),
    ).fetchone()
    return r is not None


def save_seen_listing(profile_id: int, dedup_key: str, listing: dict) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO seen_listings (profile_id, dedup_key, listing_json, first_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (profile_id, dedup_key, json.dumps(listing, ensure_ascii=False),
             datetime.now(UTC).isoformat()),
        )
        conn.commit()


def list_listings(profile_id: int, limit: int = 100) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT listing_json, first_seen_at FROM seen_listings "
        "WHERE profile_id = ? ORDER BY first_seen_at DESC LIMIT ?",
        (profile_id, limit),
    ).fetchall()
    result = []
    for r in rows:
        item = json.loads(r[0])
        item["first_seen_at"] = r[1]
        result.append(item)
    return result


def count_listings(profile_id: int) -> int:
    conn = _get_conn()
    r = conn.execute(
        "SELECT COUNT(*) FROM seen_listings WHERE profile_id = ?",
        (profile_id,),
    ).fetchone()
    return int(r[0] or 0)


# ----------------------------------------------------------------------
# Tarif-Beobachtungen
# ----------------------------------------------------------------------

def save_observation(
    portal: str, product: str, postal_code: str, tariff_name: str,
    ct_per_kwh: float | None, base_fee_eur: float = 0, bonus_eur: float = 0,
    observed_at: str | None = None,
) -> int:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO tariff_observations "
            "(portal, product, postal_code, tariff_name, ct_per_kwh, "
            "base_fee_eur, bonus_eur, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (portal, product, postal_code, tariff_name, ct_per_kwh,
             base_fee_eur, bonus_eur,
             observed_at or datetime.now(UTC).isoformat()),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def load_observations(product: str, postal_code: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT portal, product, postal_code, tariff_name, ct_per_kwh, "
        "base_fee_eur, bonus_eur, observed_at FROM tariff_observations "
        "WHERE product = ? AND postal_code = ? ORDER BY observed_at",
        (product, postal_code),
    ).fetchall()
    return [
        {"portal": r[0], "product": r[1], "postal_code": r[2],
         "tariff_name": r[3], "ct_per_kwh": r[4], "base_fee_eur_per_year": r[5],
         "bonus_eur_first_year": r[6], "observed_at": r[7]}
        for r in rows
    ]


def reset_db_for_tests() -> None:
    """Test-Hook: Tabellen leeren (nicht die Datei löschen)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.executescript(
                "DELETE FROM search_profiles; DELETE FROM seen_listings; "
                "DELETE FROM tariff_observations;"
            )
            _conn.commit()
