"""Classificação de links: site próprio x redes sociais, agregadores, delivery,
WhatsApp, diretórios etc.

A classificação olha apenas o domínio/caminho. Ela NÃO diz que um site
pertence a uma empresa — isso é papel da verificação (``verification``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tipos de link
PROPRIO = "proprio"  # domínio próprio (candidato a site próprio)
SITE_PLATAFORMA = "site_plataforma"  # site em subdomínio de construtor (Wix, Google Sites...)
REDE_SOCIAL = "rede_social"
WHATSAPP = "whatsapp"
AGREGADOR = "agregador"  # link na bio (Linktree etc.)
DELIVERY = "delivery"
AGENDAMENTO = "agendamento"
MARKETPLACE = "marketplace"
DIRETORIO = "diretorio"  # guias, mapas, consultas de CNPJ, avaliações
ENCURTADOR = "encurtador"
NAO_RELACIONADO = "nao_relacionado"  # notícias, governo, enciclopédias
INVALIDO = "invalido"

SITE_KINDS = {PROPRIO, SITE_PLATAFORMA}
# Presença que conta como "redes sociais ou plataformas" quando a associação
# com a empresa vem da fonte (registro, link na bio, material do usuário).
PRESENCE_KINDS = {REDE_SOCIAL, WHATSAPP, AGREGADOR, DELIVERY, AGENDAMENTO, MARKETPLACE}

KIND_LABELS = {
    PROPRIO: "Domínio próprio",
    SITE_PLATAFORMA: "Site em plataforma (subdomínio)",
    REDE_SOCIAL: "Rede social",
    WHATSAPP: "WhatsApp",
    AGREGADOR: "Agregador de links",
    DELIVERY: "Delivery/cardápio",
    AGENDAMENTO: "Agendamento",
    MARKETPLACE: "Marketplace",
    DIRETORIO: "Diretório/mapa",
    ENCURTADOR: "Link encurtado",
    NAO_RELACIONADO: "Não relacionado",
    INVALIDO: "Inválido",
}

# domínio registrável -> (tipo, plataforma)
_DOMAINS: dict[str, tuple[str, str]] = {}


def _reg(kind: str, platform: str, *domains: str) -> None:
    for d in domains:
        _DOMAINS[d] = (kind, platform)


_reg(REDE_SOCIAL, "Instagram", "instagram.com", "instagr.am")
_reg(REDE_SOCIAL, "Facebook", "facebook.com", "fb.com", "fb.me", "fb.watch")
_reg(REDE_SOCIAL, "TikTok", "tiktok.com")
_reg(REDE_SOCIAL, "X/Twitter", "twitter.com", "x.com")
_reg(REDE_SOCIAL, "YouTube", "youtube.com", "youtu.be")
_reg(REDE_SOCIAL, "LinkedIn", "linkedin.com")
_reg(REDE_SOCIAL, "Pinterest", "pinterest.com", "pin.it")
_reg(REDE_SOCIAL, "Threads", "threads.net", "threads.com")
_reg(REDE_SOCIAL, "Kwai", "kwai.com")
_reg(REDE_SOCIAL, "Telegram", "t.me", "telegram.me")
_reg(WHATSAPP, "WhatsApp", "wa.me", "whatsapp.com", "wa.link", "whats.link")
_reg(
    AGREGADOR, "Link na bio",
    "linktr.ee", "linktree.com", "bio.link", "beacons.ai", "campsite.bio", "lnk.bio",
    "taplink.cc", "taplink.at", "linkin.bio", "msha.ke", "bio.site", "solo.to",
    "allmylinks.com", "hoo.be", "tap.bio", "linkme.bio", "linkbio.co", "about.me",
    "lnk.to", "contate.me", "zaap.bio", "linkr.bio", "flow.page",
)
_reg(
    DELIVERY, "Delivery/cardápio",
    "ifood.com.br", "rappi.com.br", "aiqfome.com", "ubereats.com", "goomer.app",
    "anota.ai", "menudino.com", "cardapioweb.com", "deliverydireto.com.br",
    "olaclick.com", "instadelivery.com.br", "neemo.com.br", "jotaja.com",
    "saipos.com", "ceofood.com.br", "delivery.much.com.br", "pedidosja.com",
    "cardapio.ai", "menu.app", "consumer.app", "99app.com", "keeta.com",
)
_reg(
    AGENDAMENTO, "Agendamento",
    "booksy.com", "trinks.com", "avec.app", "simplesagenda.com", "beautydate.com.br",
    "salaovip.com.br", "calendly.com", "agendasalao.com.br", "gendo.app",
    "appbarber.com.br", "agendaedu.com", "minhaagenda.app",
)
_reg(
    MARKETPLACE, "Marketplace",
    "mercadolivre.com.br", "mercadolibre.com", "shopee.com.br", "elo7.com.br",
    "olx.com.br", "enjoei.com.br", "amazon.com.br", "magazineluiza.com.br",
    "americanas.com.br", "hotmart.com", "kiwify.com.br", "sympla.com.br",
)
_reg(
    DIRETORIO, "Diretório/mapa",
    "google.com", "google.com.br", "goo.gl", "g.page", "g.co", "business.google.com",
    "maps.app.goo.gl", "waze.com", "moovitapp.com", "guiamais.com.br",
    "apontador.com.br", "telelistas.net", "solutudo.com.br", "cnpj.biz",
    "econodata.com.br", "cnpja.com", "casadosdados.com.br", "empresascnpj.com",
    "consultas.plus", "tripadvisor.com", "tripadvisor.com.br", "yelp.com",
    "foursquare.com", "booking.com", "airbnb.com", "airbnb.com.br", "hagah.com.br",
    "guiadacidade.com.br", "listamais.com.br", "infobel.com", "cylex.com.br",
    "habitissimo.com.br", "getninjas.com.br", "doctoralia.com.br",
    "boaconsulta.com", "reclameaqui.com.br", "restaurantguru.com",
    "restaurantguru.com.br", "kekanto.com.br", "encontrabrasilia.com",
    "encontrabrasil.com.br", "brasilia.cylex.com.br", "empresaqui.com.br",
    "informecadastral.com.br", "cnpjs.rocks", "cadastroempresa.com.br",
    "speedio.com.br", "bing.com", "duckduckgo.com", "apple.com", "here.com",
    "openstreetmap.org", "wikimapia.org", "mapcarta.com", "trivago.com.br",
    "hoteis.com", "decolar.com", "expedia.com.br", "zapimoveis.com.br",
    "vivareal.com.br", "imovelweb.com.br", "olx.com", "ifixit.com",
    "guiabrasilia.com.br", "achei.com.br", "maplink.global", "glassdoor.com.br",
    "indeed.com", "infojobs.com.br", "catho.com.br", "vagas.com.br",
)
_reg(
    NAO_RELACIONADO, "Notícias/governo/referência",
    "wikipedia.org", "g1.globo.com", "globo.com", "metropoles.com",
    "correiobraziliense.com.br", "jornaldebrasilia.com.br", "uol.com.br",
    "terra.com.br", "folha.uol.com.br", "estadao.com.br", "r7.com", "cnnbrasil.com.br",
    "agenciabrasil.ebc.com.br", "jusbrasil.com.br", "escavador.com", "gov.br",
    "df.gov.br", "jus.br", "leg.br", "mp.br", "reddit.com", "medium.com",
)
_reg(
    ENCURTADOR, "Encurtador",
    "bit.ly", "tinyurl.com", "cutt.ly", "is.gd", "ow.ly", "rebrand.ly", "encurtador.com.br",
    "l.instagram.com", "lm.facebook.com", "l.facebook.com",
)

# Sufixos de subdomínios de construtores de site: o site existe, mas não em
# domínio próprio. Tratamos como site encontrado (com subtipo), para não
# oferecer "site" a quem já mantém um.
_BUILDER_SUFFIXES = {
    "wixsite.com": "Wix",
    "wix.com": "Wix",
    "editorx.io": "Wix",
    "sites.google.com": "Google Sites",
    "business.site": "Google Business (site)",
    "negocio.site": "Google Business (site)",
    "webnode.page": "Webnode",
    "webnode.com.br": "Webnode",
    "wordpress.com": "WordPress.com",
    "blogspot.com": "Blogger",
    "carrd.co": "Carrd",
    "godaddysites.com": "GoDaddy",
    "site123.me": "SITE123",
    "ueniweb.com": "UENI",
    "jimdosite.com": "Jimdo",
    "jimdofree.com": "Jimdo",
    "weebly.com": "Weebly",
    "squarespace.com": "Squarespace",
    "nuvemshop.com.br": "Nuvemshop",
    "lojaintegrada.com.br": "Loja Integrada",
    "mercadoshops.com.br": "Mercado Shops",
    "vercel.app": "Vercel",
    "netlify.app": "Netlify",
    "github.io": "GitHub Pages",
    "hostingersite.com": "Hostinger",
    "my.canva.site": "Canva",
    "canva.site": "Canva",
    "tray.com.br": "Tray",
    "ecwid.com": "Ecwid",
    "webflow.io": "Webflow",
    "framer.website": "Framer",
    "framer.ai": "Framer",
}

# Subdomínios de domínios de diretório que na verdade são do Google Business
_SECOND_LEVEL_BR = {
    "com", "net", "org", "art", "adv", "eng", "med", "blog", "eco", "edu", "gov",
    "ind", "inf", "jus", "leg", "mp", "odo", "psi", "tur", "vet", "arq", "eti",
    "srv", "tv", "agr", "esp", "fm", "imb", "app", "dev", "log", "rec", "seg",
    "radio", "coop", "bio", "ong", "nom", "far", "fot", "pro", "slg", "tmp", "trd",
    "abc", "emp", "taxi", "wiki", "nutr", "ato", "bib", "cim", "cng", "cnt", "ecn",
    "enf", "eti", "fnd", "fst", "geo", "ggf", "jor", "lel", "mat", "mus", "not",
    "ntr", "ppg", "qsl", "rep", "zlg", "des", "det",
}

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.com.br", "outlook.com",
    "outlook.com.br", "live.com", "msn.com", "yahoo.com", "yahoo.com.br",
    "ymail.com", "bol.com.br", "uol.com.br", "terra.com.br", "ig.com.br",
    "icloud.com", "me.com", "mac.com", "globo.com", "globomail.com",
    "zipmail.com.br", "protonmail.com", "proton.me", "r7.com", "oi.com.br",
    "pop.com.br", "aol.com", "gmx.com", "gmx.net", "yandex.com", "zoho.com",
    "superig.com.br", "brturbo.com.br", "click21.com.br", "tutanota.com",
}

_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|igshid$|igsh$|si$|ref$|mc_)")


@dataclass(frozen=True)
class UrlClass:
    kind: str
    platform: str | None = None
    host: str | None = None
    domain: str | None = None

    @property
    def is_site(self) -> bool:
        return self.kind in SITE_KINDS


def normalize_url(url: str | None) -> str | None:
    """Normaliza URL para comparação (sem www, sem barra final, sem rastreio)."""
    if not url:
        return None
    u = str(url).strip().strip("<>\"' ")
    if not u or " " in u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", u):
        if "@" in u and "/" not in u:
            return None  # e-mail
        u = "https://" + u
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        return None
    if host.startswith("www."):
        host = host[4:]
    path = parts.path or ""
    if path.endswith("/"):
        path = path.rstrip("/")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)])
    netloc = host + (f":{parts.port}" if parts.port and parts.port not in (80, 443) else "")
    return urlunsplit(("https", netloc, path, query, ""))


def host_of(url: str | None) -> str | None:
    n = normalize_url(url)
    if not n:
        return None
    return urlsplit(n).hostname


def registrable_domain(host: str | None) -> str | None:
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) >= 3 and labels[-1] == "br" and labels[-2] in _SECOND_LEVEL_BR:
        return ".".join(labels[-3:])
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host


def _builder_platform(host: str, path: str) -> str | None:
    if host == "sites.google.com":
        return "Google Sites"
    for suffix, name in _BUILDER_SUFFIXES.items():
        if host == suffix or host.endswith("." + suffix):
            if host == suffix and suffix in ("wix.com", "wordpress.com", "squarespace.com", "nuvemshop.com.br", "tray.com.br"):
                return None  # página do próprio construtor, não um site de cliente
            return name
    return None


def classify_url(url: str | None) -> UrlClass:
    n = normalize_url(url)
    if not n:
        return UrlClass(INVALIDO)
    parts = urlsplit(n)
    host = parts.hostname or ""
    path = parts.path or ""
    domain = registrable_domain(host)

    builder = _builder_platform(host, path)
    if builder:
        return UrlClass(SITE_PLATAFORMA, builder, host, domain)

    if host == "api.whatsapp.com" or host == "chat.whatsapp.com" or host.endswith(".whatsapp.com"):
        return UrlClass(WHATSAPP, "WhatsApp", host, domain)
    if host in ("maps.google.com", "maps.app.goo.gl") or path.startswith("/maps"):
        if domain in ("google.com", "google.com.br", "goo.gl"):
            return UrlClass(DIRETORIO, "Google Maps", host, domain)

    # Correspondência exata do host tem prioridade (ex.: g1.globo.com)
    for candidate in (host, domain):
        if candidate in _DOMAINS:
            kind, platform = _DOMAINS[candidate]
            return UrlClass(kind, platform, host, domain)
    # Sufixos governamentais/judiciais
    for suffix in (".gov.br", ".jus.br", ".leg.br", ".mp.br", ".mil.br"):
        if host.endswith(suffix):
            return UrlClass(NAO_RELACIONADO, "Governo/Judiciário", host, domain)
    # Subdomínios de diretórios (ex.: brasilia.cylex.com.br)
    for d, (kind, platform) in _DOMAINS.items():
        if host.endswith("." + d):
            return UrlClass(kind, platform, host, domain)
    return UrlClass(PROPRIO, None, host, domain)


def email_domain(email: str | None) -> str | None:
    """Domínio do e-mail, se não for de provedor gratuito."""
    if not email or "@" not in email:
        return None
    domain = email.strip().lower().rsplit("@", 1)[1].strip(". ")
    if not domain or "." not in domain or domain in FREE_EMAIL_DOMAINS:
        return None
    return domain


def social_handle_url(platform: str, value: str | None) -> str | None:
    """Converte '@perfil' ou 'perfil' em URL (tags do OSM às vezes trazem só o nome)."""
    if not value:
        return None
    v = value.strip()
    if v.startswith("http://") or v.startswith("https://") or "." in v.split("/")[0]:
        return v
    v = v.lstrip("@").strip("/")
    if not v:
        return None
    base = {
        "instagram": "https://instagram.com/",
        "facebook": "https://facebook.com/",
        "tiktok": "https://tiktok.com/@",
        "twitter": "https://x.com/",
        "youtube": "https://youtube.com/@",
        "linkedin": "https://linkedin.com/company/",
    }.get(platform)
    return base + v if base else None
