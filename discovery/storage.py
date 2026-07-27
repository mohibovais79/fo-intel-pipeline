"""
fo-intel-pipeline — persistence layer

SQLite is the source of truth between pipeline stages; JSONL exports per
stage are git-diffable for audit. Both are written under data/.

The raw_candidates table holds every foundation that passed the asset
threshold, before qualification. The excluded_candidates table mirrors
schema.ExcludedCandidate for firms that failed the qualification gate.
This separation is what lets the source-mix ratio be audited mid-run
without re-deriving it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from config import DATABASE_PATH, DISCOVERY_OUTPUT_DIR


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"not serializable: {type(obj)}")


def get_db(path: str = DATABASE_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS raw_candidates (
            record_id        TEXT PRIMARY KEY,
            discovery_source TEXT NOT NULL,
            state_targeting  TEXT,
            ein              TEXT,
            entity_name      TEXT NOT NULL,
            city             TEXT,
            state_region     TEXT,
            street_address   TEXT,
            assets_usd       INTEGER,
            tax_year         INTEGER,
            officers_json    TEXT,        -- list of {name, title} dicts
            xml_object_id    TEXT,
            xml_fetch_status TEXT,        -- 'ok' | 'no_xml' | 'error'
            discovered_at    TEXT NOT NULL,
            discovery_note   TEXT
        );

        CREATE TABLE IF NOT EXISTS excluded_candidates (
            record_id            TEXT PRIMARY KEY,
            discovery_source     TEXT NOT NULL,
            entity_name          TEXT NOT NULL,
            reason_excluded      TEXT NOT NULL,
            fo_type_considered   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id        TEXT PRIMARY KEY,
            stage         TEXT NOT NULL,
            started_at    TEXT NOT NULL,
            finished_at   TEXT,
            counts_json   TEXT,
            notes         TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_candidates(discovery_source);
        CREATE INDEX IF NOT EXISTS idx_raw_state  ON raw_candidates(state_targeting);
        CREATE INDEX IF NOT EXISTS idx_raw_assets ON raw_candidates(assets_usd);
        """
    )
    conn.commit()


def upsert_raw_candidate(conn: sqlite3.Connection, c: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO raw_candidates
            (record_id, discovery_source, state_targeting, ein, entity_name,
             city, state_region, street_address, assets_usd, tax_year,
             officers_json, xml_object_id, xml_fetch_status, discovered_at,
             discovery_note)
        VALUES
            (:record_id, :discovery_source, :state_targeting, :ein, :entity_name,
             :city, :state_region, :street_address, :assets_usd, :tax_year,
             :officers_json, :xml_object_id, :xml_fetch_status, :discovered_at,
             :discovery_note)
        ON CONFLICT(record_id) DO UPDATE SET
            officers_json   = excluded.officers_json,
            xml_object_id   = excluded.xml_object_id,
            xml_fetch_status= excluded.xml_fetch_status
        """,
        c,
    )
    conn.commit()


def insert_excluded(conn: sqlite3.Connection, e: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO excluded_candidates
            (record_id, discovery_source, entity_name, reason_excluded,
             fo_type_considered)
        VALUES
            (:record_id, :discovery_source, :entity_name, :reason_excluded,
             :fo_type_considered)
        """,
        e,
    )
    conn.commit()


def log_run(
    conn: sqlite3.Connection,
    run_id: str,
    stage: str,
    started_at: str,
    finished_at: str | None = None,
    counts: dict[str, int] | None = None,
    notes: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO pipeline_runs
            (run_id, stage, started_at, finished_at, counts_json, notes)
        VALUES
            (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            stage,
            started_at,
            finished_at,
            json.dumps(counts) if counts else None,
            notes,
        ),
    )
    conn.commit()


def export_jsonl(rows: list[sqlite3.Row], stage: str) -> Path:
    out_dir = Path(DISCOVERY_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stage}.jsonl"
    with path.open("w") as f:
        for row in rows:
            d = {k: row[k] for k in row.keys()}
            f.write(json.dumps(d, default=_json_default) + "\n")
    return path


def source_mix_ratio(conn: sqlite3.Connection) -> dict[str, float]:
    """Live source-mix audit — called mid-run to enforce the diversity guard."""
    rows = conn.execute(
        "SELECT discovery_source, COUNT(*) AS n FROM raw_candidates GROUP BY discovery_source"
    ).fetchall()
    total = sum(r["n"] for r in rows) or 1
    return {r["discovery_source"]: r["n"] / total for r in rows}
