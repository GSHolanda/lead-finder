"""Validação do piloto: revisão manual de uma amostra de leads classificados
automaticamente como "site próprio não localizado" (inclui "somente redes
sociais ou plataformas", que também significa site próprio não localizado).

Mede quantos tinham site que a ferramenta não encontrou (falso "sem site"),
com intervalo de confiança, e diagnostica cada erro para orientar melhorias
da verificação ANTES de aumentar o volume de prospecção.
"""

from __future__ import annotations

import math
import random
import sqlite3
import time

from leadfinder import statuses as st
from leadfinder import urls
from leadfinder.db import dumps, loads, now_iso
from leadfinder.discovery.segments import segment_label
from leadfinder.verification.manual import set_manual_status

REVIEW_RESULTS = {
    "sem_site_confirmado": "Confirmado: não tem site próprio",
    "tinha_site": "Tinha site próprio (a ferramenta não encontrou)",
    "inconclusivo": "Não consegui confirmar",
}

HOW_FOUND = {
    "busca_google": "Busca no Google/Bing pelo nome",
    "busca_telefone": "Busca pelo telefone",
    "bio_instagram": "Link na bio do Instagram",
    "perfil_facebook": "Página do Facebook",
    "google_maps": "Perfil no Google Maps",
    "dominio_similar": "Digitando nome.com.br",
    "diretorio": "Guia/diretório",
    "outro": "Outro",
}

RECOMMENDATIONS = {
    "nunca_apareceu_busca": "Sites não apareceram nas consultas: aumente as consultas (ex.: nome + segmento), "
                            "ative mais motores no SearXNG ou teste outro provedor.",
    "sem_busca": "Verificação rodou sem provedor de busca: configure SearXNG/Brave antes de escalar.",
    "candidato_nao_confirmado": "O site apareceu como candidato mas não foi confirmado: revise as regras de evidência "
                                "(ex.: telefone em imagem, página de contato com outro caminho).",
    "candidato_bloqueado": "O site apareceu mas estava bloqueado/fora do ar no acesso automático: "
                           "esses casos devem ir para revisão manual.",
    "bio_instagram": "Sites estavam na bio do Instagram: cole a bio como material ou registre o link do "
                     "agregador — a ferramenta não lê o Instagram automaticamente.",
    "google_maps": "Sites estavam no Google Maps: confira o perfil no Maps durante a revisão manual "
                   "(não é consultado automaticamente).",
    "dominio_similar": "Sites em domínio parecido com o nome: mantenha 'adivinhar domínio' ativo e "
                       "considere variações (com hífen, com segmento).",
    "diretorio": "Sites só citados em guias: considere ler o link de site publicado em diretórios.",
}


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def create_sample(conn: sqlite3.Connection, size: int = 30, statuses: list[str] | None = None,
                  seed: int | None = None, name: str | None = None, exclude_sampled: bool = True) -> dict:
    statuses = statuses or [st.NAO_LOCALIZADO, st.SOMENTE_REDES]
    seed = seed if seed is not None else int(time.time())
    q = ("SELECT id, site_status_auto, site_confidence_auto, segment FROM leads WHERE site_status_auto IN ("
         + ",".join("?" * len(statuses)) + ")")
    params: list = list(statuses)
    if exclude_sampled:
        q += " AND id NOT IN (SELECT lead_id FROM pilot_items)"
    pool = [dict(r) for r in conn.execute(q + " ORDER BY id", params)]
    if not pool:
        raise ValueError("Nenhum lead classificado como 'site próprio não localizado' disponível para amostra.")
    chosen = random.Random(seed).sample(pool, min(size, len(pool)))
    sample_id = conn.execute(
        "INSERT INTO pilot_samples(name, created_at, seed, criteria_json) VALUES (?,?,?,?)",
        (name or f"Amostra {now_iso()[:10]}", now_iso(), seed,
         dumps({"tamanho_pedido": size, "classificacoes": statuses, "universo": len(pool)})),
    ).lastrowid
    for lead in chosen:
        conn.execute(
            "INSERT INTO pilot_items(sample_id, lead_id, status_at_sampling, confidence_at_sampling, segment_at_sampling)"
            " VALUES (?,?,?,?,?)",
            (sample_id, lead["id"], lead["site_status_auto"], lead["site_confidence_auto"], lead["segment"]),
        )
    conn.commit()
    return get_sample(conn, sample_id)


def get_sample(conn: sqlite3.Connection, sample_id: int) -> dict:
    s = conn.execute("SELECT * FROM pilot_samples WHERE id=?", (sample_id,)).fetchone()
    if not s:
        raise ValueError("amostra não encontrada")
    items = [dict(r) for r in conn.execute(
        "SELECT p.*, l.name, l.neighborhood, l.city, l.phone_norm FROM pilot_items p JOIN leads l ON l.id=p.lead_id"
        " WHERE p.sample_id=? ORDER BY p.id", (sample_id,))]
    d = dict(s)
    d["criteria"] = loads(d.pop("criteria_json"), {})
    d["items"] = items
    return d


def list_samples(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT s.id, s.name, s.created_at, COUNT(p.id) AS itens,"
        " SUM(CASE WHEN p.review_result IS NOT NULL THEN 1 ELSE 0 END) AS revisados"
        " FROM pilot_samples s LEFT JOIN pilot_items p ON p.sample_id=s.id GROUP BY s.id ORDER BY s.id DESC")]


def review_item(conn: sqlite3.Connection, item_id: int, result: str, found_url: str | None = None,
                how_found: str | None = None, note: str | None = None) -> dict:
    if result not in REVIEW_RESULTS:
        raise ValueError("resultado de revisão inválido")
    item = conn.execute("SELECT * FROM pilot_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        raise ValueError("item não encontrado")
    if result == "tinha_site":
        if not found_url:
            raise ValueError("informe a URL do site encontrado")
        if how_found and how_found not in HOW_FOUND:
            raise ValueError("forma de localização inválida")
    conn.execute(
        "UPDATE pilot_items SET review_result=?, found_url=?, how_found=?, note=?, reviewed_at=? WHERE id=?",
        (result, found_url if result == "tinha_site" else None, how_found if result == "tinha_site" else None,
         note, now_iso(), item_id),
    )
    conn.commit()
    # A revisão do piloto é uma correção humana: vale para o lead.
    if result == "tinha_site":
        set_manual_status(conn, item["lead_id"], st.SITE_PROPRIO, url=found_url, source="piloto",
                          note=f"piloto: {HOW_FOUND.get(how_found or 'outro', 'outro')}" + (f" — {note}" if note else ""))
    elif result == "sem_site_confirmado":
        set_manual_status(conn, item["lead_id"], item["status_at_sampling"], source="piloto",
                          note="piloto: ausência de site confirmada na revisão" + (f" — {note}" if note else ""))
    return dict(conn.execute("SELECT * FROM pilot_items WHERE id=?", (item_id,)).fetchone())


def _diagnose_miss(conn: sqlite3.Connection, item: dict) -> str:
    found_domain = urls.registrable_domain(urls.host_of(item["found_url"]))
    run = conn.execute(
        "SELECT summary_json FROM verification_runs WHERE lead_id=? AND started_at<=? ORDER BY id DESC LIMIT 1",
        (item["lead_id"], item["reviewed_at"] or now_iso()),
    ).fetchone()
    summary = loads(run["summary_json"], {}) if run else {}
    if summary.get("provedor_busca") in (None, "nenhum"):
        return "sem_busca"
    for ev in summary.get("avaliacoes", []):
        if urls.registrable_domain(urls.host_of(ev.get("url"))) == found_domain:
            if ev.get("resultado") in ("bloqueado", "indisponivel", "erro"):
                return "candidato_bloqueado"
            return "candidato_nao_confirmado"
    if item.get("how_found") in ("bio_instagram", "google_maps", "dominio_similar", "diretorio"):
        return item["how_found"]
    return "nunca_apareceu_busca"


def report(conn: sqlite3.Connection, sample_id: int, settings: dict) -> dict:
    sample = get_sample(conn, sample_id)
    items = sample["items"]
    decided = [i for i in items if i["review_result"] in ("sem_site_confirmado", "tinha_site")]
    misses = [i for i in decided if i["review_result"] == "tinha_site"]
    n, k = len(decided), len(misses)
    lo, hi = wilson_interval(k, n)

    def group(key_fn):
        out: dict[str, dict] = {}
        for i in decided:
            g = out.setdefault(key_fn(i), {"revisados": 0, "tinham_site": 0})
            g["revisados"] += 1
            g["tinham_site"] += int(i["review_result"] == "tinha_site")
        for g in out.values():
            g["taxa"] = round(g["tinham_site"] / g["revisados"], 3) if g["revisados"] else None
        return out

    def band(i):
        return st.confidence_label(i["confidence_at_sampling"])

    diagnoses: dict[str, int] = {}
    miss_details = []
    for m in misses:
        d = _diagnose_miss(conn, m)
        diagnoses[d] = diagnoses.get(d, 0) + 1
        miss_details.append({"lead_id": m["lead_id"], "nome": m["name"], "url": m["found_url"],
                             "como_encontrou": HOW_FOUND.get(m["how_found"] or "outro"), "diagnostico": d,
                             "classificacao_na_amostra": st.SITE_STATUS_LABELS.get(m["status_at_sampling"])})
    how: dict[str, int] = {}
    for m in misses:
        key = HOW_FOUND.get(m["how_found"] or "outro")
        how[key] = how.get(key, 0) + 1

    limit = float(settings.get("piloto_limite_erro", 0.10))
    min_n = int(settings.get("piloto_minimo_revisados", 20))
    if n < min_n:
        gate = {"pode_escalar": False,
                "motivo": f"Revise pelo menos {min_n} itens (revisados: {n}) antes de decidir."}
    elif hi > limit:
        gate = {"pode_escalar": False,
                "motivo": f"Taxa de site não encontrado pode chegar a {hi:.0%} (limite {limit:.0%}). "
                          "Melhore a verificação e repita o piloto antes de aumentar o volume."}
    else:
        gate = {"pode_escalar": True,
                "motivo": f"Limite superior do erro ({hi:.0%}) dentro do aceitável ({limit:.0%})."}
    recs = [RECOMMENDATIONS[d] for d, _ in sorted(diagnoses.items(), key=lambda kv: -kv[1]) if d in RECOMMENDATIONS]
    return {
        "amostra": {"id": sample["id"], "nome": sample["name"], "criada_em": sample["created_at"],
                    "semente": sample["seed"], "criterios": sample["criteria"]},
        "itens": len(items),
        "revisados": n,
        "inconclusivos": sum(1 for i in items if i["review_result"] == "inconclusivo"),
        "pendentes": sum(1 for i in items if not i["review_result"]),
        "tinham_site": k,
        "taxa_site_nao_encontrado": round(k / n, 3) if n else None,
        "intervalo_95": [round(lo, 3), round(hi, 3)],
        "por_classificacao": group(lambda i: st.SITE_STATUS_LABELS.get(i["status_at_sampling"], i["status_at_sampling"])),
        "por_segmento": group(lambda i: segment_label(i["segment_at_sampling"])),
        "por_confianca": group(band),
        "como_foram_encontrados": how,
        "diagnosticos": diagnoses,
        "erros": miss_details,
        "recomendacoes": recs,
        "decisao": gate,
    }


def regress(conn: sqlite3.Connection, sample_id: int, ctx) -> dict:
    """Reexecuta a verificação nos leads em que a ferramenta errou e mede
    quantos passam a ser detectados (útil após ajustar a verificação)."""
    from leadfinder.verification.verifier import verify_lead

    misses = [dict(r) for r in conn.execute(
        "SELECT * FROM pilot_items WHERE sample_id=? AND review_result='tinha_site'", (sample_id,))]
    detected, details = 0, []
    for m in misses:
        res = verify_lead(conn, m["lead_id"], ctx)
        expected = urls.registrable_domain(urls.host_of(m["found_url"]))
        got = urls.registrable_domain(urls.host_of(res.get("url"))) if res.get("url") else None
        ok = res["status"] == st.SITE_PROPRIO and (got == expected or got is None)
        detected += int(ok)
        details.append({"lead_id": m["lead_id"], "esperado": m["found_url"], "classificacao": res["status"],
                        "url": res.get("url"), "detectado": ok})
    return {"casos": len(misses), "detectados_agora": detected, "detalhes": details}
