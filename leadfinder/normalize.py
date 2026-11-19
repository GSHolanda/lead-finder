"""Normalização de textos, telefones, CNPJ, CEP e nomes (pt-BR)."""

from __future__ import annotations

import re
import unicodedata

_SPACES = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def norm_text(text: str | None) -> str:
    """Minúsculas, sem acentos, só letras/dígitos separados por espaço."""
    if not text:
        return ""
    t = strip_accents(str(text)).lower()
    t = _NON_ALNUM.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


# --- Telefones -------------------------------------------------------------

def normalize_phone(value: str | None) -> str | None:
    """Retorna DDD + número (10 ou 11 dígitos) ou None.

    Aceita formatos como "+55 (61) 99999-1234", "061 3333-4444", "5561999991234".
    """
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) in (11, 12):
        digits = digits[1:]
    if len(digits) not in (10, 11):
        return None
    ddd = digits[:2]
    if ddd[0] == "0" or ddd[1] == "0":
        return None
    if len(digits) == 11 and digits[2] != "9":
        return None
    return digits


def phone_key(norm: str | None) -> tuple[str, str] | None:
    """Chave de comparação: (DDD, últimos 8 dígitos).

    Celulares antigos sem o 9 inicial continuam casando com a versão nova.
    """
    if not norm or len(norm) < 10:
        return None
    return norm[:2], norm[-8:]


def phones_match(a: str | None, b: str | None) -> bool:
    ka, kb = phone_key(a), phone_key(b)
    return ka is not None and ka == kb


def is_mobile(norm: str | None) -> bool:
    return bool(norm) and len(norm) == 11 and norm[2] == "9"


def format_phone(norm: str | None) -> str:
    if not norm:
        return ""
    if len(norm) == 11:
        return f"({norm[:2]}) {norm[2:7]}-{norm[7:]}"
    if len(norm) == 10:
        return f"({norm[:2]}) {norm[2:6]}-{norm[6:]}"
    return norm


_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\s?55[\s.\-]?)?\(?0?(\d{2})\)?[\s.\-]?(9[\s.]?\d{4}|\d{4})[\s.\-]?(\d{4})(?!\d)"
)
_WA_RE = re.compile(r"(?:wa\.me/|phone=)(\+?\d{10,13})")


def find_phones(text: str | None) -> set[str]:
    """Encontra telefones brasileiros em texto livre (inclui links wa.me)."""
    found: set[str] = set()
    if not text:
        return found
    for m in _PHONE_RE.finditer(text):
        raw = m.group(1) + re.sub(r"\D", "", m.group(2)) + m.group(3)
        norm = normalize_phone(raw)
        if norm:
            found.add(norm)
    for m in _WA_RE.finditer(text):
        norm = normalize_phone(m.group(1))
        if norm:
            found.add(norm)
    return found


# --- CNPJ / CEP / e-mail ---------------------------------------------------

_CNPJ_RE = re.compile(r"(?<!\d)(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})-?(\d{2})(?!\d)")
_CEP_RE = re.compile(r"(?<!\d)(\d{5})-?(\d{3})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def normalize_cnpj(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) == 14 else None


def find_cnpjs(text: str | None) -> set[str]:
    if not text:
        return set()
    return {"".join(m.groups()) for m in _CNPJ_RE.finditer(text)}


def normalize_cep(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) == 8 else None


def find_ceps(text: str | None) -> set[str]:
    if not text:
        return set()
    return {a + b for a, b in _CEP_RE.findall(text)}


def find_emails(text: str | None) -> set[str]:
    if not text:
        return set()
    return {e.lower().rstrip(".") for e in _EMAIL_RE.findall(text)}


# --- Nomes -----------------------------------------------------------------

LEGAL_SUFFIXES = {
    "ltda", "me", "epp", "eireli", "sa", "s", "a", "mei", "slu", "cia", "ss",
}
STOPWORDS = {
    "de", "da", "do", "das", "dos", "e", "a", "o", "as", "os", "the", "and",
    "em", "na", "no", "&",
}
# Palavras de segmento/lugar: ajudam a ler o nome, mas não o distinguem.
GENERIC_WORDS = {
    "restaurante", "lanchonete", "bar", "salao", "studio", "estudio", "clinica",
    "loja", "oficina", "auto", "center", "centro", "comercio", "servicos",
    "servico", "brasilia", "df", "distrito", "federal", "goias", "go", "cafe",
    "padaria", "pizzaria", "barbearia", "academia", "pet", "shop", "store",
    "beleza", "estetica", "odontologia", "consultorio", "escritorio",
    "advocacia", "advogados", "contabilidade", "imobiliaria", "otica", "mercado",
    "supermercado", "mini", "minimercado", "casa", "espaco", "atelie", "ateliê",
    "grupo", "empresa", "unidade", "filial", "matriz", "oficial",
}


def name_tokens(name: str | None) -> list[str]:
    toks = norm_text(name).split()
    return [t for t in toks if t not in STOPWORDS and t not in LEGAL_SUFFIXES]


def distinctive_tokens(name: str | None) -> list[str]:
    return [t for t in name_tokens(name) if t not in GENERIC_WORDS and len(t) >= 2]


def name_in_text(name: str | None, text_norm: str) -> bool:
    """O nome da empresa aparece no texto (já normalizado)?

    Exige todos os termos distintivos do nome (ou a frase completa, quando o
    nome só tem palavras genéricas). Isso é evidência FRACA: nunca basta, sozinha,
    para associar um site à empresa.
    """
    if not name or not text_norm:
        return False
    padded = f" {text_norm} "
    full = norm_text(name)
    if full and f" {full} " in padded:
        return True
    dist = distinctive_tokens(name)
    if not dist:
        return False
    return all(f" {t} " in padded for t in dist)


def slugify(text: str | None, sep: str = "") -> str:
    toks = name_tokens(text)
    return sep.join(toks)


def address_key(street: str | None, housenumber: str | None) -> str | None:
    """Chave simples de endereço para comparação textual."""
    s = norm_text(street)
    if not s:
        return None
    n = norm_text(housenumber)
    return f"{s} {n}".strip() if n else s


def address_in_text(street: str | None, housenumber: str | None, text_norm: str) -> bool:
    """Logradouro (e número, quando houver) aparecem próximos no texto."""
    s = norm_text(street)
    if len(s) < 5 or not text_norm:
        return False
    idx = text_norm.find(s)
    if idx < 0:
        return False
    n = norm_text(housenumber)
    if not n:
        return False  # só o logradouro é comum demais em Brasília (ex.: "CLN 205")
    after = text_norm[idx + len(s): idx + len(s) + 40]
    return re.search(rf"(?<![0-9a-z]){re.escape(n)}(?![0-9a-z])", after) is not None
