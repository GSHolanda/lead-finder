"""Provedores de busca web para a verificação de site.

Opções gratuitas:
- SearXNG local (metabuscador de código aberto, roda em Docker na sua máquina);
- Brave Search API (tem plano gratuito com limite mensal — confira as
  condições atuais no site do Brave);
- nenhum: a verificação automática fica limitada aos links das fontes e o
  resultado não passa de "inconclusiva" — a interface oferece links de busca
  para confirmação manual.

Não fazemos raspagem de páginas de resultados do Google/Bing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlencode

from leadfinder.net import HttpClient
from leadfinder.normalize import format_phone


@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""
    rank: int = 0


@dataclass
class SearchOutcome:
    provider: str
    query: str
    ok: bool
    results: list[SearchResult] = field(default_factory=list)
    error: str | None = None


class SearchProvider:
    name = "nenhum"
    available = False

    def search(self, query: str) -> SearchOutcome:  # pragma: no cover - interface
        return SearchOutcome(self.name, query, ok=False, error="provedor de busca não configurado")


class NullProvider(SearchProvider):
    pass


class SearxngProvider(SearchProvider):
    name = "searxng"
    available = True

    def __init__(self, base_url: str, client: HttpClient) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client

    def search(self, query: str) -> SearchOutcome:
        params = urlencode({"q": query, "format": "json", "language": "pt-BR", "safesearch": 0})
        res, data = self.client.get_json(f"{self.base_url}/search?{params}")
        if not res.ok or not isinstance(data, dict):
            hint = ""
            if res.status == 403:
                hint = " (habilite 'json' em search.formats no settings.yml do SearXNG)"
            return SearchOutcome(self.name, query, ok=False,
                                 error=f"{res.error_detail or res.error or res.status}{hint}")
        results = [
            SearchResult(url=r.get("url", ""), title=r.get("title", ""), snippet=r.get("content", ""), rank=i + 1)
            for i, r in enumerate(data.get("results", []))
            if r.get("url")
        ]
        unresponsive = data.get("unresponsive_engines") or []
        if not results and unresponsive:
            return SearchOutcome(self.name, query, ok=False,
                                 error=f"motores sem resposta: {', '.join(str(u[0]) for u in unresponsive)}")
        return SearchOutcome(self.name, query, ok=True, results=results[:20])


class BraveProvider(SearchProvider):
    name = "brave"
    available = True
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, client: HttpClient) -> None:
        self.api_key = api_key
        self.client = client

    def search(self, query: str) -> SearchOutcome:
        params = urlencode({"q": query, "country": "BR", "search_lang": "pt-br", "count": 20})
        res, data = self.client.get_json(
            f"{self.endpoint}?{params}", headers={"X-Subscription-Token": self.api_key}
        )
        if not res.ok or not isinstance(data, dict):
            return SearchOutcome(self.name, query, ok=False, error=f"{res.error_detail or res.error or res.status}")
        items = (data.get("web") or {}).get("results", [])
        results = [
            SearchResult(url=r.get("url", ""), title=r.get("title", ""), snippet=r.get("description", ""), rank=i + 1)
            for i, r in enumerate(items)
            if r.get("url")
        ]
        return SearchOutcome(self.name, query, ok=True, results=results)


def make_provider(settings: dict, client: HttpClient) -> SearchProvider:
    p = (settings.get("busca_provedor") or "nenhum").lower()
    if p == "searxng" and settings.get("searxng_url"):
        return SearxngProvider(settings["searxng_url"], client)
    if p == "brave" and settings.get("brave_api_key"):
        return BraveProvider(settings["brave_api_key"], client)
    return NullProvider()


def build_queries(lead: dict, max_queries: int = 3) -> list[tuple[str, str]]:
    """Consultas por nome + cidade/bairro e por telefone (distingue homônimos).

    Retorna lista de (tipo, consulta).
    """
    name = (lead.get("name") or "").strip()
    city = lead.get("city") or ("Brasília" if (lead.get("uf") or "DF") == "DF" else "")
    hood = lead.get("neighborhood") or ""
    queries: list[tuple[str, str]] = []
    if name:
        queries.append(("nome_cidade", f'"{name}" {city}'.strip()))
    if name and hood and hood.lower() not in city.lower():
        queries.append(("nome_bairro", f'"{name}" {hood}'))
    phone = lead.get("phone_norm") or lead.get("whatsapp_norm")
    if phone:
        queries.append(("telefone", f'"{format_phone(phone)}"'))
    if len(queries) < 2 and name:
        queries.append(("nome_site", f"{name} {city} site oficial".strip()))
    return queries[:max_queries]


def manual_search_links(lead: dict) -> list[dict]:
    """Links para o usuário conferir manualmente (abertos por ele, no navegador)."""
    name = lead.get("name") or ""
    city = lead.get("city") or "Brasília"
    hood = lead.get("neighborhood") or ""
    q_name = f'"{name}" {hood or city}'.strip()
    links = [
        {"label": "Google", "url": "https://www.google.com/search?q=" + quote_plus(q_name)},
        {"label": "Bing", "url": "https://www.bing.com/search?q=" + quote_plus(q_name)},
        {"label": "DuckDuckGo", "url": "https://duckduckgo.com/?q=" + quote_plus(q_name)},
        {"label": "Google Maps", "url": "https://www.google.com/maps/search/" + quote_plus(f"{name} {hood} {city}".strip())},
    ]
    phone = lead.get("phone_norm") or lead.get("whatsapp_norm")
    if phone:
        links.append({"label": "Google (telefone)",
                      "url": "https://www.google.com/search?q=" + quote_plus(f'"{format_phone(phone)}"')})
    if lead.get("cnpj"):
        links.append({"label": "Google (CNPJ)", "url": "https://www.google.com/search?q=" + quote_plus(lead["cnpj"])})
    return links
