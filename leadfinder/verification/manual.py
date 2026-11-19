"""Correções humanas da classificação de site.

A correção fica em colunas próprias (``site_status_manual`` etc.), prevalece
sobre a classificação automática e nunca é alterada por rotinas automáticas.
Todas as mudanças vão para o histórico.
"""

from __future__ import annotations

import sqlite3

from leadfinder import statuses as st
from leadfinder.db import add_history, now_iso
from leadfinder.leads import add_link, get_lead


def set_manual_status(
    conn: sqlite3.Connection,
    lead_id: int,
    status: str,
    url: str | None = None,
    source: str = "pesquisa_manual",
    note: str | None = None,
) -> dict:
    if status not in st.SITE_STATUSES:
        raise ValueError(f"classificação inválida: {status}")
    if source not in st.MANUAL_SOURCES:
        raise ValueError(f"origem inválida: {source}")
    if status == st.SITE_PROPRIO and not url:
        raise ValueError("informe a URL do site encontrado")
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise ValueError("lead não encontrado")
    url = (url or "").strip() or None
    conn.execute(
        "UPDATE leads SET site_status_manual=?, site_url_manual=?, site_manual_source=?, site_manual_note=?,"
        " site_manual_at=?, updated_at=? WHERE id=?",
        (status, url, source, note, now_iso(), now_iso(), lead_id),
    )
    if url:
        add_link(conn, lead_id, url, f"manual:{source}", "usuario")
    detail = f"origem: {st.MANUAL_SOURCES[source]}"
    if url:
        detail += f"; URL: {url}"
    if note:
        detail += f"; nota: {note}"
    add_history(conn, lead_id, "humano", "site_status_manual", lead.get("site_status_manual"), status, detail)
    conn.commit()
    return get_lead(conn, lead_id)


def clear_manual_status(conn: sqlite3.Connection, lead_id: int, note: str | None = None) -> dict:
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise ValueError("lead não encontrado")
    if lead.get("site_status_manual"):
        conn.execute(
            "UPDATE leads SET site_status_manual=NULL, site_url_manual=NULL, site_manual_source=NULL,"
            " site_manual_note=NULL, site_manual_at=NULL, updated_at=? WHERE id=?",
            (now_iso(), lead_id),
        )
        add_history(conn, lead_id, "humano", "site_status_manual", lead["site_status_manual"], None,
                    "correção removida pelo usuário" + (f"; nota: {note}" if note else ""))
        conn.commit()
    return get_lead(conn, lead_id)


def set_chain_flag(conn: sqlite3.Connection, lead_id: int, is_chain: bool, note: str | None = None) -> dict:
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise ValueError("lead não encontrado")
    conn.execute("UPDATE leads SET is_chain=?, chain_reason=?, updated_at=? WHERE id=?",
                 (int(is_chain), (note or "marcado pelo usuário") if is_chain else None, now_iso(), lead_id))
    add_history(conn, lead_id, "humano", "is_chain", lead["is_chain"], int(is_chain), note)
    conn.commit()
    return get_lead(conn, lead_id)


def set_pipeline_status(conn: sqlite3.Connection, lead_id: int, status: str, note: str | None = None) -> dict:
    if status not in st.PIPELINE_STATUSES:
        raise ValueError("status de funil inválido")
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise ValueError("lead não encontrado")
    conn.execute("UPDATE leads SET pipeline_status=?, updated_at=? WHERE id=?", (status, now_iso(), lead_id))
    add_history(conn, lead_id, "humano", "pipeline_status", lead["pipeline_status"], status, note)
    conn.commit()
    return get_lead(conn, lead_id)
