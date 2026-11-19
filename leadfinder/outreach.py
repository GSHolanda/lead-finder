"""Mensagens iniciais para oferecer desenvolvimento de sites.

- Fundamentadas apenas no que foi encontrado (fatos registrados).
- Se a ausência de site não foi confirmada pela própria empresa, a mensagem
  PERGUNTA ("Vocês já têm um site próprio?") em vez de afirmar.
- Sem críticas à presença digital, sem promessas de vendas/Google, sem fingir
  trabalho já feito (projeto, análise, proposta).
- Nada é enviado pela ferramenta: o usuário revisa, aprova e envia manualmente.
"""

from __future__ import annotations

import re
import sqlite3

from leadfinder import statuses as st
from leadfinder.db import dumps, loads, now_iso
from leadfinder.discovery.segments import segment_label
from leadfinder.enrichment import get_facts
from leadfinder.leads import get_lead, get_links
from leadfinder.normalize import norm_text
from leadfinder.qualification import possible_benefits

QUESTION = "Vocês já têm um site próprio?"

# (padrão sobre texto normalizado, motivo)
BLOCK_ALWAYS = [
    (r"\baument\w*\s+(as\s+|suas\s+|sua\s+|o\s+|seu\s+|seus\s+)?(vendas|faturamento|clientes|lucro|receita)",
     "promessa de resultado (vendas/clientes)"),
    (r"\bmais\s+(vendas|clientes|lucro|faturamento)\b", "promessa de resultado (vendas/clientes)"),
    (r"\b(primeira\s+pagina|primeiro\s+lugar|topo|primeiras\s+posicoes)\s+(do|no|da|na)\s+google", "promessa de posição no Google"),
    (r"\b(aparecer|ficar|estar)\s+(em\s+primeiro|no\s+topo)", "promessa de posição no Google"),
    (r"\bgarant\w+", "garantia de resultado"),
    (r"\b(dobrar|triplicar|multiplicar)\b", "promessa de resultado"),
    (r"\bamador\w*", "crítica à empresa"),
    (r"\bdesatualizad\w*|\bultrapassad\w*|\bdefasad\w*", "crítica à presença digital"),
    (r"\b(presenca|perfil|instagram|pagina)\s+(digital\s+)?(fraca|fraco|ruim|pobre|feia|feio)\b", "crítica à presença digital"),
    (r"\bperd\w+\s+(de\s+)?(clientes|vendas|dinheiro|oportunidades)", "presume perda de clientes"),
    (r"\b(falta\s+de|pouco|sem)\s+profissionalismo|\bpouco\s+profissional|\bnao\s+(e|parece|passa)\s+\w*\s*profissional",
     "crítica ao profissionalismo"),
    (r"\b(criei|desenvolvi|preparei|montei|elaborei|fiz|produzi|desenhei|separei|fizemos|criamos|preparamos)\s+"
     r"(\w+\s+){0,2}(um|uma|o|a|esse|essa|este|esta|seu|sua)\s+(\w+\s+){0,2}"
     r"(site|projeto|layout|proposta|analise|prototipo|modelo|maquete|orcamento|diagnostico|rascunho|demo|estudo)\b",
     "afirma trabalho que ainda não foi feito"),
    (r"\b(analisei|avaliei|auditei|estudei)\b", "afirma análise que não foi feita"),
    (r"\bsegue\s+(a\s+|o\s+|em\s+anexo\s+)?(proposta|projeto|layout|analise|orcamento)", "afirma material que não existe"),
    (r"\bfaturamento\b", "presume faturamento"),
    (r"\borcamento\s+de\s+voces|\bverba\b", "presume orçamento da empresa"),
    (r"\b(voces|vcs?|a\s+empresa)\s+(precisam|necessitam|deveriam)\s+(de\s+)?(ter\s+)?(um\s+)?site", "afirma necessidade"),
    (r"\[[^\]]+\]", "há um campo entre colchetes para preencher"),
]

BLOCK_ABSENCE = [
    (r"\b(voces|vcs?|a\s+empresa|o\s+negocio|o\s+estabelecimento)\s+(ainda\s+)?nao\s+(tem|tinha|possui|possuem|conta|contam|tem)\b.{0,30}\bsite",
     "afirma que a empresa não tem site (não confirmado pela empresa)"),
    (r"\bsem\s+(um\s+)?site\b", "afirma ausência de site (não confirmada pela empresa)"),
    (r"\bnao\s+(encontrei|achei|localizei|vi)\s+(o\s+|um\s+|nenhum\s+)?site", "afirma ausência de site (não confirmada pela empresa)"),
    (r"\b(falta|ausencia)\s+de\s+(um\s+)?site", "afirma ausência de site (não confirmada pela empresa)"),
]

QUESTION_PATTERNS = [r"ja\s+(tem|tem|possuem|possui|contam\s+com)\s+(um\s+)?site", r"tem\s+(um\s+)?site\s+proprio"]


def check_guardrails(text: str, absence_confirmed_by_company: bool, facts: list[dict] | None = None) -> dict:
    t = norm_text(text)
    # o colchete é removido pela normalização: checa no texto original
    blocks, warnings = [], []
    for pattern, reason in BLOCK_ALWAYS:
        target = text if pattern.startswith(r"\[") else t
        if re.search(pattern, target, flags=re.I):
            blocks.append(reason)
    if not absence_confirmed_by_company:
        for pattern, reason in BLOCK_ABSENCE:
            if re.search(pattern, t):
                blocks.append(reason)
        if not any(re.search(p, t) for p in QUESTION_PATTERNS):
            warnings.append(f"Recomendado perguntar \"{QUESTION}\" — a ausência de site não foi confirmada pela empresa.")
    if len(text) > 900:
        warnings.append("Mensagem longa (mais de 900 caracteres).")
    fact_text = norm_text(" ".join((f.get("value") or "") + " " + (f.get("evidence") or "") for f in facts or []))
    numbers = set(re.findall(r"\b\d{2,}\b", t))
    unknown = [n for n in numbers if n not in fact_text]
    if unknown:
        warnings.append("Confira os números citados (não constam nas informações coletadas): " + ", ".join(sorted(unknown)))
    return {"bloqueios": sorted(set(blocks)), "avisos": warnings}


def _absence_confirmed(lead: dict) -> bool:
    return bool(lead.get("site_status_manual") in st.NO_SITE_STATUSES and lead.get("site_manual_source") == "empresa")


BENEFIT_PHRASES = {
    "catalogo": "o catálogo de produtos",
    "servicos": "os serviços que vocês oferecem",
    "orcamento": "um pedido de orçamento",
    "portfolio": "fotos dos trabalhos",
    "localizacao": "endereço, mapa e horário",
    "whatsapp": "um botão direto para o WhatsApp",
    "agendamento": "o link de agendamento",
}


def _benefit_phrase(lead: dict, benefits: list[dict]) -> str:
    phrases = []
    for b in benefits:
        p = BENEFIT_PHRASES.get(b["chave"])
        if b["chave"] == "catalogo" and lead.get("segment") in ("alimentacao", "cafe_padaria"):
            p = "o cardápio"
        if p and p not in phrases:
            phrases.append(p)
    phrases = phrases[:3]
    if not phrases:
        return "as informações do negócio e as formas de contato"
    if len(phrases) == 1:
        return phrases[0]
    return ", ".join(phrases[:-1]) + " e " + phrases[-1]


def template_message(lead: dict, benefits: list[dict], settings: dict) -> str:
    seller = settings.get("vendedor_nome") or "[seu nome]"
    company = settings.get("vendedor_empresa")
    city = settings.get("vendedor_cidade") or "Brasília"
    service = settings.get("servico_descricao") or "desenvolvimento de sites"
    seg = segment_label(lead.get("segment")).lower() if lead.get("segment") else "negócios locais"
    place = lead.get("neighborhood") or lead.get("city") or city
    intro = f"Olá! Tudo bem? Meu nome é {seller}" + (f", da {company}" if company else "") + \
            f", e trabalho com {service} aqui em {city}."
    found = f"Encontrei a {lead['name']} pesquisando {seg} em {place}."
    if _absence_confirmed(lead):
        ask = "Como vocês comentaram que ainda não têm um site próprio, pensei em entrar em contato."
    else:
        ask = QUESTION
    why = f"Pergunto porque um site pode reunir {_benefit_phrase(lead, benefits)} em um endereço só."
    close = "Se fizer sentido para vocês, posso explicar como funciona, sem compromisso."
    return "\n\n".join([intro, found + " " + ask, why, close])


SYSTEM_MESSAGE = (
    "Você escreve a primeira mensagem de um profissional de desenvolvimento de sites para uma pequena empresa. "
    "Regras obrigatórias: use somente as informações fornecidas; não invente dados; se a ausência de site não "
    f"foi confirmada pela empresa, faça a pergunta \"{QUESTION}\" e NUNCA afirme que a empresa não tem site; "
    "não critique a presença digital; não prometa vendas, clientes ou posição no Google; não diga que já criou "
    "site, projeto, análise ou proposta; não presuma faturamento, orçamento ou necessidade; tom cordial e direto; "
    "português do Brasil; no máximo 600 caracteres; sem emojis; termine convidando para conversar sem compromisso. "
    "Responda apenas com o texto da mensagem."
)


def _llm_prompt(lead: dict, facts: list[dict], benefits: list[dict], settings: dict) -> str:
    lines = [
        f"Remetente: {settings.get('vendedor_nome') or '[seu nome]'}"
        + (f", empresa {settings['vendedor_empresa']}" if settings.get("vendedor_empresa") else "")
        + f", trabalha com {settings.get('servico_descricao') or 'desenvolvimento de sites'} em {settings.get('vendedor_cidade') or 'Brasília'}.",
        f"Empresa: {lead['name']}",
        f"Como foi encontrada: pesquisa por {segment_label(lead.get('segment')).lower() if lead.get('segment') else 'negócios'} "
        f"em {lead.get('neighborhood') or lead.get('city') or 'Brasília'}.",
        "Ausência de site confirmada pela própria empresa: " + ("sim" if _absence_confirmed(lead) else "NÃO (pergunte)"),
        "Informações verificadas:",
    ]
    for f in facts:
        if f["kind"] in ("servico", "produto", "forma_contato", "horario", "segmento"):
            lines.append(f"- {f['kind']}: {f['value']}")
    lines.append("Possíveis aplicações de um site (com evidência): " +
                 ("; ".join(b["titulo"] for b in benefits) or "nenhuma específica"))
    return "\n".join(lines)


def _next_version(conn: sqlite3.Connection, lead_id: int) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM messages WHERE lead_id=?", (lead_id,)).fetchone()
    return (row["v"] or 0) + 1


def _insert(conn, lead_id, body, generator, report, edited=False) -> dict:
    status = "bloqueada" if report["bloqueios"] else "rascunho"
    version = _next_version(conn, lead_id)
    cur = conn.execute(
        "INSERT INTO messages(lead_id, version, body, generator, guardrail_json, status, edited_by_human, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (lead_id, version, body, generator, dumps(report), status, int(edited), now_iso()),
    )
    conn.commit()
    return get_message(conn, cur.lastrowid)


def generate_message(conn: sqlite3.Connection, lead_id: int, settings: dict, ollama=None) -> dict:
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise ValueError("lead não encontrado")
    facts = get_facts(conn, lead_id)
    benefits, _ = possible_benefits(lead, facts, get_links(conn, lead_id))
    confirmed = _absence_confirmed(lead)
    rejected_llm = None
    if ollama is not None and ollama.available():
        body = ollama.chat_text(SYSTEM_MESSAGE, _llm_prompt(lead, facts, benefits, settings))
        if body:
            body = body.strip().strip('"').strip()
            report = check_guardrails(body, confirmed, facts)
            if not report["bloqueios"]:
                return _insert(conn, lead_id, body, f"ollama:{ollama.model}", report)
            rejected_llm = {"texto": body, "bloqueios": report["bloqueios"]}
    body = template_message(lead, benefits, settings)
    report = check_guardrails(body, confirmed, facts)
    if rejected_llm:
        report["avisos"].append("O texto do modelo local foi descartado por violar regras: "
                                + "; ".join(rejected_llm["bloqueios"]) + ". Usado o modelo padrão.")
    return _insert(conn, lead_id, body, "modelo_padrao", report)


def save_edit(conn: sqlite3.Connection, message_id: int, body: str) -> dict:
    msg = get_message(conn, message_id)
    if msg is None:
        raise ValueError("mensagem não encontrada")
    lead = get_lead(conn, msg["lead_id"])
    report = check_guardrails(body, _absence_confirmed(lead), get_facts(conn, lead["id"]))
    return _insert(conn, lead["id"], body.strip(), "edicao_humana", report, edited=True)


def approve(conn: sqlite3.Connection, message_id: int) -> dict:
    msg = get_message(conn, message_id)
    if msg is None:
        raise ValueError("mensagem não encontrada")
    if msg["guardrails"]["bloqueios"]:
        raise ValueError("A mensagem tem trechos não permitidos: " + "; ".join(msg["guardrails"]["bloqueios"]))
    lead = get_lead(conn, msg["lead_id"])
    if st.effective_status(lead) not in st.NO_SITE_STATUSES:
        raise ValueError("Só é possível aprovar mensagens de empresas sem site próprio localizado após verificação.")
    conn.execute("UPDATE messages SET status='substituida' WHERE lead_id=? AND status='aprovada'", (lead["id"],))
    conn.execute("UPDATE messages SET status='aprovada', approved_at=? WHERE id=?", (now_iso(), message_id))
    if lead["pipeline_status"] in ("novo", "em_revisao"):
        from leadfinder.db import add_history

        conn.execute("UPDATE leads SET pipeline_status='mensagem_aprovada' WHERE id=?", (lead["id"],))
        add_history(conn, lead["id"], "humano", "pipeline_status", lead["pipeline_status"], "mensagem_aprovada",
                    f"mensagem v{msg['version']} aprovada")
    conn.commit()
    return get_message(conn, message_id)


def get_message(conn: sqlite3.Connection, message_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["guardrails"] = loads(d.pop("guardrail_json"), {"bloqueios": [], "avisos": []})
    return d


def latest_message(conn: sqlite3.Connection, lead_id: int) -> dict | None:
    row = conn.execute("SELECT id FROM messages WHERE lead_id=? ORDER BY version DESC LIMIT 1", (lead_id,)).fetchone()
    return get_message(conn, row["id"]) if row else None
