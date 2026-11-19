"""Cliente HTTP mínimo (só biblioteca padrão) com:

- respeito a robots.txt (RFC 9309) para páginas de terceiros;
- intervalo mínimo entre requisições ao mesmo host;
- limite de tamanho de resposta e tempo;
- classificação do tipo de falha (DNS, conexão, tempo, SSL, HTTP).

Nunca tenta contornar bloqueios: 401/403/429, captchas ou robots.txt que
proíbem o acesso são registrados como "bloqueado" e a página não é lida.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from leadfinder import __version__
from leadfinder.db import now_iso

USER_AGENT = f"LeadFinderLocal/{__version__} (verificacao de presenca web; uso local)"


@dataclass
class FetchResult:
    url: str
    final_url: str | None = None
    status: int | None = None
    ok: bool = False
    error: str | None = None  # dns | conexao | tempo | ssl | http | robots | robots_indisponivel | tamanho | outro
    error_detail: str | None = None
    content_type: str | None = None
    text: str | None = None
    fetched_at: str = field(default_factory=now_iso)

    @property
    def blocked(self) -> bool:
        return self.error in ("robots", "robots_indisponivel") or self.status in (401, 403, 429)

    @property
    def unreachable(self) -> bool:
        return self.error in ("dns", "conexao", "tempo", "ssl") or (
            self.status is not None and self.status >= 500
        )

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "ok": self.ok,
            "error": self.error,
            "error_detail": self.error_detail,
            "fetched_at": self.fetched_at,
        }


def _classify_exception(exc: BaseException) -> tuple[str, str]:
    reason = getattr(exc, "reason", exc)
    text = str(reason)
    if isinstance(reason, socket.gaierror) or "Name or service not known" in text or "getaddrinfo" in text or "nodename nor servname" in text:
        return "dns", text
    if isinstance(reason, (ssl.SSLError, ssl.CertificateError)) or "CERTIFICATE" in text.upper() or "SSL" in text:
        return "ssl", text
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
        return "tempo", text
    if isinstance(reason, (ConnectionError, OSError)) or "Tunnel connection failed" in text:
        return "conexao", text
    return "outro", text


def _decode(raw: bytes, content_type: str | None) -> str:
    charset = None
    if content_type:
        m = re.search(r"charset=([\w\-]+)", content_type, re.I)
        if m:
            charset = m.group(1)
    if not charset:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", raw[:4096], re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    for enc in (charset, "utf-8", "latin-1"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


class HttpClient:
    """Cliente usado por descoberta, verificação e enriquecimento."""

    def __init__(
        self,
        timeout: float = 12.0,
        min_interval: float = 1.5,
        max_bytes: int = 1_500_000,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, tuple[RobotFileParser | None, str | None]] = {}
        self._lock = threading.Lock()

    # -- infraestrutura ---------------------------------------------------
    def _wait_turn(self, host: str) -> None:
        if host in ("localhost", "127.0.0.1", "::1"):
            return  # serviços locais (Ollama, SearXNG) não precisam de intervalo
        with self._lock:
            last = self._last_hit.get(host, 0.0)
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.monotonic()

    def _open(self, req: urllib.request.Request, timeout: float | None = None) -> FetchResult:
        url = req.full_url
        host = urlsplit(url).hostname or ""
        self._wait_turn(host)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read(self.max_bytes + 1)
                ctype = resp.headers.get("Content-Type")
                res = FetchResult(
                    url=url,
                    final_url=resp.geturl(),
                    status=resp.status,
                    ok=200 <= resp.status < 300,
                    content_type=ctype,
                )
                if len(raw) > self.max_bytes:
                    raw = raw[: self.max_bytes]
                res.text = _decode(raw, ctype)
                return res
        except urllib.error.HTTPError as e:
            return FetchResult(
                url=url,
                final_url=e.geturl() if hasattr(e, "geturl") else url,
                status=e.code,
                ok=False,
                error="http",
                error_detail=f"HTTP {e.code}",
            )
        except Exception as e:  # noqa: BLE001 — registramos qualquer falha
            kind, detail = _classify_exception(e)
            return FetchResult(url=url, ok=False, error=kind, error_detail=detail[:300])

    # -- robots.txt -------------------------------------------------------
    def robots_allows(self, url: str) -> tuple[bool, str | None, FetchResult | None]:
        """Retorna (permitido, observação, resultado_da_busca_do_robots)."""
        parts = urlsplit(url)
        base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        cached = self._robots.get(base)
        fetch_res = None
        if cached is None:
            robots_url = base + "/robots.txt"
            req = urllib.request.Request(robots_url, headers={"User-Agent": self.user_agent})
            fetch_res = self._open(req)
            if fetch_res.ok and fetch_res.text is not None:
                rp = RobotFileParser()
                rp.parse(fetch_res.text.splitlines())
                cached = (rp, None)
            elif fetch_res.status is not None and 400 <= fetch_res.status < 500:
                cached = (None, "robots.txt ausente (4xx): acesso permitido")
            else:
                # Servidor inacessível ou 5xx: pela RFC 9309, assumir proibido.
                cached = (None, "robots_indisponivel")
            self._robots[base] = cached
        rp, note = cached
        if rp is None:
            if note == "robots_indisponivel":
                return False, note, fetch_res
            return True, note, fetch_res
        allowed = rp.can_fetch(self.user_agent, url)
        return allowed, None if allowed else "proibido por robots.txt", fetch_res

    # -- API pública --------------------------------------------------------
    def get_page(self, url: str) -> FetchResult:
        """Busca uma página de terceiro respeitando robots.txt."""
        allowed, note, robots_res = self.robots_allows(url)
        if not allowed:
            if note == "robots_indisponivel" and robots_res is not None and robots_res.error:
                # Não conseguimos sequer conectar ao servidor.
                return FetchResult(
                    url=url,
                    ok=False,
                    status=robots_res.status,
                    error=robots_res.error,
                    error_detail=f"falha ao conectar (robots.txt): {robots_res.error_detail}",
                )
            if note == "robots_indisponivel":
                return FetchResult(url=url, ok=False, status=robots_res.status if robots_res else None,
                                   error="robots_indisponivel",
                                   error_detail="robots.txt indisponível (5xx): acesso não realizado")
            return FetchResult(url=url, ok=False, error="robots", error_detail=note)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "pt-BR,pt;q=0.9",
            },
        )
        return self._open(req)

    def get_json(self, url: str, headers: dict | None = None) -> tuple[FetchResult, object | None]:
        """Chamada a API (robots.txt não se aplica a APIs documentadas)."""
        h = {"User-Agent": self.user_agent, "Accept": "application/json"}
        h.update(headers or {})
        res = self._open(urllib.request.Request(url, headers=h))
        return res, self._parse_json(res)

    def post_json(self, url: str, payload: object | None = None, form: dict | None = None,
                  headers: dict | None = None, timeout: float | None = None) -> tuple[FetchResult, object | None]:
        h = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if form is not None:
            from urllib.parse import urlencode

            data = urlencode(form).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(payload or {}).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        res = self._open(req, timeout=timeout)
        return res, self._parse_json(res)

    @staticmethod
    def _parse_json(res: FetchResult) -> object | None:
        if not res.ok or not res.text:
            return None
        try:
            return json.loads(res.text)
        except ValueError:
            res.ok = False
            res.error = "outro"
            res.error_detail = "resposta não é JSON"
            return None
