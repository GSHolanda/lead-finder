"""Qualificação para venda de sites.

Pontuação (0–100), sempre com a composição visível:
- Verificação (até 35): site próprio não localizado × confiança da verificação;
- Aderência ao serviço (até 20): segmento entre os segmentos-alvo;
- Contato comercial (até 20): WhatsApp/celular > fixo > e-mail;
- Benefício plausível e específico (até 15): aplicações de site com evidência;
- Qualidade das evidências (até 10): quantidade e diversidade de fontes.

Não presume faturamento, orçamento, interesse ou "perda de clientes".
Empresas com site próprio encontrado vão para a lista separada.
"""

from __future__ import annotations

import sqlite3

from leadfinder import statuses as st
from leadfinder import urls
from leadfinder.db import dumps, now_iso
from leadfinder.discovery.segments import SEGMENTS
from leadfinder.enrichment import business_summary, get_facts
from leadfinder.leads import get_lead, get_links
from leadfinder.normalize import format_phone, is_mobile, norm_text

BENEFIT_TITLES = {
    "catalogo": "Catálogo/cardápio online",
    "servicos": "Página de apresentação dos serviços",
    "orcamento": "Pedido de orçamento pelo site",
    "portfolio": "Portfólio de trabalhos",
    "localizacao": "Localização e horário em um só lugar",
    "whatsapp": "Botão de contato pelo WhatsApp",
    "agendamento": "Acesso direto ao agendamento",
}

MANUAL_CONFIDENCE = {"empresa": 1.0, "pesquisa_manual": 0.9, "piloto": 0.9, "outro": 0.8}


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    t = norm_text(text)
    return any(w in t for w in words)


def possible_benefits(lead: dict, facts: list[dict], links: list[dict]) -> tuple[list[dict], list[dict]]:
    """Retorna (benefícios com evidência, sugestões do segmento sem evidência específica)."""
    by: dict[str, list[dict]] = {}
    for f in facts:
        by.setdefault(f["kind"], []).append(f)
    assoc_links = [l for l in links if l["association"] in ("fonte", "agregador", "usuario", "evidencia")]
    kinds = {l["kind"] for l in assoc_links}
    out: list[dict] = []

    def add(key: str, why: str, evid: list[str]) -> None:
        out.append({"chave": key, "titulo": BENEFIT_TITLES[key], "justificativa": why, "evidencias": evid})

    produtos = by.get("produto", [])
    if produtos or urls.DELIVERY in kinds or urls.MARKETPLACE in kinds:
        ev = [f"{p['value']} ({p['source']})" for p in produtos[:4]]
        ev += [f"{l['platform'] or l['kind']}: {l['url']}" for l in assoc_links if l["kind"] in (urls.DELIVERY, urls.MARKETPLACE)][:2]
        add("catalogo", "Há produtos/cardápio ou vendas em plataforma publicados; um site pode reunir o catálogo.", ev)
    servicos = by.get("servico", [])
    if len(servicos) >= 2:
        add("servicos", f"{len(servicos)} serviços citados nas fontes; podem ser apresentados em uma página.",
            [f"{s['value']} ({s['source']})" for s in servicos[:5]])
    texts = " ".join(f["value"] + " " + (f.get("evidence") or "") for f in facts)
    seg_benefits = set((SEGMENTS.get(lead.get("segment") or "") or {}).get("beneficios", []))
    if _contains_any(texts, ("orcamento",)):
        add("orcamento", "As fontes mencionam orçamento como forma de contratação.",
            [f["value"] for f in facts if _contains_any(f["value"] + " " + (f.get("evidence") or ""), ("orcamento",))][:3])
    if _contains_any(texts, ("portfolio", "nossos trabalhos", "antes e depois", "projetos realizados")):
        add("portfolio", "As fontes mencionam trabalhos/portfólio; um site pode organizá-los.",
            [f["value"] for f in facts if _contains_any(f["value"] + " " + (f.get("evidence") or ""),
                                                        ("portfolio", "trabalhos", "antes e depois", "projetos"))][:3])
    if lead.get("street"):
        ev = [f"{f['value']} ({f['source']})" for f in by.get("endereco", [])[:1]]
        ev += [f"Horário: {f['value']}" for f in by.get("horario", [])[:1]]
        add("localizacao", "Há endereço físico publicado; um site pode mostrar mapa, endereço e horário.", ev)
    wa = lead.get("whatsapp_norm") or (lead.get("phone_norm") if is_mobile(lead.get("phone_norm")) else None)
    if urls.WHATSAPP in kinds or wa:
        ev = [l["url"] for l in assoc_links if l["kind"] == urls.WHATSAPP][:1]
        if wa and not ev:
            ev = [f"celular publicado: {format_phone(wa)}"]
        add("whatsapp", "Há WhatsApp/celular publicado; o site pode levar direto à conversa.", ev)
    if urls.AGENDAMENTO in kinds:
        add("agendamento", "Há agendamento online em plataforma; o site pode dar acesso direto a ele.",
            [l["url"] for l in assoc_links if l["kind"] == urls.AGENDAMENTO][:2])

    found = {b["chave"] for b in out}
    suggestions = [
        {"chave": k, "titulo": BENEFIT_TITLES[k],
         "justificativa": "Comum no segmento — sem evidência específica desta empresa; avaliar na conversa."}
        for k in sorted(seg_benefits - found) if k in BENEFIT_TITLES
    ]
    return out, suggestions


def best_contact(lead: dict, links: list[dict]) -> dict:
    wa_link = next((l["url"] for l in links if l["kind"] == urls.WHATSAPP
                    and l["association"] in ("fonte", "agregador", "usuario", "evidencia")), None)
    wa = lead.get("whatsapp_norm") or (lead.get("phone_norm") if is_mobile(lead.get("phone_norm")) else None)
    caveat = None
    if lead.get("phone_source") == "cnpj" and not lead.get("whatsapp_norm"):
        caveat = "Telefone vem do cadastro CNPJ e pode ser do contador — confirme antes de usar."
    return {
        "whatsapp": format_phone(wa) if wa else None,
        "whatsapp_digits": wa,
        "whatsapp_link": wa_link,
        "telefone": format_phone(lead.get("phone_norm")) if lead.get("phone_norm") else None,
        "telefone_fonte": lead.get("phone_source"),
        "email": lead.get("email"),
        "email_fonte": lead.get("email_source"),
        "aviso": caveat,
    }


def _verification_component(lead: dict) -> tuple[float, str]:
    eff = st.effective_status(lead)
    if eff not in st.NO_SITE_STATUSES:
        return 0.0, f"{st.SITE_STATUS_LABELS.get(eff, eff)}: não pontua"
    if lead.get("site_status_manual"):
        conf = MANUAL_CONFIDENCE.get(lead.get("site_manual_source") or "outro", 0.8)
        return 35 * conf, f"confirmado manualmente ({st.MANUAL_SOURCES.get(lead.get('site_manual_source'), '')})"
    conf = lead.get("site_confidence_auto") or 0
    return 35 * conf, f"verificação automática, confiança {st.confidence_label(conf)} ({conf:.2f})"


def next_action(lead: dict, in_campaign: bool, exclusion: str | None, contact: dict, message: dict | None) -> str:
    eff = st.effective_status(lead)
    if lead.get("pipeline_status") in ("descartado", "sem_interesse"):
        return "Nenhuma — lead encerrado"
    if eff == st.PENDENTE:
        return "Executar a verificação de site"
    if eff == st.SITE_PROPRIO:
        return "Lista separada (site encontrado) — fora da campanha"
    if eff == st.POSSIVEL_INDISPONIVEL:
        return "Abrir o link do site e confirmar manualmente se está fora do ar"
    if eff == st.INCONCLUSIVA:
        return "Confirmar manualmente com os links de busca e registrar a classificação"
    if lead.get("is_chain"):
        return "Revisar se é unidade de rede/franquia (normalmente usa o site da marca)"
    if not (contact.get("whatsapp") or contact.get("telefone") or contact.get("email")):
        return "Buscar um contato comercial publicado (ex.: bio do Instagram) e colar como material"
    if lead.get("pipeline_status") == "contatado":
        return "Aguardar resposta e registrar o retorno"
    if lead.get("pipeline_status") == "respondeu":
        return "Dar seguimento à conversa (registrar resultado)"
    if message is None:
        return "Gerar mensagem inicial"
    if message["status"] == "bloqueada":
        return "Editar a mensagem: há trechos não permitidos"
    if message["status"] == "rascunho":
        return "Revisar, editar se necessário e aprovar a mensagem"
    if message["status"] == "aprovada":
        return "Enviar manualmente pelo canal indicado e marcar como contatado"
    return "Revisar lead"


def qualify_lead(conn: sqlite3.Connection, lead_id: int, settings: dict) -> dict:
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise ValueError("lead não encontrado")
    facts = get_facts(conn, lead_id)
    links = get_links(conn, lead_id)
    benefits, suggestions = possible_benefits(lead, facts, links)
    contact = best_contact(lead, links)

    v_score, v_note = _verification_component(lead)
    targets = settings.get("segmentos_alvo") or []
    seg = lead.get("segment")
    if targets:
        a_score = 20 if seg in targets else 5
        a_note = "segmento-alvo" if seg in targets else "fora dos segmentos-alvo"
    else:
        a_score = 20 if seg else 10
        a_note = "segmento identificado" if seg else "segmento não identificado"
    if contact["whatsapp"]:
        c_score, c_note = 20.0, "WhatsApp/celular publicado"
    elif contact["telefone"]:
        c_score, c_note = 14.0, "telefone fixo publicado"
    elif contact["email"]:
        c_score, c_note = 8.0, "só e-mail"
    else:
        c_score, c_note = 0.0, "sem contato comercial"
    if contact["aviso"]:
        c_score *= 0.7
        c_note += " (origem: cadastro CNPJ)"
    b_score = min(15, 5 * len(benefits))
    substantive = [f for f in facts if f["kind"] in ("servico", "produto", "forma_contato", "horario", "rede_social", "observacao")]
    sources = {f["source"] for f in facts}
    e_score = min(10, len(substantive) + 2 * max(0, len(sources) - 1))

    eff = st.effective_status(lead)
    exclusion = None
    if eff == st.SITE_PROPRIO:
        exclusion = "Site próprio encontrado — lista separada"
    elif eff not in st.NO_SITE_STATUSES:
        exclusion = "Verificação de site insuficiente: " + st.SITE_STATUS_LABELS.get(eff, eff)
    elif lead.get("is_chain"):
        exclusion = "Possível rede/franquia (" + (lead.get("chain_reason") or "") + ")"
    elif lead.get("pipeline_status") in ("descartado", "sem_interesse"):
        exclusion = "Lead encerrado (" + st.PIPELINE_STATUSES[lead["pipeline_status"]] + ")"
    in_campaign = exclusion is None

    score = round(v_score + a_score + c_score + b_score + e_score, 1) if in_campaign else 0.0
    breakdown = {
        "verificacao": {"pontos": round(v_score, 1), "max": 35, "nota": v_note},
        "aderencia": {"pontos": a_score, "max": 20, "nota": a_note},
        "contato": {"pontos": round(c_score, 1), "max": 20, "nota": c_note},
        "beneficio": {"pontos": b_score, "max": 15, "nota": f"{len(benefits)} aplicação(ões) com evidência"},
        "evidencias": {"pontos": e_score, "max": 10, "nota": f"{len(substantive)} informação(ões), {len(sources)} fonte(s)"},
    }
    msg = conn.execute("SELECT status FROM messages WHERE lead_id=? ORDER BY version DESC LIMIT 1", (lead_id,)).fetchone()
    action = next_action(lead, in_campaign, exclusion, contact, dict(msg) if msg else None)
    summary = business_summary(lead, facts)
    conn.execute(
        "INSERT INTO qualification(lead_id, score, in_campaign, exclusion, breakdown_json, benefits_json, summary,"
        " next_action, computed_at) VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(lead_id) DO UPDATE SET score=excluded.score, in_campaign=excluded.in_campaign,"
        " exclusion=excluded.exclusion, breakdown_json=excluded.breakdown_json, benefits_json=excluded.benefits_json,"
        " summary=excluded.summary, next_action=excluded.next_action, computed_at=excluded.computed_at",
        (lead_id, score, int(in_campaign), exclusion, dumps(breakdown),
         dumps({"com_evidencia": benefits, "sugestoes_segmento": suggestions}), summary, action, now_iso()),
    )
    conn.commit()
    return {"lead_id": lead_id, "score": score, "in_campaign": in_campaign, "exclusion": exclusion,
            "breakdown": breakdown, "benefits": benefits, "suggestions": suggestions, "summary": summary,
            "next_action": action, "contact": contact}
