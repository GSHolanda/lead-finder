"""Armazenamento local em SQLite.

Princípios do modelo de dados:
- Toda consulta de verificação fica registrada (``verification_checks``), com
  data, fonte, consulta/URL e resultado — falha de consulta é registrada como
  falha, nunca como "ausência de site".
- A classificação automática (``site_status_auto``) e a correção humana
  (``site_status_manual``) ficam em colunas separadas. Rotinas automáticas
  nunca escrevem nas colunas manuais.
- Toda mudança de classificação vai para ``status_history``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    segment TEXT,
    category_raw TEXT,
    phone TEXT,
    phone_norm TEXT,
    phone_source TEXT,
    whatsapp TEXT,
    whatsapp_norm TEXT,
    email TEXT,
    email_source TEXT,
    street TEXT,
    housenumber TEXT,
    neighborhood TEXT,
    city TEXT,
    uf TEXT,
    postcode TEXT,
    lat REAL,
    lon REAL,
    cnpj TEXT,
    is_chain INTEGER NOT NULL DEFAULT 0,
    chain_reason TEXT,
    discovery_priority TEXT NOT NULL DEFAULT 'alta',
    discovery_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    site_status_auto TEXT NOT NULL DEFAULT 'pendente',
    site_confidence_auto REAL,
    site_url_auto TEXT,
    site_reason_auto TEXT,
    site_checked_at TEXT,
    last_run_id INTEGER,

    site_status_manual TEXT,
    site_url_manual TEXT,
    site_manual_source TEXT,
    site_manual_note TEXT,
    site_manual_at TEXT,

    pipeline_status TEXT NOT NULL DEFAULT 'novo',
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone_norm);
CREATE INDEX IF NOT EXISTS idx_leads_cnpj ON leads(cnpj);
CREATE INDEX IF NOT EXISTS idx_leads_name ON leads(name_norm);

CREATE TABLE IF NOT EXISTS lead_sources (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_url TEXT,
    fetched_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE(source, source_ref)
);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    url_norm TEXT NOT NULL,
    kind TEXT NOT NULL,
    platform TEXT,
    origin TEXT NOT NULL,
    association TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_result TEXT,
    UNIQUE(lead_id, url_norm)
);

CREATE TABLE IF NOT EXISTS verification_runs (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_status TEXT,
    confidence REAL,
    reason TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS verification_checks (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES verification_runs(id) ON DELETE CASCADE,
    lead_id INTEGER NOT NULL,
    check_type TEXT NOT NULL,
    source TEXT,
    query TEXT,
    url TEXT,
    performed_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_run ON verification_checks(run_id);

CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    changed_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS page_cache (
    url TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    final_url TEXT,
    title TEXT,
    text TEXT
);

CREATE TABLE IF NOT EXISTS user_materials (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    title TEXT,
    content TEXT NOT NULL,
    source_url TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    value_norm TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    evidence TEXT,
    method TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_lead ON facts(lead_id);

CREATE TABLE IF NOT EXISTS fact_rejections (
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value_norm TEXT NOT NULL,
    rejected_at TEXT NOT NULL,
    PRIMARY KEY (lead_id, kind, value_norm)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    generator TEXT NOT NULL,
    guardrail_json TEXT,
    status TEXT NOT NULL,
    edited_by_human INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS qualification (
    lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
    score REAL,
    in_campaign INTEGER NOT NULL DEFAULT 0,
    exclusion TEXT,
    breakdown_json TEXT,
    benefits_json TEXT,
    summary TEXT,
    next_action TEXT,
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pilot_samples (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seed INTEGER NOT NULL,
    criteria_json TEXT
);

CREATE TABLE IF NOT EXISTS pilot_items (
    id INTEGER PRIMARY KEY,
    sample_id INTEGER NOT NULL REFERENCES pilot_samples(id) ON DELETE CASCADE,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    status_at_sampling TEXT NOT NULL,
    confidence_at_sampling REAL,
    segment_at_sampling TEXT,
    review_result TEXT,
    found_url TEXT,
    how_found TEXT,
    note TEXT,
    reviewed_at TEXT,
    UNIQUE(sample_id, lead_id)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_db_path() -> str:
    env = os.environ.get("LEADFINDER_DB")
    if env:
        return env
    return str(Path.cwd() / "data" / "leadfinder.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or default_db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def add_history(
    conn: sqlite3.Connection,
    lead_id: int,
    actor: str,
    field: str,
    old_value: Any,
    new_value: Any,
    note: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO status_history(lead_id, changed_at, actor, field, old_value, new_value, note)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            lead_id,
            now_iso(),
            actor,
            field,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            note,
        ),
    )
