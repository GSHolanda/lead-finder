"""Pesquisa sem site: reúne fatos comprováveis sobre o negócio.

Fontes usadas (nenhuma exige login):
- campos estruturados das fontes de descoberta (OSM, CNPJ, CSV);
- links públicos registrados (presença em redes/plataformas — só o link);
- páginas de agregadores de links da própria empresa, quando o robots.txt
  permite a leitura (guardadas durante a verificação);
- materiais colados pelo usuário (ex.: texto da bio do Instagram, com o link).

Extração com Ollama: cada item precisa vir com um trecho LITERAL do texto de
origem. Itens cujo trecho não está no texto são descartados (anti-invenção).
Quando há pouca presença digital, o resumo diz isso — não completa lacunas.
"""

from __future__ import annotations

import sqlite3

from leadfinder import urls
from leadfinder.db import loads, now_iso
from leadfinder.discovery.segments import CUISINE_LABELS, segment_label
from leadfinder.leads import get_lead, get_links, get_sources
from leadfinder.normalize import format_phone, norm_text

FACT_LABELS = {
    "nome_comercial": "Nome comercial",
    "segmento": "Segmento",
    "servico": "Serviço",
    "produto": "Produto/cardápio",
    "regiao_atendida": "Região atendida",
    "endereco": "Endereço",
    "contato_telefone": "Telefone",
    "contato_whatsapp": "WhatsApp",
    "contato_email": "E-mail",
    "rede_social": "Rede social/plataforma",
    "forma_contato": "Forma de contato/contratação",
    "horario": "Horário",
    "observacao": "Descrição publicada",
}

SOURCE_LABELS = {
    "osm": "OpenStreetMap",
    "cnpj": "Cadastro CNPJ (dados abertos)",
    "csv": "Lista importada",
    "manual": "Usuário",
}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        key: {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"valor": {"type": "string"}, "evidencia": {"type": "string"}},
                "required": ["valor", "evidencia"],
            },
        }
        for key in ("servicos", "produtos", "regiao_atendida", "formas_contato", "horario")
    },
    "required": ["servicos", "produtos", "regiao_atendida", "formas_contato", "horario"],
}

EXTRACTION_KIND = {
    "servicos": "servico",
    "produtos": "produto",
    "regiao_atendida": "regiao_atendida",
    "formas_contato": "forma_contato",
    "horario": "horario",
}

SYSTEM_EXTRACTION = (
    "Você extrai informações de textos públicos de pequenas empresas brasileiras. "
    "Regras obrigatórias: (1) use SOMENTE o que está escrito no texto; (2) para cada item, "
    "'evidencia' deve ser um trecho copiado literalmente do texto, sem alterações; (3) não deduza, "
    "não complete e não invente — se algo não estiver escrito, devolva lista vazia; (4) não avalie "
    "a empresa nem sua presença digital; (5) responda em português, em JSON."
)


def _fact(kind: str, value: str, source: str, source_url: str | None = None, evidence: str | None = None,
          method: str = "estruturado") -> dict:
    return {"kind": kind, "value": value.strip(), "source": source, "source_url": source_url,
            "evidence": evidence, "method": method}


def structured_facts(conn: sqlite3.Connection, lead: dict) -> list[dict]:
    facts: list[dict] = []
    sources = get_sources(conn, lead["id"])
    first = sources[0] if sources else {"source": "manual", "source_url": None}
    src_name = SOURCE_LABELS.get(first["source"], first["source"])
    facts.append(_fact("nome_comercial", lead["name"], src_name, first.get("source_url")))
    if lead.get("segment") or lead.get("category_raw"):
        seg = segment_label(lead.get("segment")) if lead.get("segment") else ""
        cat = lead.get("category_raw") or ""
        facts.append(_fact("segmento", " — ".join(p for p in (seg, cat) if p), src_name, first.get("source_url")))
    addr = ", ".join(p for p in [
        " ".join(p for p in (lead.get("street"), lead.get("housenumber")) if p),
        lead.get("neighborhood"),
        "/".join(p for p in (lead.get("city"), lead.get("uf")) if p),
    ] if p)
    if lead.get("street"):
        facts.append(_fact("endereco", addr, src_name, first.get("source_url")))
    if lead.get("neighborhood") or lead.get("city"):
        facts.append(_fact("regiao_atendida", ", ".join(p for p in (lead.get("neighborhood"), lead.get("city")) if p)
                           + " (localização do estabelecimento)", src_name, first.get("source_url")))
    if lead.get("phone_norm"):
        ps = SOURCE_LABELS.get(lead.get("phone_source") or "", lead.get("phone_source") or src_name)
        note = " — atenção: telefone do cadastro pode ser do contador" if lead.get("phone_source") == "cnpj" else ""
        facts.append(_fact("contato_telefone", format_phone(lead["phone_norm"]) + note, ps))
    if lead.get("whatsapp_norm"):
        facts.append(_fact("contato_whatsapp", format_phone(lead["whatsapp_norm"]), src_name))
    if lead.get("email"):
        es = SOURCE_LABELS.get(lead.get("email_source") or "", lead.get("email_source") or src_name)
        note = " — atenção: e-mail do cadastro pode ser do contador" if lead.get("email_source") == "cnpj" else ""
        facts.append(_fact("contato_email", lead["email"] + note, es))

    for link in get_links(conn, lead["id"]):
        if link["association"] not in ("fonte", "agregador", "usuario", "evidencia"):
            continue
        kind, platform = link["kind"], link["platform"] or urls.KIND_LABELS.get(link["kind"], link["kind"])
        origin = link["origin"]
        if kind in (urls.REDE_SOCIAL, urls.AGREGADOR, urls.MARKETPLACE):
            facts.append(_fact("rede_social", f"{platform}: {link['url']}", origin, link["url"]))
        elif kind == urls.WHATSAPP:
            facts.append(_fact("forma_contato", "Contato por WhatsApp (link publicado)", origin, link["url"]))
        elif kind == urls.DELIVERY:
            facts.append(_fact("forma_contato", f"Pedidos por plataforma de delivery ({link['platform'] or 'delivery'})", origin, link["url"]))
            facts.append(_fact("rede_social", f"{platform}: {link['url']}", origin, link["url"]))
        elif kind == urls.AGENDAMENTO:
            facts.append(_fact("forma_contato", f"Agendamento online ({link['platform'] or 'plataforma'})", origin, link["url"]))

    for src in sources:
        raw = loads(src.get("raw_json"), {}) or {}
        tags = raw.get("tags") if isinstance(raw, dict) else None
        if not tags:
            continue
        label = SOURCE_LABELS.get(src["source"], src["source"])
        url = src.get("source_url")
        for c in (tags.get("cuisine") or "").split(";"):
            c = c.strip()
            if c:
                facts.append(_fact("produto", CUISINE_LABELS.get(c, c.replace("_", " ")), label, url, f"cuisine={c}"))
        if tags.get("opening_hours"):
            facts.append(_fact("horario", tags["opening_hours"], label, url, f"opening_hours={tags['opening_hours']}"))
        if tags.get("delivery") == "yes":
            facts.append(_fact("forma_contato", "Faz entregas (delivery)", label, url, "delivery=yes"))
        if tags.get("takeaway") == "yes":
            facts.append(_fact("forma_contato", "Retirada no local", label, url, "takeaway=yes"))
        if tags.get("healthcare:speciality"):
            for s in tags["healthcare:speciality"].split(";"):
                facts.append(_fact("servico", f"especialidade: {s.strip()}", label, url, "healthcare:speciality"))
        for k, v in tags.items():
            if k.startswith("service:") and v == "yes":
                facts.append(_fact("servico", k.split(":", 1)[1].replace(":", " ").replace("_", " "), label, url, f"{k}=yes"))
        if tags.get("description"):
            facts.append(_fact("observacao", tags["description"], label, url, "description"))
    return facts


def text_sources(conn: sqlite3.Connection, lead: dict) -> list[dict]:
    """Textos públicos disponíveis para extração."""
    out = []
    for src in get_sources(conn, lead["id"]):
        raw = loads(src.get("raw_json"), {}) or {}
        tags = raw.get("tags") if isinstance(raw, dict) else None
        if tags:
            text = " ".join(tags.get(k, "") for k in ("description", "note", "service", "cuisine") if tags.get(k))
            if len(text) >= 20:
                out.append({"source": SOURCE_LABELS.get(src["source"], src["source"]),
                            "source_url": src.get("source_url"), "text": text})
    for link in get_links(conn, lead["id"]):
        if link["kind"] != urls.AGREGADOR or link["association"] not in ("fonte", "usuario", "evidencia"):
            continue
        row = conn.execute("SELECT text FROM page_cache WHERE url=?", (link["url_norm"],)).fetchone()
        if row and row["text"]:
            out.append({"source": f"Página pública ({link['platform'] or 'agregador'})", "source_url": link["url"],
                        "text": row["text"][:6000]})
    for m in conn.execute("SELECT * FROM user_materials WHERE lead_id=? ORDER BY id", (lead["id"],)):
        out.append({"source": "Material do usuário" + (f": {m['title']}" if m["title"] else ""),
                    "source_url": m["source_url"], "text": m["content"][:6000]})
    return out


def evidence_in_text(evidence: str | None, text: str) -> bool:
    ev = norm_text(evidence)
    return len(ev) >= 3 and ev in norm_text(text)


def extract_with_ollama(ollama, src: dict) -> tuple[list[dict], int]:
    """Retorna (fatos válidos, quantidade descartada por evidência inexistente)."""
    data = ollama.chat_json(
        SYSTEM_EXTRACTION,
        "Texto público (fonte: " + src["source"] + "):\n\"\"\"\n" + src["text"] + "\n\"\"\"\n\n"
        "Extraia: servicos, produtos, regiao_atendida, formas_contato (como o cliente contrata ou "
        "pede: WhatsApp, Direct, telefone, delivery, agendamento, orçamento), horario.",
        EXTRACTION_SCHEMA,
    )
    if not data:
        return [], 0
    facts, dropped = [], 0
    for key, kind in EXTRACTION_KIND.items():
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            value, ev = str(item.get("valor") or "").strip(), str(item.get("evidencia") or "").strip()
            if not value:
                continue
            if not evidence_in_text(ev, src["text"]):
                dropped += 1
                continue
            facts.append(_fact(kind, value[:200], src["source"], src.get("source_url"), ev[:300], method="ollama"))
    return facts, dropped


def enrich_lead(conn: sqlite3.Connection, lead_id: int, ollama=None) -> dict:
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise ValueError("lead não encontrado")
    facts = structured_facts(conn, lead)
    notes = []
    dropped_total = 0
    texts = text_sources(conn, lead)
    used_ollama = False
    if texts:
        if ollama is not None and ollama.available():
            used_ollama = True
            for src in texts:
                got, dropped = extract_with_ollama(ollama, src)
                facts.extend(got)
                dropped_total += dropped
        else:
            notes.append("Ollama indisponível: textos públicos não foram analisados (só campos estruturados).")
    if dropped_total:
        notes.append(f"{dropped_total} item(ns) sugerido(s) pelo modelo descartado(s): o trecho citado não existe no texto.")
    rejected = {(r["kind"], r["value_norm"]) for r in conn.execute(
        "SELECT kind, value_norm FROM fact_rejections WHERE lead_id=?", (lead_id,))}
    conn.execute("DELETE FROM facts WHERE lead_id=? AND method IN ('estruturado','ollama')", (lead_id,))
    seen = {(r["kind"], r["value_norm"]) for r in conn.execute(
        "SELECT kind, value_norm FROM facts WHERE lead_id=?", (lead_id,))}
    added = 0
    for f in facts:
        key = (f["kind"], norm_text(f["value"]))
        if key in rejected or key in seen:
            continue
        seen.add(key)
        conn.execute(
            "INSERT INTO facts(lead_id, kind, value, value_norm, source, source_url, evidence, method, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (lead_id, f["kind"], f["value"], key[1], f["source"], f["source_url"], f["evidence"], f["method"], now_iso()),
        )
        added += 1
    conn.commit()
    return {"lead_id": lead_id, "facts": added, "ollama": used_ollama, "textos": len(texts), "notas": notes}


def add_material(conn: sqlite3.Connection, lead_id: int, content: str, title: str | None = None,
                 source_url: str | None = None) -> int:
    if not content or not content.strip():
        raise ValueError("material vazio")
    cur = conn.execute(
        "INSERT INTO user_materials(lead_id, title, content, source_url, created_at) VALUES (?,?,?,?,?)",
        (lead_id, title, content.strip(), source_url, now_iso()),
    )
    if source_url:
        from leadfinder.leads import add_link

        add_link(conn, lead_id, source_url, "material_usuario", "usuario")
    conn.commit()
    return cur.lastrowid


def reject_fact(conn: sqlite3.Connection, fact_id: int) -> None:
    row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
    if not row:
        raise ValueError("fato não encontrado")
    conn.execute("INSERT OR IGNORE INTO fact_rejections(lead_id, kind, value_norm, rejected_at) VALUES (?,?,?,?)",
                 (row["lead_id"], row["kind"], row["value_norm"], now_iso()))
    conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
    conn.commit()


def add_manual_fact(conn: sqlite3.Connection, lead_id: int, kind: str, value: str, source_url: str | None = None) -> None:
    if kind not in FACT_LABELS:
        raise ValueError("tipo de informação inválido")
    conn.execute(
        "INSERT INTO facts(lead_id, kind, value, value_norm, source, source_url, evidence, method, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (lead_id, kind, value.strip(), norm_text(value), "Usuário", source_url, None, "manual", now_iso()),
    )
    conn.commit()


def get_facts(conn: sqlite3.Connection, lead_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM facts WHERE lead_id=? ORDER BY kind, id", (lead_id,))]


def business_summary(lead: dict, facts: list[dict]) -> str:
    """Resumo factual, montado só a partir dos fatos registrados."""
    by = {}
    for f in facts:
        by.setdefault(f["kind"], []).append(f)
    seg = by.get("segmento", [{}])[0].get("value") if by.get("segmento") else None
    place = ", ".join(p for p in (lead.get("neighborhood"), lead.get("city")) if p)
    parts = [lead["name"] + (f" — {seg}" if seg else "") + (f", em {place}" if place else "") + "."]
    offer = [f["value"] for f in by.get("servico", []) + by.get("produto", [])][:6]
    if offer:
        parts.append("Serviços/produtos citados nas fontes: " + "; ".join(offer) + ".")
    forms = sorted({f["value"] for f in by.get("forma_contato", [])})
    if forms:
        parts.append("Formas de contato/contratação publicadas: " + "; ".join(forms) + ".")
    nets = sorted({f["value"].split(":")[0] for f in by.get("rede_social", [])})
    if nets:
        parts.append("Presença registrada: " + ", ".join(nets) + ".")
    if by.get("horario"):
        parts.append("Horário publicado: " + by["horario"][0]["value"] + ".")
    if not offer and not forms and not nets:
        parts.append("Pouca informação pública encontrada além de nome, localização e contato nas fontes de descoberta.")
    return " ".join(parts)
