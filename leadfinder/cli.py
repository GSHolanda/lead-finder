"""Linha de comando: ``python -m leadfinder <comando>``."""

from __future__ import annotations

import argparse
import json
import sys

from leadfinder import statuses as st
from leadfinder.config import get_settings, make_client, public_settings, update_settings
from leadfinder.db import connect, default_db_path


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def cmd_init(conn, args):
    print(f"Banco pronto em {args.db}")


def cmd_serve(conn, args):
    from leadfinder.server import serve

    conn.close()
    serve(args.db, args.host, args.port)


def cmd_segments(conn, args):
    from leadfinder.discovery.segments import SEGMENTS

    for k, v in SEGMENTS.items():
        print(f"{k:24} {v['label']}")


def cmd_discover(conn, args):
    from leadfinder.discovery.osm import discover

    settings = get_settings(conn)
    if args.ra:
        rt, name = "ra", args.ra
    elif args.municipio:
        rt, name = "municipio_go", args.municipio
    else:
        rt, name = "df", None
    res = discover(conn, make_client(settings), settings, args.segmento, rt, name, args.raio, args.limit)
    if res.error:
        print("Erro:", res.error, file=sys.stderr)
        return 1
    print(f"{res.total_elements} estabelecimentos; {res.created} novos; {res.merged} mesclados.")


def cmd_import_csv(conn, args):
    from leadfinder.discovery.importers import import_csv

    _print(import_csv(conn, args.arquivo, default_city=args.cidade, default_uf=args.uf).__dict__)


def cmd_import_cnpj(conn, args):
    from leadfinder.discovery.importers import (
        MUNICIPIO_BRASILIA,
        import_cnpj_estabelecimentos,
        load_cnaes,
        load_municipios,
    )
    from leadfinder.normalize import norm_text

    codes = list(args.municipio or [])
    names: dict[str, str] = {}
    if args.municipios_arquivo:
        table = load_municipios(args.municipios_arquivo)
        for city in args.cidade or []:
            code = table.get(norm_text(city))
            if not code:
                print(f"Município não encontrado na tabela: {city}", file=sys.stderr)
                return 1
            codes.append(code)
            names[code] = city
    if not codes and args.uf == "DF":
        codes = [MUNICIPIO_BRASILIA]
    cnaes = load_cnaes(args.cnaes_arquivo) if args.cnaes_arquivo else None
    res = import_cnpj_estabelecimentos(conn, args.arquivo, uf=args.uf, municipios=codes, segment_keys=args.segmento,
                                       cnae_labels=cnaes, municipio_names=names, limit=args.limit)
    _print(res.__dict__)


def cmd_process(conn, args):
    from leadfinder.service import build_services, list_leads, process_lead

    svc = build_services(conn)
    if not svc.verify_ctx.provider.available:
        print("Aviso: nenhum provedor de busca configurado — os resultados ficarão como 'inconclusiva'.", file=sys.stderr)
    ids = [l["id"] for l in list_leads(conn, view=args.view, status=args.status, segment=args.segmento, limit=args.limit)]
    totals: dict[str, int] = {}
    for i, lid in enumerate(ids, 1):
        out = process_lead(conn, lid, svc, verify=not args.sem_verificacao, enrich=not args.sem_enriquecimento)
        s = (out.get("verificacao") or {}).get("status", "-")
        totals[s] = totals.get(s, 0) + 1
        print(f"[{i}/{len(ids)}] lead {lid}: {st.SITE_STATUS_LABELS.get(s, s)}")
    _print(totals)


def cmd_set_site(conn, args):
    from leadfinder.service import requalify
    from leadfinder.verification.manual import set_manual_status

    set_manual_status(conn, args.lead, args.status, url=args.url, source=args.fonte, note=args.nota)
    requalify(conn, args.lead)
    print("Correção registrada.")


def cmd_list(conn, args):
    from leadfinder.service import list_leads

    for l in list_leads(conn, view=args.view, limit=args.limit):
        score = f"{l['score']:5.1f}" if l["score"] is not None else "  -  "
        print(f"{l['id']:6} {score}  {l['status_label'][:38]:38}  {l['nome'][:40]:40} {l['local'][:30]}")


def cmd_pilot_sample(conn, args):
    from leadfinder.pilot import create_sample

    statuses = [st.NAO_LOCALIZADO] if args.so_nao_localizado else None
    s = create_sample(conn, size=args.tamanho, statuses=statuses, seed=args.semente)
    print(f"Amostra #{s['id']} com {len(s['items'])} itens (semente {s['seed']}). Revise pela interface, aba Piloto.")


def cmd_pilot_report(conn, args):
    from leadfinder.pilot import report

    _print(report(conn, args.amostra, get_settings(conn)))


def cmd_pilot_regress(conn, args):
    from leadfinder.pilot import regress
    from leadfinder.service import build_services

    _print(regress(conn, args.amostra, build_services(conn).verify_ctx))


def cmd_config(conn, args):
    if args.set:
        changes = {}
        for item in args.set:
            k, _, v = item.partition("=")
            changes[k.strip()] = v.strip()
        update_settings(conn, changes)
    _print(public_settings(conn))


def cmd_export(conn, args):
    from leadfinder.server import _csv_export

    with open(args.saida, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(_csv_export(conn, args.view))
    print(f"Exportado para {args.saida}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="leadfinder", description="Prospecção de empresas sem site próprio (DF e entorno)")
    p.add_argument("--db", default=default_db_path(), help="arquivo do banco (padrão: data/leadfinder.db ou $LEADFINDER_DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="cria o banco local").set_defaults(fn=cmd_init)
    s = sub.add_parser("serve", help="abre a interface web local")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("segmentos", help="lista os segmentos disponíveis").set_defaults(fn=cmd_segments)

    s = sub.add_parser("descobrir", help="busca estabelecimentos no OpenStreetMap")
    s.add_argument("--segmento", action="append", required=True, help="chave do segmento (repita para vários)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--ra", help="Região Administrativa/bairro do DF (busca por raio)")
    g.add_argument("--municipio", help="município do entorno (GO)")
    s.add_argument("--raio", type=int, default=2500)
    s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_discover)

    s = sub.add_parser("importar-csv", help="importa uma lista CSV")
    s.add_argument("arquivo")
    s.add_argument("--cidade")
    s.add_argument("--uf")
    s.set_defaults(fn=cmd_import_csv)

    s = sub.add_parser("importar-cnpj", help="importa um arquivo de Estabelecimentos dos dados abertos do CNPJ")
    s.add_argument("arquivo")
    s.add_argument("--uf", default="DF")
    s.add_argument("--municipio", action="append", help="código de município da Receita (Brasília = 9701)")
    s.add_argument("--municipios-arquivo", help="tabela de municípios da Receita (para usar --cidade)")
    s.add_argument("--cidade", action="append", help="nome do município (requer --municipios-arquivo)")
    s.add_argument("--cnaes-arquivo", help="tabela de CNAEs da Receita (para rótulos)")
    s.add_argument("--segmento", action="append")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_import_cnpj)

    s = sub.add_parser("processar", help="verifica site, pesquisa e qualifica leads")
    s.add_argument("--view", default="verificar", choices=["verificar", "campanha", "com_site", "todos"])
    s.add_argument("--status", help="ex.: pendente, inconclusiva")
    s.add_argument("--segmento")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--sem-verificacao", action="store_true")
    s.add_argument("--sem-enriquecimento", action="store_true")
    s.set_defaults(fn=cmd_process)

    s = sub.add_parser("corrigir-site", help="registra correção humana da classificação")
    s.add_argument("lead", type=int)
    s.add_argument("status", choices=st.SITE_STATUSES)
    s.add_argument("--url")
    s.add_argument("--fonte", default="pesquisa_manual", choices=list(st.MANUAL_SOURCES))
    s.add_argument("--nota")
    s.set_defaults(fn=cmd_set_site)

    s = sub.add_parser("listar", help="lista leads")
    s.add_argument("--view", default="campanha", choices=["verificar", "campanha", "com_site", "todos"])
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("piloto-amostra", help="sorteia amostra de 'site não localizado' para revisão manual")
    s.add_argument("--tamanho", type=int, default=30)
    s.add_argument("--semente", type=int)
    s.add_argument("--so-nao-localizado", action="store_true", help="exclui 'somente redes/plataformas'")
    s.set_defaults(fn=cmd_pilot_sample)
    s = sub.add_parser("piloto-relatorio", help="mostra a taxa de sites não encontrados")
    s.add_argument("amostra", type=int)
    s.set_defaults(fn=cmd_pilot_report)
    s = sub.add_parser("piloto-reverificar", help="reverifica os casos em que a ferramenta errou")
    s.add_argument("amostra", type=int)
    s.set_defaults(fn=cmd_pilot_regress)

    s = sub.add_parser("config", help="mostra/altera configurações (ex.: --set busca_provedor=searxng)")
    s.add_argument("--set", action="append")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("exportar", help="exporta uma lista em CSV")
    s.add_argument("--view", default="campanha", choices=["verificar", "campanha", "com_site", "todos"])
    s.add_argument("--saida", default="leads.csv")
    s.set_defaults(fn=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = connect(args.db)
    try:
        return args.fn(conn, args) or 0
    except ValueError as e:
        print("Erro:", e, file=sys.stderr)
        return 1
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
