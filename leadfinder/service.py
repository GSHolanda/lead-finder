"""Operações de alto nível usadas pela interface web e pela linha de comando."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import quote

from leadfinder import statuses as st
from leadfinder import urls
from leadfinder.config import get_settings, make_client
from leadfinder.db import loads, rows_to_dicts
from leadfinder.discovery.segments import segment_label
from leadfinder.enrichment import FACT_LABELS, business_summary, enrich_lead, get_facts
from leadfinder.leads import get_lead, get_links, get_sources
from leadfinder.ollama import make_ollama
from leadfinder.outreach import latest_message
from leadfinder.qualification import best_contact, qualify_lead
from leadfinder.verification.search import make_provider, manual_search_links
from leadfinder.verification.verifier import VerifyContext, verify_lead


@dataclass
class Services:
    settings: dict
    verify_ctx: VerifyContext
    ollama: object


def build_services(conn: sqlite3.Connection) -> Services:
    settings = get_settings(conn)
    client = make_client(settings)
    provider = make_provider(settings, client)
    return Services(settings, VerifyContext(client=client, provider=provider, settings=settings), make_ollama(settings))


def process_lead(conn: sqlite3.Connection, lead_id: int, svc: Services, verify: bool = True,
                 enrich: bool = True) -> dict:
    out: dict = {"lead_id": lead_id}
    if verify:
        out["verificacao"] = {k: v for k, v in verify_lead(conn, lead_id, svc.verify_ctx).items() if k != "summary"}
    if enrich:
        out["enriquecimento"] = enrich_lead(conn, lead_id, svc.ollama)
    out["qualificacao"] = qualify_lead(conn, lead_id, svc.settings)
    return out


def requalify(conn: sqlite3.Connection, lead_id: int, settings: dict | None = None) -> dict:
    return qualify_lead(conn, lead_id, settings or get_settings(conn))


VIEW_SQL = {
    "campanha": "q.in_campaign = 1",
    "verificar": ("COALESCE(l.site_status_manual, l.site_status_auto) IN ('pendente','inconclusiva','possivel_indisponivel')"
                  " OR (COALESCE(l.site_status_manual, l.site_status_auto) IN ('somente_redes','nao_localizado')"
                  "     AND COALESCE(q.in_campaign, 0) = 0)"),
    "com_site": "COALESCE(l.site_status_manual, l.site_status_auto) = 'site_proprio'",
    "todos": "1=1",
}


def list_leads(conn: sqlite3.Connection, view: str = "campanha", segment: str | None = None,
               search: str | None = None, status: str | None = None, limit: int = 500) -> list[dict]:
    where = [f"({VIEW_SQL.get(view, '1=1')})"]
    params: list = []
    if segment:
        where.append("l.segment = ?")
        params.append(segment)
    if status:
        where.append("COALESCE(l.site_status_manual, l.site_status_auto) = ?")
        params.append(status)
    if search:
        where.append("(l.name_norm LIKE ? OR LOWER(COALESCE(l.neighborhood,'')) LIKE ? OR LOWER(COALESCE(l.city,'')) LIKE ?)")
        from leadfinder.normalize import norm_text

        s = f"%{norm_text(search)}%"
        params += [s, f"%{search.lower()}%", f"%{search.lower()}%"]
    order = {
        "campanha": "q.score DESC, l.name",
        "verificar": "CASE l.discovery_priority WHEN 'alta' THEN 0 ELSE 1 END, l.id",
    }.get(view, "l.name")
    rows = conn.execute(
        "SELECT l.*, q.score, q.in_campaign, q.exclusion, q.next_action FROM leads l"
        " LEFT JOIN qualification q ON q.lead_id = l.id WHERE " + " AND ".join(where)
        + f" ORDER BY {order} LIMIT ?", (*params, limit)
    ).fetchall()
    out = []
    for r in rows:
        lead = dict(r)
        eff = st.effective_status(lead)
        out.append({
            "id": lead["id"],
            "nome": lead["name"],
            "segmento": segment_label(lead["segment"]),
            "local": ", ".join(p for p in (lead["neighborhood"], lead["city"]) if p),
            "status": eff,
            "status_label": st.SITE_STATUS_LABELS.get(eff, eff),
            "manual": bool(lead["site_status_manual"]),
            "confianca": lead["site_confidence_auto"],
            "confianca_label": "manual" if lead["site_status_manual"] else st.confidence_label(lead["site_confidence_auto"]),
            "score": lead["score"],
            "proxima_acao": lead["next_action"] or ("Executar a verificação de site" if eff == st.PENDENTE else None),
            "funil": lead["pipeline_status"],
            "prioridade": lead["discovery_priority"],
            "rede": bool(lead["is_chain"]),
            "tem_contato": bool(lead["phone_norm"] or lead["whatsapp_norm"] or lead["email"]),
        })
    return out


def counts(conn: sqlite3.Connection) -> dict:
    out = {}
    for view in VIEW_SQL:
        out[view] = conn.execute(
            "SELECT COUNT(*) FROM leads l LEFT JOIN qualification q ON q.lead_id=l.id WHERE " + VIEW_SQL[view]
        ).fetchone()[0]
    by_status = conn.execute(
        "SELECT COALESCE(site_status_manual, site_status_auto) AS s, COUNT(*) AS n FROM leads GROUP BY s"
    ).fetchall()
    out["por_status"] = {r["s"]: r["n"] for r in by_status}
    return out


def lead_detail(conn: sqlite3.Connection, lead_id: int) -> dict:
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise ValueError("lead não encontrado")
    links = get_links(conn, lead_id)
    facts = get_facts(conn, lead_id)
    q = conn.execute("SELECT * FROM qualification WHERE lead_id=?", (lead_id,)).fetchone()
    q = dict(q) if q else None
    run = None
    checks: list[dict] = []
    if lead.get("last_run_id"):
        r = conn.execute("SELECT * FROM verification_runs WHERE id=?", (lead["last_run_id"],)).fetchone()
        if r:
            run = dict(r)
            run["summary"] = loads(run.pop("summary_json"), {})
            checks = rows_to_dicts(conn.execute(
                "SELECT check_type, source, query, url, performed_at, outcome, detail FROM verification_checks"
                " WHERE run_id=? ORDER BY id", (run["id"],)))
    runs = rows_to_dicts(conn.execute(
        "SELECT id, started_at, result_status, confidence FROM verification_runs WHERE lead_id=? ORDER BY id DESC",
        (lead_id,)))
    history = rows_to_dicts(conn.execute(
        "SELECT * FROM status_history WHERE lead_id=? ORDER BY id DESC", (lead_id,)))
    materials = rows_to_dicts(conn.execute(
        "SELECT * FROM user_materials WHERE lead_id=? ORDER BY id DESC", (lead_id,)))
    message = latest_message(conn, lead_id)
    contact = best_contact(lead, links)
    eff = st.effective_status(lead)
    wa_send = None
    if message and message["status"] == "aprovada" and contact.get("whatsapp_digits"):
        wa_send = f"https://wa.me/55{contact['whatsapp_digits']}?text={quote(message['body'])}"
    sources = get_sources(conn, lead_id)
    for s in sources:
        s.pop("raw_json", None)
    return {
        "lead": lead,
        "segmento_label": segment_label(lead.get("segment")),
        "fontes": sources,
        "site": {
            "status": eff,
            "status_label": st.SITE_STATUS_LABELS.get(eff, eff),
            "auto": {
                "status": lead["site_status_auto"],
                "label": st.SITE_STATUS_LABELS.get(lead["site_status_auto"]),
                "confianca": lead["site_confidence_auto"],
                "confianca_label": st.confidence_label(lead["site_confidence_auto"]),
                "url": lead["site_url_auto"],
                "motivo": lead["site_reason_auto"],
                "verificado_em": lead["site_checked_at"],
            },
            "manual": {
                "status": lead["site_status_manual"],
                "label": st.SITE_STATUS_LABELS.get(lead["site_status_manual"]) if lead["site_status_manual"] else None,
                "url": lead["site_url_manual"],
                "origem": st.MANUAL_SOURCES.get(lead["site_manual_source"]) if lead["site_manual_source"] else None,
                "nota": lead["site_manual_note"],
                "em": lead["site_manual_at"],
            },
            "url": st.effective_site_url(lead),
            "ultima_execucao": run,
            "consultas": checks,
            "execucoes": runs,
            "busca_manual": manual_search_links(lead),
        },
        "links": [{**l, "tipo_label": urls.KIND_LABELS.get(l["kind"], l["kind"])} for l in links],
        "contato": contact,
        "fatos": [{**f, "tipo_label": FACT_LABELS.get(f["kind"], f["kind"])} for f in facts],
        "materiais": materials,
        "resumo": (q or {}).get("summary") or business_summary(lead, facts),
        "qualificacao": {
            "score": (q or {}).get("score"),
            "na_campanha": bool((q or {}).get("in_campaign")),
            "exclusao": (q or {}).get("exclusion"),
            "composicao": loads((q or {}).get("breakdown_json"), {}),
            "aplicacoes": loads((q or {}).get("benefits_json"), {"com_evidencia": [], "sugestoes_segmento": []}),
            "proxima_acao": (q or {}).get("next_action") or "Executar a verificação de site",
        },
        "mensagem": message,
        "whatsapp_envio": wa_send,
        "historico": history,
    }
