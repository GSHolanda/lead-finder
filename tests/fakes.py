"""Dublês de rede para os testes (nenhum teste acessa a internet)."""

from __future__ import annotations

from leadfinder.net import FetchResult
from leadfinder.urls import normalize_url
from leadfinder.verification.search import SearchOutcome, SearchProvider, SearchResult


def html(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


class FakeClient:
    """Mapa URL -> resposta. URLs não mapeadas falham com erro de DNS."""

    def __init__(self, pages: dict | None = None, robots_block: set | None = None) -> None:
        self.pages = {normalize_url(k) or k: v for k, v in (pages or {}).items()}
        self.robots_block = {normalize_url(u) or u for u in (robots_block or set())}
        self.requests: list[str] = []

    def get_page(self, url: str) -> FetchResult:
        self.requests.append(url)
        key = normalize_url(url) or url
        if key in self.robots_block:
            return FetchResult(url=url, ok=False, error="robots", error_detail="proibido por robots.txt")
        spec = self.pages.get(key)
        if spec is None:
            return FetchResult(url=url, ok=False, error="dns", error_detail="Name or service not known")
        if isinstance(spec, FetchResult):
            return spec
        if isinstance(spec, tuple):  # (status, html) ou (status, html, final_url)
            status, text = spec[0], spec[1]
            final = spec[2] if len(spec) > 2 else url
            ok = 200 <= status < 300
            return FetchResult(url=url, final_url=final, status=status, ok=ok, text=text if ok else None,
                               error=None if ok else "http", error_detail=None if ok else f"HTTP {status}")
        return FetchResult(url=url, final_url=url, status=200, ok=True, text=spec)

    def get_json(self, url, headers=None):  # pragma: no cover - não usado
        return FetchResult(url=url, ok=False, error="dns"), None

    def post_json(self, url, payload=None, form=None, headers=None, timeout=None):  # pragma: no cover
        return FetchResult(url=url, ok=False, error="dns"), None


class FakeSearch(SearchProvider):
    name = "fake"
    available = True

    def __init__(self, results: dict | None = None, fail: bool = False) -> None:
        # results: {trecho_da_consulta: [(url, titulo, trecho), ...]}
        self.results = results or {}
        self.fail = fail
        self.queries: list[str] = []

    def search(self, query: str) -> SearchOutcome:
        self.queries.append(query)
        if self.fail:
            return SearchOutcome(self.name, query, ok=False, error="tempo esgotado")
        out = []
        for key, items in self.results.items():
            if key in query:
                for i, (u, t, s) in enumerate(items):
                    out.append(SearchResult(url=u, title=t, snippet=s, rank=i + 1))
        return SearchOutcome(self.name, query, ok=True, results=out)


class FakeOllama:
    def __init__(self, response: dict | str | None = None, available: bool = True) -> None:
        self.response = response
        self.available_flag = available
        self.calls: list[dict] = []
        self.model = "fake-model"

    def available(self) -> bool:
        return self.available_flag

    def chat_json(self, system: str, user: str, schema: dict | None = None) -> dict | None:
        self.calls.append({"system": system, "user": user})
        return self.response if isinstance(self.response, dict) else None

    def chat_text(self, system: str, user: str) -> str | None:
        self.calls.append({"system": system, "user": user})
        return self.response if isinstance(self.response, str) else None
