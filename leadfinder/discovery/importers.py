"""Importação de listas: CSV genérico e arquivos de dados abertos do CNPJ.

Dados abertos do CNPJ (Receita Federal): arquivos "Estabelecimentos*.csv"
(separados por ';', codificação latin-1, sem cabeçalho). Gratuitos em
https://dados.gov.br — procure por "Cadastro Nacional da Pessoa Jurídica".

Cuidados adotados:
- só estabelecimentos ATIVOS (situação cadastral 02);
- só registros com NOME FANTASIA (evita nomes de pessoas físicas de MEIs);
- telefone e e-mail do cadastro podem ser do contador: a origem fica marcada
  como "cnpj" e a interface avisa isso. O domínio desse e-mail só conta como
  site da empresa se o conteúdo do site confirmar.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from leadfinder.discovery.segments import segment_for_cnae
from leadfinder.leads import LeadRecord, upsert_record
from leadfinder.normalize import norm_text

# Layout oficial do arquivo de Estabelecimentos
CNPJ_COLUMNS = [
    "cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial", "nome_fantasia",
    "situacao_cadastral", "data_situacao", "motivo_situacao", "cidade_exterior", "pais",
    "data_inicio", "cnae_principal", "cnae_secundaria", "tipo_logradouro", "logradouro",
    "numero", "complemento", "bairro", "cep", "uf", "municipio", "ddd1", "telefone1",
    "ddd2", "telefone2", "ddd_fax", "fax", "email", "situacao_especial", "data_situacao_especial",
]

# Código TOM/Receita do município de Brasília
MUNICIPIO_BRASILIA = "9701"


@dataclass
class ImportResult:
    read: int = 0
    imported: int = 0
    merged: int = 0
    skipped: int = 0
    skipped_reasons: dict = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.skipped_reasons[reason] = self.skipped_reasons.get(reason, 0) + 1


def _open_text(path: str) -> io.TextIOBase:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return io.StringIO(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return io.StringIO(raw.decode("utf-8", errors="replace"))


# --- CSV genérico ------------------------------------------------------------

CSV_ALIASES = {
    "name": ["nome", "name", "empresa", "nome_fantasia", "estabelecimento", "razao_social"],
    "phone": ["telefone", "phone", "fone", "tel", "celular"],
    "whatsapp": ["whatsapp", "whats", "zap"],
    "email": ["email", "e-mail", "e_mail"],
    "website": ["site", "website", "url", "pagina"],
    "instagram": ["instagram", "insta"],
    "facebook": ["facebook", "fb"],
    "other_links": ["links", "outros_links", "redes"],
    "street": ["endereco", "logradouro", "rua", "address", "street"],
    "housenumber": ["numero", "número", "num", "housenumber"],
    "neighborhood": ["bairro", "regiao", "região", "ra", "setor", "neighborhood"],
    "city": ["cidade", "municipio", "município", "city"],
    "uf": ["uf", "estado", "state"],
    "postcode": ["cep", "postcode"],
    "cnpj": ["cnpj"],
    "segment": ["segmento", "segment", "categoria", "ramo"],
    "source_url": ["fonte_url", "fonte", "source_url", "link_fonte"],
}


def _map_header(header: list[str]) -> dict[str, int]:
    idx = {}
    normalized = [norm_text(h).replace(" ", "_") for h in header]
    for field_name, aliases in CSV_ALIASES.items():
        for alias in aliases:
            a = norm_text(alias).replace(" ", "_")
            if a in normalized:
                idx[field_name] = normalized.index(a)
                break
    return idx


def import_csv(conn: sqlite3.Connection, path: str, source_label: str | None = None,
               default_city: str | None = None, default_uf: str | None = None) -> ImportResult:
    """Importa uma lista CSV montada pelo usuário (separador ',' ou ';')."""
    fh = _open_text(path)
    sample = fh.read(4096)
    fh.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(fh, dialect)
    header = next(reader, None)
    result = ImportResult()
    if not header:
        return result
    idx = _map_header(header)
    if "name" not in idx:
        raise ValueError("CSV sem coluna de nome (use 'nome' ou 'empresa')")
    label = source_label or Path(path).name
    for line_no, row in enumerate(reader, start=2):
        result.read += 1

        def get(key: str) -> str | None:
            i = idx.get(key)
            if i is None or i >= len(row):
                return None
            v = row[i].strip()
            return v or None

        name = get("name")
        if not name:
            result.skip("sem nome")
            continue
        links = []
        for key, origin in (("website", "csv:site"), ("instagram", "csv:instagram"), ("facebook", "csv:facebook")):
            v = get(key)
            if v:
                if key == "instagram" and "/" not in v:
                    v = "https://instagram.com/" + v.lstrip("@")
                links.append((v, origin))
        other = get("other_links")
        if other:
            for part in other.replace(",", " ").replace(";", " ").split():
                links.append((part, "csv:links"))
        seg = get("segment")
        rec = LeadRecord(
            name=name,
            source="csv",
            source_ref=f"{label}:{line_no}",
            source_url=get("source_url"),
            segment=seg if seg else None,
            phone=get("phone"),
            phone_source="csv",
            whatsapp=get("whatsapp"),
            email=get("email"),
            email_source="csv",
            street=get("street"),
            housenumber=get("housenumber"),
            neighborhood=get("neighborhood"),
            city=get("city") or default_city,
            uf=get("uf") or default_uf,
            postcode=get("postcode"),
            cnpj=get("cnpj"),
            links=links,
            raw={"arquivo": label, "linha": line_no, "valores": dict(zip(header, row))},
        )
        _, created = upsert_record(conn, rec)
        result.imported += int(created)
        result.merged += int(not created)
    conn.commit()
    return result


# --- Dados abertos do CNPJ ---------------------------------------------------

def load_municipios(path: str) -> dict[str, str]:
    """Tabela de municípios da Receita (código;nome) -> {nome_normalizado: código}."""
    out = {}
    for row in csv.reader(_open_text(path), delimiter=";"):
        if len(row) >= 2:
            out[norm_text(row[1])] = row[0].strip()
    return out


def load_cnaes(path: str) -> dict[str, str]:
    out = {}
    for row in csv.reader(_open_text(path), delimiter=";"):
        if len(row) >= 2:
            out[row[0].strip()] = row[1].strip()
    return out


def cnpj_row_to_record(row: list[str], cnae_labels: dict[str, str] | None = None,
                       city_name: str | None = None) -> LeadRecord | None:
    if len(row) < len(CNPJ_COLUMNS) - 2:
        return None
    d = dict(zip(CNPJ_COLUMNS, [c.strip() for c in row]))
    name = d.get("nome_fantasia")
    if not name:
        return None
    cnpj = d["cnpj_basico"] + d["cnpj_ordem"] + d["cnpj_dv"]
    phone = (d.get("ddd1", "") + d.get("telefone1", "")) or None
    street = " ".join(p for p in (d.get("tipo_logradouro"), d.get("logradouro")) if p) or None
    cnae = d.get("cnae_principal")
    return LeadRecord(
        name=name.title() if name.isupper() else name,
        source="cnpj",
        source_ref=cnpj,
        segment=segment_for_cnae(cnae),
        category_raw=(f"CNAE {cnae}" + (f" — {cnae_labels[cnae]}" if cnae_labels and cnae in cnae_labels else "")) if cnae else None,
        phone=phone,
        phone_source="cnpj",
        email=d.get("email") or None,
        email_source="cnpj",
        street=street,
        housenumber=d.get("numero") or None,
        neighborhood=(d.get("bairro") or "").title() or None,
        city=city_name or ("Brasília" if d.get("municipio") == MUNICIPIO_BRASILIA else None),
        uf=d.get("uf") or None,
        postcode=d.get("cep") or None,
        cnpj=cnpj,
        raw={k: d[k] for k in ("cnpj_basico", "cnpj_ordem", "cnpj_dv", "situacao_cadastral",
                               "cnae_principal", "cnae_secundaria", "uf", "municipio", "bairro")},
    )


def import_cnpj_estabelecimentos(
    conn: sqlite3.Connection,
    path: str,
    uf: str = "DF",
    municipios: list[str] | None = None,
    segment_keys: list[str] | None = None,
    cnae_labels: dict[str, str] | None = None,
    municipio_names: dict[str, str] | None = None,
    limit: int | None = None,
) -> ImportResult:
    """Filtra um arquivo de Estabelecimentos e importa os leads."""
    result = ImportResult()
    wanted_munis = set(municipios or [])
    with open(path, encoding="latin-1", newline="") as fh:
        for row in csv.reader(fh, delimiter=";", quotechar='"'):
            if limit and result.imported >= limit:
                break
            result.read += 1
            if len(row) < 21:
                result.skip("linha inválida")
                continue
            if row[19].strip() != uf:
                continue
            if wanted_munis and row[20].strip() not in wanted_munis:
                continue
            if row[5].strip() != "02":
                result.skip("não ativa")
                continue
            seg = segment_for_cnae(row[11].strip())
            if segment_keys and seg not in segment_keys:
                continue
            city = (municipio_names or {}).get(row[20].strip())
            rec = cnpj_row_to_record(row, cnae_labels, city_name=city)
            if rec is None:
                result.skip("sem nome fantasia")
                continue
            _, created = upsert_record(conn, rec)
            result.imported += int(created)
            result.merged += int(not created)
            if result.read % 5000 == 0:
                conn.commit()
    conn.commit()
    return result
