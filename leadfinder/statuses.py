"""Classificações de existência de site e estados do funil."""

from __future__ import annotations

PENDENTE = "pendente"
SITE_PROPRIO = "site_proprio"  # 1. Site próprio encontrado
SOMENTE_REDES = "somente_redes"  # 2. Somente redes sociais ou plataformas
NAO_LOCALIZADO = "nao_localizado"  # 3. Site próprio não localizado após verificação
INCONCLUSIVA = "inconclusiva"  # 4. Verificação inconclusiva
POSSIVEL_INDISPONIVEL = "possivel_indisponivel"  # 5. Possível site indisponível

SITE_STATUSES = [
    SITE_PROPRIO,
    SOMENTE_REDES,
    NAO_LOCALIZADO,
    INCONCLUSIVA,
    POSSIVEL_INDISPONIVEL,
]

SITE_STATUS_LABELS = {
    PENDENTE: "Verificação pendente",
    SITE_PROPRIO: "Site próprio encontrado",
    SOMENTE_REDES: "Somente redes sociais ou plataformas",
    NAO_LOCALIZADO: "Site próprio não localizado após verificação",
    INCONCLUSIVA: "Verificação inconclusiva",
    POSSIVEL_INDISPONIVEL: "Possível site indisponível",
}

# Classificações que significam "não encontramos site próprio" — entram na
# campanha principal (desde que a verificação tenha sido suficiente).
NO_SITE_STATUSES = {SOMENTE_REDES, NAO_LOCALIZADO}

# Origem de uma correção humana.
MANUAL_SOURCES = {
    "pesquisa_manual": "Pesquisa manual do usuário",
    "empresa": "Confirmado pela própria empresa",
    "piloto": "Revisão da amostra do piloto",
    "outro": "Outro",
}

PIPELINE_STATUSES = {
    "novo": "Novo",
    "em_revisao": "Em revisão",
    "mensagem_aprovada": "Mensagem aprovada",
    "contatado": "Contatado (manualmente)",
    "respondeu": "Respondeu",
    "sem_interesse": "Sem interesse",
    "descartado": "Descartado",
}


def effective_status(lead: dict) -> str:
    """Correção humana sempre prevalece sobre a classificação automática."""
    return lead.get("site_status_manual") or lead.get("site_status_auto") or PENDENTE


def effective_site_url(lead: dict) -> str | None:
    if lead.get("site_status_manual"):
        return lead.get("site_url_manual")
    return lead.get("site_url_auto")


def confidence_label(conf: float | None) -> str:
    if conf is None:
        return "—"
    if conf >= 0.8:
        return "alta"
    if conf >= 0.6:
        return "média"
    return "baixa"
