"""Gravação de leads vindos das fontes, com deduplicação entre fontes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from leadfinder import urls
from leadfinder.db import add_history, dumps, now_iso
from leadfinder.normalize import (
    distinctive_tokens,
    norm_text,
    normalize_cep,
    normalize_cnpj,
    normalize_phone,
    phone_key,
)


@dataclass
class LeadRecord:
    name: str
    source: str  # osm | cnpj | csv | manual
    source_ref: str
    source_url: str | None = None
    segment: str | None = None
    category_raw: str | None = None
    phone: str | None = None
    phone_source: str | None = None
    whatsapp: str | None = None
    email: str | None = None
    email_source: str | None = None
    street: str | None = None
    housenumber: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    uf: str | None = None
    postcode: str | None = None
    lat: float | None = None
    lon: float | None = None
    cnpj: str | None = None
    is_chain: bool = False
    chain_reason: str | None = None
    links: list[tuple[str, str]] = field(default_factory=list)  # (url, origem)
    raw: Any = None


MERGE_FIELDS = [
    "segment", "category_raw", "phone", "phone_norm", "phone_source", "whatsapp",
    "whatsapp_norm", "email", "email_source", "street", "housenumber", "neighborhood",
    "city", "uf", "postcode", "lat", "lon", "cnpj",
]


def _find_existing(conn: sqlite3.Connection, rec: LeadRecord, phone_norm: str | None,
                   cnpj: str | None) -> int | None:
    row = conn.execute(
        "SELECT lead_id FROM lead_sources WHERE source=? AND source_ref=?",
        (rec.source, rec.source_ref),
    ).fetchone()
    if row:
        return row["lead_id"]
    if cnpj:
        row = conn.execute("SELECT id FROM leads WHERE cnpj=?", (cnpj,)).fetchone()
        if row:
            return row["id"]
    name_norm = norm_text(rec.name)
    if phone_norm:
        key = phone_key(phone_norm)
        for r in conn.execute(
            "SELECT id, phone_norm, name_norm FROM leads WHERE phone_norm LIKE ?",
            (f"{key[0]}%{key[1]}",),
        ):
            # Mesmo telefone. Exige também um termo DISTINTIVO do nome em comum
            # (não "studio", "restaurante"...) para não fundir empresas
            # diferentes que compartilham um número.
            mine, theirs = set(distinctive_tokens(rec.name)), set(distinctive_tokens(r["name_norm"]))
            if (mine & theirs) or (not mine and not theirs and name_norm == r["name_norm"]):
                return r["id"]
    place = norm_text(rec.neighborhood) or norm_text(rec.city)
    if name_norm and place:
        for r in conn.execute("SELECT id, neighborhood, city FROM leads WHERE name_norm=?", (name_norm,)):
            if place in (norm_text(r["neighborhood"]), norm_text(r["city"])):
                return r["id"]
    return None


def add_link(conn: sqlite3.Connection, lead_id: int, url: str, origin: str,
             association: str) -> urls.UrlClass | None:
    """Registra um link do lead. ``association`` diz COMO sabemos que ele é da
    empresa: 'fonte' (veio no registro da fonte), 'agregador' (link na bio da
    própria empresa), 'evidencia' (conteúdo confere), 'usuario' (informado pelo
    usuário) ou 'nao_confirmado'."""
    norm = urls.normalize_url(url)
    if not norm:
        return None
    cls = urls.classify_url(norm)
    existing = conn.execute(
        "SELECT id, association FROM links WHERE lead_id=? AND url_norm=?", (lead_id, norm)
    ).fetchone()
    rank = {"nao_confirmado": 0, "fonte": 2, "agregador": 2, "evidencia": 3, "usuario": 4}
    if existing:
        if rank.get(association, 0) > rank.get(existing["association"], 0):
            conn.execute("UPDATE links SET association=?, origin=? WHERE id=?",
                         (association, origin, existing["id"]))
        return cls
    conn.execute(
        "INSERT INTO links(lead_id, url, url_norm, kind, platform, origin, association, first_seen_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (lead_id, url.strip(), norm, cls.kind, cls.platform, origin, association, now_iso()),
    )
    return cls


def compute_discovery_priority(conn: sqlite3.Connection, lead_id: int) -> tuple[str, str]:
    kinds = [r["kind"] for r in conn.execute(
        "SELECT kind FROM links WHERE lead_id=? AND association IN ('fonte','usuario','agregador')",
        (lead_id,),
    )]
    if any(k in urls.SITE_KINDS for k in kinds):
        return "baixa", "Registro da fonte já traz um link de site (será verificado)"
    if any(k in urls.PRESENCE_KINDS or k == urls.DIRETORIO for k in kinds):
        return "alta", "Registro só traz redes sociais/plataformas"
    return "alta", "Campo de site vazio na fonte"


def upsert_record(conn: sqlite3.Connection, rec: LeadRecord) -> tuple[int, bool]:
    """Insere ou mescla um registro. Nunca altera campos de correção humana."""
    now = now_iso()
    phone_norm = normalize_phone(rec.phone)
    wa_norm = normalize_phone(rec.whatsapp)
    cnpj = normalize_cnpj(rec.cnpj)
    values = {
        "segment": rec.segment,
        "category_raw": rec.category_raw,
        "phone": rec.phone,
        "phone_norm": phone_norm,
        "phone_source": rec.phone_source or rec.source,
        "whatsapp": rec.whatsapp,
        "whatsapp_norm": wa_norm,
        "email": (rec.email or "").strip().lower() or None,
        "email_source": rec.email_source or (rec.source if rec.email else None),
        "street": rec.street,
        "housenumber": rec.housenumber,
        "neighborhood": rec.neighborhood,
        "city": rec.city,
        "uf": rec.uf,
        "postcode": normalize_cep(rec.postcode) or rec.postcode,
        "lat": rec.lat,
        "lon": rec.lon,
        "cnpj": cnpj,
    }
    if not phone_norm:
        values["phone_source"] = None
    lead_id = _find_existing(conn, rec, phone_norm, cnpj)
    created = lead_id is None
    if created:
        cur = conn.execute(
            "INSERT INTO leads(name, name_norm, is_chain, chain_reason, created_at, updated_at, "
            + ", ".join(MERGE_FIELDS)
            + ") VALUES (?,?,?,?,?,?," + ",".join("?" * len(MERGE_FIELDS)) + ")",
            (rec.name.strip(), norm_text(rec.name), int(rec.is_chain), rec.chain_reason, now, now,
             *[values[f] for f in MERGE_FIELDS]),
        )
        lead_id = cur.lastrowid
    else:
        current = dict(conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone())
        updates = {f: values[f] for f in MERGE_FIELDS if values[f] not in (None, "") and current.get(f) in (None, "")}
        # telefone e sua origem andam juntos
        if "phone" not in updates:
            updates.pop("phone_norm", None)
            updates.pop("phone_source", None)
        if "email" not in updates:
            updates.pop("email_source", None)
        if rec.is_chain and not current["is_chain"]:
            updates["is_chain"] = 1
            updates["chain_reason"] = rec.chain_reason
        if updates:
            updates["updated_at"] = now
            conn.execute(
                "UPDATE leads SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?",
                (*updates.values(), lead_id),
            )
    conn.execute(
        "INSERT INTO lead_sources(lead_id, source, source_ref, source_url, fetched_at, raw_json)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(source, source_ref) DO UPDATE SET fetched_at=excluded.fetched_at, raw_json=excluded.raw_json",
        (lead_id, rec.source, rec.source_ref, rec.source_url, now, dumps(rec.raw) if rec.raw is not None else None),
    )
    for url, origin in rec.links:
        add_link(conn, lead_id, url, origin, "fonte")
    if rec.whatsapp:
        wa = normalize_phone(rec.whatsapp)
        if wa:
            add_link(conn, lead_id, f"https://wa.me/55{wa}", f"{rec.source}:whatsapp", "fonte")
    prio, note = compute_discovery_priority(conn, lead_id)
    conn.execute("UPDATE leads SET discovery_priority=?, discovery_note=? WHERE id=?", (prio, note, lead_id))
    if created:
        add_history(conn, lead_id, "auto", "lead", None, "criado", f"fonte: {rec.source} ({rec.source_ref})")
    return lead_id, created


def get_lead(conn: sqlite3.Connection, lead_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    return dict(row) if row else None


def get_links(conn: sqlite3.Connection, lead_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM links WHERE lead_id=? ORDER BY id", (lead_id,)
    )]


def get_sources(conn: sqlite3.Connection, lead_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM lead_sources WHERE lead_id=? ORDER BY id", (lead_id,)
    )]
