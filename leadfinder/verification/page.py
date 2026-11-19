"""Leitura de páginas HTML e comparação de evidências com o lead.

Força das evidências (para associar um site à empresa):
- FORTE: telefone, CNPJ ou e-mail do lead aparecem na página;
- MÉDIA: CEP ou logradouro+número do lead aparecem na página;
- FRACA: o nome aparece na página. Nome sozinho NUNCA confirma associação.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

from leadfinder.normalize import (
    address_in_text,
    find_ceps,
    find_cnpjs,
    find_emails,
    find_phones,
    name_in_text,
    norm_text,
    normalize_cep,
    phones_match,
)

PARKED_PATTERNS = [
    "domain is for sale", "this domain is for sale", "buy this domain", "este dominio esta a venda",
    "dominio a venda", "dominio esta disponivel", "domain parking", "parked free", "sedoparking",
    "hugedomains", "godaddy com parked", "this domain may be for sale",
    "dominio registrado no registro br", "pagina em construcao",
    "site em construcao", "site under construction", "website coming soon",
    "index of", "apache2 ubuntu default page", "welcome to nginx",
    "default web site page", "hostgator", "pagina padrao", "account suspended",
    "conta suspensa", "site suspenso", "website expired", "dominio expirado",
]

LOCAL_TERMS = [
    "brasilia", " df ", "distrito federal", "taguatinga", "ceilandia", "aguas claras",
    "guara", "sobradinho", "gama", "samambaia", "planaltina", "asa sul", "asa norte",
    "lago sul", "lago norte", "sudoeste", "noroeste", "cruzeiro", "vicente pires",
    "riacho fundo", "recanto das emas", "santa maria", "paranoa", "sao sebastiao",
    "nucleo bandeirante", "park way", "jardim botanico", "brazlandia", "itapoa",
    "valparaiso", "luziania", "aguas lindas", "cidade ocidental", "novo gama", "formosa",
    "santo antonio do descoberto", "alexania", "cristalina",
]


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            self._href = a.get("href")
            self._link_text = []
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key in ("description", "og:title", "og:description", "og:site_name"):
                self.meta[key] = a.get("content") or ""

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "template") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._link_text).strip()))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)
        if self._href is not None:
            self._link_text.append(data.strip())


@dataclass
class PageInfo:
    url: str
    title: str
    text: str  # texto visível (para enriquecimento)
    text_norm: str
    links: list[tuple[str, str]]  # (url absoluta, texto do link)
    phones: set[str] = field(default_factory=set)
    cnpjs: set[str] = field(default_factory=set)
    ceps: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    parked_reason: str | None = None


def parse_html(html: str, base_url: str) -> PageInfo:
    ex = _Extractor()
    try:
        ex.feed(html)
        ex.close()
    except Exception:  # noqa: BLE001 — HTML quebrado: aproveitamos o que deu
        pass
    title = " ".join(t.strip() for t in ex.title_parts if t.strip())
    text = re.sub(r"\s+", " ", " ".join(ex.text_parts)).strip()
    meta_text = " ".join(ex.meta.values())
    links = []
    hrefs_text = []
    for href, label in ex.links:
        if not href:
            continue
        hrefs_text.append(href)
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append((urljoin(base_url, href), label))
    raw_for_patterns = " ".join([title, meta_text, text, " ".join(hrefs_text)])
    text_norm = norm_text(" ".join([title, meta_text, text]))
    info = PageInfo(
        url=base_url,
        title=title,
        text=(title + " — " + meta_text + " — " + text).strip(" —")[:20000],
        text_norm=text_norm,
        links=links,
        phones=find_phones(raw_for_patterns),
        cnpjs=find_cnpjs(raw_for_patterns),
        ceps=find_ceps(raw_for_patterns),
        emails=find_emails(raw_for_patterns),
    )
    if len(text_norm) < 2500:  # páginas curtas: checa se é domínio estacionado/padrão
        padded = f" {text_norm} "
        for p in PARKED_PATTERNS:
            if f" {p} " in padded:
                info.parked_reason = p
                break
    return info


@dataclass
class Evidence:
    strong: list[str] = field(default_factory=list)
    medium: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    local: bool = False
    distinct_phones: int = 0

    def merge(self, other: "Evidence") -> None:
        for attr in ("strong", "medium", "weak", "contradicting"):
            cur = getattr(self, attr)
            for item in getattr(other, attr):
                if item not in cur:
                    cur.append(item)
        self.local = self.local or other.local
        self.distinct_phones = max(self.distinct_phones, other.distinct_phones)

    @property
    def name_match(self) -> bool:
        return any(w.startswith("nome") for w in self.weak)

    @property
    def confirms(self) -> bool:
        """Associação confirmada: evidência forte, ou média + nome."""
        return bool(self.strong) or (bool(self.medium) and self.name_match)

    def to_dict(self) -> dict:
        return {
            "forte": self.strong,
            "media": self.medium,
            "fraca": self.weak,
            "contraditoria": self.contradicting,
            "menciona_regiao": self.local,
            "telefones_distintos": self.distinct_phones,
        }


def lead_phones(lead: dict) -> list[str]:
    return [p for p in (lead.get("phone_norm"), lead.get("whatsapp_norm")) if p]


def match_evidence(page: PageInfo, lead: dict) -> Evidence:
    ev = Evidence()
    ev.distinct_phones = len({p[-8:] for p in page.phones})
    phones = lead_phones(lead)
    for lp in phones:
        if any(phones_match(lp, pp) for pp in page.phones):
            ev.strong.append(f"telefone {lp}")
    if lead.get("cnpj") and lead["cnpj"] in page.cnpjs:
        ev.strong.append("CNPJ")
    email = (lead.get("email") or "").lower()
    if email and email in page.emails:
        ev.strong.append("e-mail")
    cep = normalize_cep(lead.get("postcode"))
    if cep and cep in page.ceps:
        ev.medium.append(f"CEP {cep}")
    if address_in_text(lead.get("street"), lead.get("housenumber"), page.text_norm):
        ev.medium.append("endereço")
    if name_in_text(lead.get("name"), page.text_norm):
        ev.weak.append("nome na página")
    padded = f" {page.text_norm} "
    city = norm_text(lead.get("city"))
    ev.local = any(t in padded for t in LOCAL_TERMS) or bool(city and f" {city} " in padded)
    # Contradição: a página publica telefones, nenhum é do lead e o nome não aparece.
    if phones and page.phones and not ev.strong and not ev.name_match:
        ev.contradicting.append("telefones da página não conferem e o nome não aparece")
    return ev


def looks_like_directory(page: PageInfo, ev: Evidence) -> bool:
    """Páginas que listam muitas empresas (guias, listas) não são o site da empresa."""
    if ev.distinct_phones > 6:
        return True
    t = page.text_norm
    markers = ["empresas similares", "empresas relacionadas", "outras empresas", "ver no mapa",
               "avaliacoes de clientes", "guia comercial", "lista de empresas", "consulta cnpj",
               "empresas proximas", "anuncie sua empresa", "cadastre sua empresa"]
    hits = sum(1 for m in markers if m in t)
    return hits >= 2


def contact_page_link(page: PageInfo) -> str | None:
    for href, label in page.links:
        lab = norm_text(label)
        h = href.lower()
        if any(k in lab for k in ("contato", "fale conosco", "onde estamos", "localizacao")) or "/contato" in h:
            return href
    return None
