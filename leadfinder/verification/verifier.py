"""Etapa de verificação de existência de site próprio.

Fluxo (cada passo vira um registro em ``verification_checks``):
1. Links dos registros das fontes (OSM, CNPJ, CSV, usuário): classifica cada
   link; encurtadores são resolvidos; domínios próprios são acessados.
2. Agregadores de links (Linktree etc.) da própria empresa: lidos quando o
   robots.txt permite; links de saída entram na verificação.
3. Domínio do e-mail comercial (se não for provedor gratuito).
4. Busca web por nome + cidade, nome + bairro e telefone (se houver provedor).
   Candidatos só são associados com evidência de conteúdo (telefone, CNPJ,
   e-mail, CEP/endereço). Nome parecido NÃO basta.
5. Domínios prováveis (nome.com.br / nome.com), aceitos só com evidência.

Regras de prudência:
- falha de consulta não vira "sem site": vira "inconclusiva";
- domínio fora do ar vira "possível site indisponível", não "sem site";
- correções humanas nunca são sobrescritas.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from leadfinder import statuses as st
from leadfinder import urls
from leadfinder.db import add_history, dumps, now_iso
from leadfinder.leads import add_link, get_lead, get_links
from leadfinder.net import FetchResult, HttpClient
from leadfinder.normalize import find_phones, name_in_text, norm_text, phones_match, slugify
from leadfinder.verification.page import (
    Evidence,
    PageInfo,
    contact_page_link,
    lead_phones,
    looks_like_directory,
    match_evidence,
    parse_html,
)
from leadfinder.verification.search import NullProvider, SearchProvider, build_queries

# Associações em que a ligação site↔empresa vem da própria fonte/empresa
STRONG_ASSOC = {"fonte", "agregador", "usuario"}

ASSET_HOST_HINTS = ("googleapis", "gstatic", "cloudfront", "cdn", "fonts.", "static.", "assets.",
                    "sentry", "googletagmanager", "google-analytics", "doubleclick", "jsdelivr",
                    "unpkg", "cloudflare", "amazonaws", "w3.org", "schema.org", "gravatar")


@dataclass
class SiteEval:
    url: str
    origin: str
    association: str
    status: str = "pendente"
    final_url: str | None = None
    kind: str | None = None
    evidence: Evidence | None = None
    detail: str = ""
    title: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "origem": self.origin,
            "associacao": self.association,
            "resultado": self.status,
            "url_final": self.final_url,
            "tipo": self.kind,
            "evidencias": self.evidence.to_dict() if self.evidence else None,
            "detalhe": self.detail,
        }


@dataclass
class VerifyContext:
    client: HttpClient
    provider: SearchProvider = field(default_factory=NullProvider)
    settings: dict = field(default_factory=dict)


class _Log:
    def __init__(self, conn: sqlite3.Connection, run_id: int, lead_id: int) -> None:
        self.conn, self.run_id, self.lead_id = conn, run_id, lead_id
        self.count = 0

    def add(self, check_type: str, outcome: str, *, source: str | None = None, query: str | None = None,
            url: str | None = None, detail: str | None = None, result: object | None = None,
            performed_at: str | None = None) -> None:
        self.count += 1
        self.conn.execute(
            "INSERT INTO verification_checks(run_id, lead_id, check_type, source, query, url, performed_at,"
            " outcome, detail, result_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, self.lead_id, check_type, source, query, url, performed_at or now_iso(), outcome,
             detail, dumps(result) if result is not None else None),
        )

    def fetch(self, res: FetchResult, purpose: str, source: str | None = None) -> None:
        if res.ok:
            outcome = "ok"
        elif res.error in ("robots", "robots_indisponivel") or res.status in (401, 403, 429):
            outcome = "bloqueado"
        else:
            outcome = "falha"
        self.add("acesso_pagina", outcome, source=source, url=res.url,
                 detail=f"{purpose}: " + (f"HTTP {res.status}" if res.status else "") +
                 (f" {res.error_detail}" if res.error_detail else ""),
                 result=res.to_dict(), performed_at=res.fetched_at)


def _homepage(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme or "https", p.netloc, "/", "", ""))


def _is_homepage(url: str) -> bool:
    return (urlsplit(url).path or "/") in ("", "/")


def _http_variant(url: str) -> str | None:
    p = urlsplit(url)
    if p.scheme == "https":
        return urlunsplit(("http", p.netloc, p.path, p.query, ""))
    return None


def _to_https(url: str) -> str:
    n = urls.normalize_url(url)
    return n or url


class Verifier:
    def __init__(self, conn: sqlite3.Connection, ctx: VerifyContext) -> None:
        self.conn = conn
        self.ctx = ctx
        self.settings = ctx.settings or {}

    # ------------------------------------------------------------------
    def _fetch(self, log: _Log, url: str, purpose: str, source: str | None = None) -> FetchResult:
        res = self.ctx.client.get_page(url)
        log.fetch(res, purpose, source)
        if res.error == "ssl":
            alt = _http_variant(url)
            if alt:
                res2 = self.ctx.client.get_page(alt)
                log.fetch(res2, purpose + " (sem HTTPS, após erro de certificado)", source)
                if res2.ok:
                    return res2
        return res

    def _cache_page(self, page: PageInfo, final_url: str, key: str | None = None) -> None:
        for k in {urls.normalize_url(page.url) or page.url, urls.normalize_url(key) if key else None} - {None}:
            self.conn.execute(
                "INSERT INTO page_cache(url, fetched_at, final_url, title, text) VALUES (?,?,?,?,?)"
                " ON CONFLICT(url) DO UPDATE SET fetched_at=excluded.fetched_at, final_url=excluded.final_url,"
                " title=excluded.title, text=excluded.text",
                (k, now_iso(), final_url, page.title, page.text),
            )

    # ------------------------------------------------------------------
    def evaluate_site(self, log: _Log, lead: dict, ev: SiteEval, snippet: str = "") -> SiteEval:
        """Acessa um candidato a site e decide se é da empresa."""
        res = self._fetch(log, ev.url, "candidato a site", ev.origin)
        if not res.ok and res.status == 404 and not _is_homepage(ev.url):
            res = self._fetch(log, _homepage(ev.url), "página inicial", ev.origin)
        ev.final_url = res.final_url
        if res.error == "robots" or res.status in (401, 403, 429):
            ev.status = "bloqueado"
            ev.detail = "o site respondeu, mas a leitura não é permitida (robots.txt/bloqueio) — não contornado"
            ev.evidence = self._snippet_evidence(lead, snippet)
            return self._maybe_confirm_by_snippet(ev)
        if res.unreachable or res.error == "robots_indisponivel":
            ev.status = "indisponivel"
            ev.detail = f"sem resposta válida ({res.error or ''} {res.status or ''} {res.error_detail or ''})".strip()
            ev.evidence = self._snippet_evidence(lead, snippet)
            return ev
        if not res.ok or not res.text:
            ev.status = "indisponivel" if _is_homepage(res.url) else "erro"
            ev.detail = f"resposta HTTP {res.status or '?'} {res.error_detail or ''}".strip()
            ev.evidence = self._snippet_evidence(lead, snippet)
            return ev

        final_cls = urls.classify_url(res.final_url or ev.url)
        ev.kind = final_cls.kind
        if not final_cls.is_site:
            ev.status = "redireciona"
            ev.detail = f"redireciona para {urls.KIND_LABELS.get(final_cls.kind, final_cls.kind)}"
            return ev
        page = parse_html(res.text, res.final_url or ev.url)
        ev.title = page.title
        if page.parked_reason:
            ev.status = "estacionado"
            ev.detail = f"página padrão/estacionada ou em construção (\"{page.parked_reason}\")"
            return ev
        evidence = match_evidence(page, lead)
        evidence.merge(self._snippet_evidence(lead, snippet))
        pages = [page]
        if not evidence.confirms:
            extra_urls = []
            if not _is_homepage(res.final_url or ev.url):
                extra_urls.append(_homepage(res.final_url or ev.url))
            contact = contact_page_link(page)
            if contact:
                extra_urls.append(contact)
            for extra in extra_urls[:2]:
                if evidence.confirms:
                    break
                r2 = self._fetch(log, extra, "página complementar (início/contato)", ev.origin)
                if r2.ok and r2.text and urls.classify_url(r2.final_url or extra).is_site:
                    p2 = parse_html(r2.text, r2.final_url or extra)
                    pages.append(p2)
                    evidence.merge(match_evidence(p2, lead))
                    if not contact:
                        c2 = contact_page_link(p2)
                        if c2 and c2 not in extra_urls:
                            extra_urls.append(c2)
        ev.evidence = evidence
        if any(looks_like_directory(p, evidence) for p in pages):
            ev.status = "diretorio"
            ev.detail = "página parece listar várias empresas (guia/diretório) — não é site próprio"
            return ev
        for p in pages:
            self._cache_page(p, res.final_url or ev.url)
        if evidence.confirms:
            ev.status = "confirmado"
            ev.detail = "conteúdo confere com o lead: " + ", ".join(evidence.strong + evidence.medium + evidence.weak)
            return ev
        if ev.association in STRONG_ASSOC or ev.association == "email":
            if evidence.contradicting:
                ev.status = "nao_corresponde"
                ev.detail = "link veio da fonte, mas o conteúdo parece ser de outra empresa: " + "; ".join(evidence.contradicting)
            else:
                ev.status = "existe"
                ev.detail = "site acessível; associação pela fonte (" + ev.origin + ")" + (
                    "; nome aparece na página" if evidence.name_match else "; sem dados de contato para conferir")
            return ev
        if ev.association == "email_cnpj":
            if evidence.name_match and not evidence.contradicting:
                ev.status = "existe"
                ev.detail = "domínio do e-mail do cadastro CNPJ e o nome da empresa aparece no site"
            else:
                ev.status = "sem_relacao"
                ev.detail = "domínio do e-mail do cadastro CNPJ (pode ser do contador); site não menciona a empresa"
            return ev
        if evidence.name_match:
            if evidence.local:
                ev.status = "so_nome"
                ev.detail = "nome confere e há menção à região, mas sem telefone/CNPJ/endereço — não confirmado"
            else:
                ev.status = "homonimo"
                ev.detail = "nome parecido, sem menção à região e sem dados de contato — provável homônimo"
            return ev
        ev.status = "sem_relacao"
        ev.detail = "conteúdo não menciona a empresa"
        return ev

    @staticmethod
    def _snippet_evidence(lead: dict, snippet: str) -> Evidence:
        ev = Evidence()
        if not snippet:
            return ev
        found = find_phones(snippet)
        for lp in lead_phones(lead):
            if any(phones_match(lp, p) for p in found):
                ev.medium.append("telefone no trecho do resultado de busca")
        if name_in_text(lead.get("name"), norm_text(snippet)):
            ev.weak.append("nome no título/trecho da busca")
        return ev

    @staticmethod
    def _maybe_confirm_by_snippet(ev: SiteEval) -> SiteEval:
        if ev.evidence and ev.evidence.confirms and ev.association not in STRONG_ASSOC:
            ev.status = "confirmado_trecho"
            ev.detail += "; telefone e nome conferem no resultado de busca"
        return ev

    # ------------------------------------------------------------------
    def _resolve_shortener(self, log: _Log, lead_id: int, link: dict) -> dict | None:
        res = self._fetch(log, link["url"], "resolver link encurtado", link["origin"])
        if res.final_url and res.final_url != link["url"]:
            cls = add_link(self.conn, lead_id, res.final_url, link["origin"] + "→encurtador", link["association"])
            if cls:
                return {"url": res.final_url, "kind": cls.kind, "platform": cls.platform,
                        "origin": link["origin"] + "→encurtador", "association": link["association"]}
        return None

    def _process_aggregator(self, log: _Log, lead: dict, link: dict) -> tuple[bool, list[dict]]:
        res = self._fetch(log, link["url"], "agregador de links da empresa", link["origin"])
        if not res.ok or not res.text:
            return False, []
        page = parse_html(res.text, res.final_url or link["url"])
        self._cache_page(page, res.final_url or link["url"], key=link["url"])
        found = {u for u, _ in page.links}
        found |= set(re.findall(r'"url"\s*:\s*"(https?:[^"\s]+)"', res.text.replace("\\/", "/")))
        own_host = urls.registrable_domain(urls.host_of(link["url"]))
        out = []
        for u in sorted(found):
            host = urls.host_of(u) or ""
            if not host or urls.registrable_domain(host) == own_host or any(h in host for h in ASSET_HOST_HINTS):
                continue
            cls = add_link(self.conn, lead["id"], u, f"agregador:{link.get('platform') or own_host}", "agregador")
            if cls and cls.kind not in (urls.INVALIDO, urls.NAO_RELACIONADO):
                out.append({"url": u, "kind": cls.kind, "platform": cls.platform,
                            "origin": f"agregador:{own_host}", "association": "agregador"})
        log.add("agregador", "ok", source=link["origin"], url=link["url"],
                detail=f"{len(out)} links de saída encontrados",
                result=[{"url": o["url"], "tipo": o["kind"]} for o in out])
        return True, out

    # ------------------------------------------------------------------
    def verify(self, lead_id: int) -> dict:
        lead = get_lead(self.conn, lead_id)
        if lead is None:
            raise ValueError(f"lead {lead_id} não encontrado")
        started = now_iso()
        run_id = self.conn.execute(
            "INSERT INTO verification_runs(lead_id, started_at) VALUES (?, ?)", (lead_id, started)
        ).lastrowid
        log = _Log(self.conn, run_id, lead_id)
        evals: list[SiteEval] = []
        seen_domains: set[str] = set()
        aggregator_checked = False

        # 1) Links dos registros
        links = [l for l in get_links(self.conn, lead_id) if l["association"] in STRONG_ASSOC]
        queue = list(links)
        processed: set[str] = set()
        while queue:
            link = queue.pop(0)
            key = urls.normalize_url(link["url"]) or link["url"]
            if key in processed:
                continue
            processed.add(key)
            kind = link["kind"]
            log.add("link_registro", "classificado", source=link["origin"], url=link["url"],
                    detail=f"{urls.KIND_LABELS.get(kind, kind)}" + (f" ({link['platform']})" if link.get("platform") else ""),
                    result={"tipo": kind, "associacao": link["association"]})
            if kind == urls.ENCURTADOR:
                resolved = self._resolve_shortener(log, lead_id, link)
                if resolved:
                    queue.append(resolved)
            elif kind in urls.SITE_KINDS:
                domain = urls.registrable_domain(urls.host_of(link["url"]))
                if kind == urls.PROPRIO and domain in seen_domains:
                    continue
                seen_domains.add(domain)
                ev = SiteEval(url=_to_https(link["url"]), origin=link["origin"], association=link["association"])
                evals.append(self.evaluate_site(log, lead, ev))
                if ev.status == "redireciona" and ev.final_url:
                    cls = add_link(self.conn, lead_id, ev.final_url, link["origin"] + "→redirecionamento", link["association"])
                    if cls and cls.kind == urls.AGREGADOR:
                        queue.append({"url": ev.final_url, "kind": cls.kind, "platform": cls.platform,
                                      "origin": link["origin"] + "→redirecionamento", "association": link["association"]})
            elif kind == urls.AGREGADOR:
                ok, outs = self._process_aggregator(log, lead, link)
                aggregator_checked = aggregator_checked or ok
                queue.extend(outs)
            elif kind in (urls.REDE_SOCIAL, urls.WHATSAPP, urls.DELIVERY, urls.AGENDAMENTO, urls.MARKETPLACE):
                log.add("perfil_publico", "nao_consultado", source=link["origin"], url=link["url"],
                        detail="perfil registrado como presença; não é lido automaticamente "
                               "(plataformas exigem login/proíbem robôs) — abra manualmente se quiser conferir")

        # 2) Domínio do e-mail
        domain = urls.email_domain(lead.get("email"))
        if domain and urls.registrable_domain(domain) not in seen_domains:
            seen_domains.add(urls.registrable_domain(domain))
            assoc = "email_cnpj" if lead.get("email_source") == "cnpj" else "email"
            ev = SiteEval(url=f"https://{domain}", origin=f"e-mail ({lead.get('email_source') or 'fonte'})", association=assoc)
            evals.append(self.evaluate_site(log, lead, ev))

        # 3) Busca web
        provider = self.ctx.provider
        search_ok = search_fail = 0
        phone_query_ok = False
        candidates: dict[str, dict] = {}
        possible_profiles: list[dict] = []
        if not provider.available:
            log.add("busca", "nao_configurado", source="nenhum",
                    detail="nenhum provedor de busca configurado — a ausência de site não pode ser concluída")
        else:
            for qtype, query in build_queries(lead, max_queries=3):
                out = provider.search(query)
                if not out.ok:
                    search_fail += 1
                    log.add("busca", "falha", source=provider.name, query=query, detail=out.error)
                    continue
                search_ok += 1
                phone_query_ok = phone_query_ok or qtype == "telefone"
                log.add("busca", "ok", source=provider.name, query=query,
                        detail=f"{len(out.results)} resultados",
                        result=[{"url": r.url, "titulo": r.title[:120]} for r in out.results[:10]])
                for r in out.results:
                    cls = urls.classify_url(r.url)
                    text = f"{r.title} {r.snippet}"
                    if cls.is_site:
                        d = cls.domain if cls.kind == urls.PROPRIO else (cls.host or cls.domain)
                        if d in seen_domains:
                            continue
                        c = candidates.setdefault(d, {"url": r.url, "hits": 0, "best_rank": r.rank, "text": "", "queries": []})
                        c["hits"] += 1
                        c["best_rank"] = min(c["best_rank"], r.rank)
                        c["text"] += " " + text
                        c["queries"].append(qtype)
                    elif cls.kind in urls.PRESENCE_KINDS:
                        sev = self._snippet_evidence(lead, text)
                        if sev.medium:
                            add_link(self.conn, lead_id, r.url, f"busca:{qtype}", "evidencia")
                        elif sev.weak:
                            add_link(self.conn, lead_id, r.url, f"busca:{qtype}", "nao_confirmado")
                            possible_profiles.append({"url": r.url, "titulo": r.title})
            ranked = sorted(candidates.items(), key=lambda kv: (-kv[1]["hits"], kv[1]["best_rank"]))
            for d, c in ranked[: int(self.settings.get("busca_max_candidatos", 5))]:
                seen_domains.add(d)
                ev = SiteEval(url=c["url"], origin="busca:" + ",".join(sorted(set(c["queries"]))), association="busca")
                ev = self.evaluate_site(log, lead, ev, snippet=c["text"])
                if ev.status in ("indisponivel", "erro", "bloqueado") and name_in_text(lead.get("name"), norm_text(c["text"])):
                    ev.detail += "; o resultado de busca cita o nome — não foi possível conferir"
                    ev.status = "nao_conferido"
                evals.append(ev)

        # 4) Domínios prováveis (só com evidência de conteúdo)
        domain_guess_done = False
        confirmed_so_far = any(e.status in ("confirmado", "existe") for e in evals)
        if self.settings.get("adivinhar_dominio", True) and not confirmed_so_far:
            slug = slugify(lead.get("name"))
            if 4 <= len(slug) <= 40:
                domain_guess_done = True
                for guess in (f"{slug}.com.br", f"{slug}.com"):
                    if guess in seen_domains:
                        continue
                    seen_domains.add(guess)
                    ev = SiteEval(url=f"https://{guess}", origin="dominio_provavel", association="dominio")
                    ev = self.evaluate_site(log, lead, ev)
                    if ev.status in ("indisponivel", "erro", "bloqueado", "sem_relacao", "homonimo", "redireciona", "estacionado"):
                        log.add("dominio_provavel", "descartado", url=ev.url, detail=ev.detail or ev.status)
                        continue
                    evals.append(ev)

        # 5) Decisão
        decision = self._decide(lead, evals, search_ok, search_fail, phone_query_ok, aggregator_checked,
                                domain_guess_done, provider)
        for e in evals:
            norm = urls.normalize_url(e.url)
            if e.status in ("confirmado", "confirmado_trecho") and e.association not in STRONG_ASSOC:
                add_link(self.conn, lead_id, e.final_url or e.url, e.origin, "evidencia")
            if e.status in ("so_nome", "nao_conferido") and e.association in ("busca", "dominio"):
                add_link(self.conn, lead_id, e.url, e.origin, "nao_confirmado")
            if norm:
                self.conn.execute("UPDATE links SET last_result=? WHERE lead_id=? AND url_norm=?",
                                  (e.status, lead_id, norm))
        summary = {
            "provedor_busca": provider.name,
            "consultas_ok": search_ok,
            "consultas_falha": search_fail,
            "consulta_telefone": phone_query_ok,
            "agregador_lido": aggregator_checked,
            "dominio_provavel_testado": domain_guess_done,
            "avaliacoes": [e.to_dict() for e in evals],
            "perfis_nao_confirmados": possible_profiles,
            "presenca": decision["presence"],
            "checagens": log.count,
        }
        self.conn.execute(
            "UPDATE verification_runs SET finished_at=?, result_status=?, confidence=?, reason=?, summary_json=? WHERE id=?",
            (now_iso(), decision["status"], decision["confidence"], decision["reason"], dumps(summary), run_id),
        )
        self._apply_auto_result(lead, run_id, decision)
        self.conn.commit()
        return {"lead_id": lead_id, "run_id": run_id, **decision, "summary": summary}

    # ------------------------------------------------------------------
    def _presence(self, lead_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT url, kind, platform, origin FROM links WHERE lead_id=? AND association IN"
            " ('fonte','agregador','usuario','evidencia')", (lead_id,)
        ).fetchall()
        return [dict(r) for r in rows if r["kind"] in urls.PRESENCE_KINDS]

    def _decide(self, lead: dict, evals: list[SiteEval], search_ok: int, search_fail: int,
                phone_query_ok: bool, aggregator_checked: bool, domain_guess_done: bool,
                provider: SearchProvider) -> dict:
        presence = self._presence(lead["id"])
        strong_assoc = STRONG_ASSOC | {"email"}

        confirmed = [e for e in evals if e.status == "confirmado"]
        if confirmed:
            best = max(confirmed, key=lambda e: len(e.evidence.strong) if e.evidence else 0)
            conf = 0.95 if best.evidence and best.evidence.strong else 0.85
            return self._result(st.SITE_PROPRIO, conf, best.final_url or best.url,
                                f"Site próprio encontrado ({best.origin}): {best.detail}", presence)
        exists = [e for e in evals if e.status == "existe"]
        if exists:
            best = exists[0]
            return self._result(st.SITE_PROPRIO, 0.7, best.final_url or best.url,
                                f"Site próprio encontrado ({best.origin}): {best.detail}", presence)
        blocked_assoc = [e for e in evals if e.status == "bloqueado" and e.association in strong_assoc]
        by_snippet = [e for e in evals if e.status == "confirmado_trecho"]
        if blocked_assoc or by_snippet:
            best = (blocked_assoc or by_snippet)[0]
            return self._result(st.SITE_PROPRIO, 0.6, best.final_url or best.url,
                                f"Site responde, leitura não permitida ({best.origin}): {best.detail}", presence)

        down = [e for e in evals if e.status in ("indisponivel", "estacionado") and e.association in STRONG_ASSOC]
        if down:
            best = down[0]
            return self._result(st.POSSIVEL_INDISPONIVEL, 0.6, best.url,
                                f"Link de site da fonte ({best.origin}) sem funcionamento normal: {best.detail}. "
                                "Domínio fora do ar não significa ausência de site — confirmar manualmente.", presence)

        unresolved = [
            e for e in evals
            if e.status in ("so_nome", "nao_corresponde", "nao_conferido", "erro")
            or (e.association in ("email", "email_cnpj") and e.status in ("indisponivel", "estacionado", "bloqueado"))
            or (e.association in strong_assoc and e.status == "erro")
        ]
        if unresolved:
            items = "; ".join(f"{e.url} — {e.detail}" for e in unresolved[:3])
            return self._result(st.INCONCLUSIVA, 0.3, None,
                                f"Há candidato(s) que não puderam ser confirmados nem descartados: {items}", presence)

        min_q = int(self.settings.get("busca_min_consultas", 2))
        if not provider.available:
            return self._result(st.INCONCLUSIVA, 0.2, None,
                                "Busca web não configurada: só os links das fontes foram verificados. "
                                "Use os links de busca manual para confirmar.", presence)
        if search_ok < min_q:
            return self._result(st.INCONCLUSIVA, 0.25, None,
                                f"Busca insuficiente: {search_ok} consulta(s) concluída(s), {search_fail} falha(s) "
                                f"(mínimo {min_q}). Falha de consulta não significa ausência de site.", presence)

        conf = 0.35 + 0.15 * min(search_ok, 3)
        conf += 0.05 if phone_query_ok else 0
        conf += 0.05 if aggregator_checked else 0
        conf += 0.05 if domain_guess_done else 0
        conf = round(min(conf, 0.9), 2)
        discarded = [e for e in evals if e.status in ("homonimo", "sem_relacao", "diretorio", "redireciona")]
        base = (f"{search_ok} consulta(s) de busca ({provider.name})"
                + (", incluindo telefone" if phone_query_ok else "")
                + " sem site próprio associado à empresa")
        if discarded:
            base += f"; {len(discarded)} candidato(s) descartado(s) (homônimo/sem relação/diretório)"
        if presence:
            plats = sorted({p["platform"] or urls.KIND_LABELS.get(p["kind"], p["kind"]) for p in presence})
            return self._result(st.SOMENTE_REDES, conf, None, base + ". Presença encontrada: " + ", ".join(plats) + ".", presence)
        return self._result(st.NAO_LOCALIZADO, conf, None, base + ". Nenhuma rede social/plataforma associada nas fontes.", presence)

    @staticmethod
    def _result(status: str, conf: float, url: str | None, reason: str, presence: list[dict]) -> dict:
        return {"status": status, "confidence": conf, "url": url, "reason": reason, "presence": presence}

    def _apply_auto_result(self, lead: dict, run_id: int, decision: dict) -> None:
        old = lead.get("site_status_auto")
        self.conn.execute(
            "UPDATE leads SET site_status_auto=?, site_confidence_auto=?, site_url_auto=?, site_reason_auto=?,"
            " site_checked_at=?, last_run_id=?, updated_at=? WHERE id=?",
            (decision["status"], decision["confidence"], decision["url"], decision["reason"], now_iso(), run_id,
             now_iso(), lead["id"]),
        )
        if old != decision["status"]:
            note = f"verificação #{run_id}"
            manual = lead.get("site_status_manual")
            if manual and manual != decision["status"]:
                note += (f"; DIVERGÊNCIA: correção humana ({st.SITE_STATUS_LABELS.get(manual, manual)}) "
                         "foi preservada e continua valendo")
            add_history(self.conn, lead["id"], "auto", "site_status_auto", old, decision["status"], note)


def verify_lead(conn: sqlite3.Connection, lead_id: int, ctx: VerifyContext) -> dict:
    return Verifier(conn, ctx).verify(lead_id)
