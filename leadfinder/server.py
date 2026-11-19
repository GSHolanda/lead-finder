"""Servidor web local (só biblioteca padrão).

Escuta em 127.0.0.1 por padrão. Requisições POST precisam ser JSON e, se
vierem com cabeçalho Origin, ele precisa ser o próprio servidor local —
isso impede que outros sites acionem a ferramenta pelo seu navegador.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import queue
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from leadfinder import pilot as pilot_mod
from leadfinder import statuses as st
from leadfinder.config import get_settings, make_client, public_settings, update_settings
from leadfinder.db import connect, now_iso
from leadfinder.discovery import regions
from leadfinder.discovery.osm import discover
from leadfinder.discovery.segments import SEGMENTS
from leadfinder.enrichment import FACT_LABELS, add_manual_fact, add_material, enrich_lead, reject_fact
from leadfinder.leads import LeadRecord, upsert_record
from leadfinder.outreach import approve, generate_message, save_edit
from leadfinder.service import build_services, counts, lead_detail, list_leads, process_lead, requalify
from leadfinder.verification.manual import (
    clear_manual_status,
    set_chain_flag,
    set_manual_status,
    set_pipeline_status,
)

STATIC_DIR = Path(__file__).parent / "static"


class JobManager:
    """Fila de tarefas longas (descoberta, verificação em lote), uma por vez."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.jobs: dict[int, dict] = {}
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._next = 1
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, kind: str, fn, **params) -> dict:
        with self._lock:
            job = {"id": self._next, "tipo": kind, "status": "na_fila", "progresso": 0, "total": None,
                   "mensagens": [], "resultado": None, "erro": None, "criado_em": now_iso(), "parametros": params}
            self.jobs[self._next] = job
            self._next += 1
        self._q.put((job, fn))
        return job

    def _worker(self) -> None:
        while True:
            job, fn = self._q.get()
            job["status"] = "executando"
            conn = connect(self.db_path)
            try:
                job["resultado"] = fn(conn, job)
                job["status"] = "concluido"
            except Exception as e:  # noqa: BLE001
                job["status"] = "erro"
                job["erro"] = str(e)
                job["mensagens"].append(traceback.format_exc(limit=3))
            finally:
                conn.close()

    def list(self) -> list[dict]:
        return sorted(self.jobs.values(), key=lambda j: -j["id"])[:30]


def _meta() -> dict:
    return {
        "site_status": st.SITE_STATUS_LABELS,
        "site_status_editaveis": st.SITE_STATUSES,
        "origens_manuais": st.MANUAL_SOURCES,
        "funil": st.PIPELINE_STATUSES,
        "segmentos": {k: v["label"] for k, v in SEGMENTS.items()},
        "regioes": regions.region_options(),
        "revisao_piloto": pilot_mod.REVIEW_RESULTS,
        "como_encontrou": pilot_mod.HOW_FOUND,
        "tipos_fato": FACT_LABELS,
    }


def _job_discover(params: dict):
    def run(conn, job):
        settings = get_settings(conn)
        client = make_client(settings)
        res = discover(conn, client, settings, params["segments"], params["region_type"],
                       params.get("region_name"), int(params.get("radius") or 2500),
                       int(params["limit"]) if params.get("limit") else None)
        if res.error:
            raise RuntimeError(res.error)
        job["mensagens"].append(f"{res.total_elements} estabelecimentos no OSM; {res.created} novos; {res.merged} mesclados.")
        return res.__dict__
    return run


def _job_process(params: dict):
    def run(conn, job):
        svc = build_services(conn)
        ids = [l["id"] for l in list_leads(conn, view=params.get("view") or "verificar",
                                           status=params.get("status") or None,
                                           segment=params.get("segment") or None,
                                           limit=int(params.get("limit") or 50))]
        job["total"] = len(ids)
        summary: dict[str, int] = {}
        for i, lid in enumerate(ids, start=1):
            try:
                out = process_lead(conn, lid, svc, verify=params.get("verify", True), enrich=params.get("enrich", True))
                status = (out.get("verificacao") or {}).get("status", "enriquecido")
                summary[status] = summary.get(status, 0) + 1
            except Exception as e:  # noqa: BLE001 — um lead com erro não para o lote
                job["mensagens"].append(f"lead {lid}: {e}")
            job["progresso"] = i
        return {"processados": len(ids), "resultado_por_classificacao": summary}
    return run


def _job_regress(sample_id: int):
    def run(conn, job):
        svc = build_services(conn)
        res = pilot_mod.regress(conn, sample_id, svc.verify_ctx)
        for d in res["detalhes"]:
            requalify(conn, d["lead_id"], svc.settings)
        return res
    return run


def _csv_export(conn, view: str) -> str:
    rows = list_leads(conn, view=view, limit=100000)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "nome", "segmento", "local", "classificacao_site", "confianca", "correcao_manual", "pontuacao",
                "proxima_acao", "funil"])
    for r in rows:
        w.writerow([r["id"], r["nome"], r["segmento"], r["local"], r["status_label"],
                    r["confianca_label"], "sim" if r["manual"] else "não", r["score"] or "", r["proxima_acao"] or "",
                    st.PIPELINE_STATUSES.get(r["funil"], r["funil"])])
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "LeadFinder/0.1"
    db_path: str = ""
    jobs: JobManager

    def log_message(self, fmt, *args):  # silencioso; erros vão no JSON
        pass

    # -- respostas -------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, msg: str, code: int = 400) -> None:
        self._json({"erro": msg}, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        return data if isinstance(data, dict) else {}

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin in (f"http://{host}", f"https://{host}")

    # -- roteamento ------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        if not self._origin_ok():
            return self._error("origem não permitida", 403)
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._error("envie JSON", 415)
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if method == "GET" and (path == "/" or path.startswith("/static/")):
            return self._static("index.html" if path == "/" else path[len("/static/"):])
        conn = connect(self.db_path)
        try:
            for m, pattern, fn in ROUTES:
                if m != method:
                    continue
                match = re.fullmatch(pattern, path)
                if match:
                    body = self._body() if method == "POST" else {}
                    return fn(self, conn, query, body, *[int(g) if g.isdigit() else g for g in match.groups()])
            self._error("rota não encontrada", 404)
        except ValueError as e:
            self._error(str(e), 400)
        except Exception as e:  # noqa: BLE001
            self._error(f"erro interno: {e}", 500)
        finally:
            conn.close()

    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._error("arquivo não encontrado", 404)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


# --- handlers -----------------------------------------------------------------

def h_meta(h, conn, q, b):
    h._json(_meta())


def h_health(h, conn, q, b):
    svc = build_services(conn)
    models = svc.ollama.models()
    h._json({
        "ollama": {"url": svc.settings["ollama_url"], "modelo": svc.settings["ollama_model"],
                   "disponivel": svc.ollama.available(), "modelos_instalados": models},
        "busca": {"provedor": svc.verify_ctx.provider.name, "ativo": svc.verify_ctx.provider.available},
        "vendedor_configurado": bool(svc.settings.get("vendedor_nome")),
        "contagens": counts(conn),
    })


def h_leads(h, conn, q, b):
    h._json({"leads": list_leads(conn, view=q.get("view", "campanha"), segment=q.get("segment") or None,
                                 search=q.get("q") or None, status=q.get("status") or None),
             "contagens": counts(conn)})


def h_lead(h, conn, q, b, lead_id):
    h._json(lead_detail(conn, lead_id))


def h_verify(h, conn, q, b, lead_id):
    svc = build_services(conn)
    process_lead(conn, lead_id, svc, verify=True, enrich=True)
    h._json(lead_detail(conn, lead_id))


def h_enrich(h, conn, q, b, lead_id):
    svc = build_services(conn)
    res = enrich_lead(conn, lead_id, svc.ollama)
    requalify(conn, lead_id, svc.settings)
    h._json({"enriquecimento": res, "lead": lead_detail(conn, lead_id)})


def h_site_status(h, conn, q, b, lead_id):
    set_manual_status(conn, lead_id, b.get("status"), url=b.get("url"), source=b.get("source") or "pesquisa_manual",
                      note=b.get("note"))
    requalify(conn, lead_id)
    h._json(lead_detail(conn, lead_id))


def h_site_status_clear(h, conn, q, b, lead_id):
    clear_manual_status(conn, lead_id, b.get("note"))
    requalify(conn, lead_id)
    h._json(lead_detail(conn, lead_id))


def h_chain(h, conn, q, b, lead_id):
    set_chain_flag(conn, lead_id, bool(b.get("is_chain")), b.get("note"))
    requalify(conn, lead_id)
    h._json(lead_detail(conn, lead_id))


def h_pipeline(h, conn, q, b, lead_id):
    set_pipeline_status(conn, lead_id, b.get("status"), b.get("note"))
    requalify(conn, lead_id)
    h._json(lead_detail(conn, lead_id))


def h_material(h, conn, q, b, lead_id):
    add_material(conn, lead_id, b.get("content") or "", b.get("title"), b.get("source_url") or None)
    svc = build_services(conn)
    res = enrich_lead(conn, lead_id, svc.ollama)
    requalify(conn, lead_id, svc.settings)
    h._json({"enriquecimento": res, "lead": lead_detail(conn, lead_id)})


def h_fact(h, conn, q, b, lead_id):
    add_manual_fact(conn, lead_id, b.get("kind"), b.get("value") or "", b.get("source_url") or None)
    requalify(conn, lead_id)
    h._json(lead_detail(conn, lead_id))


def h_fact_reject(h, conn, q, b, fact_id):
    row = conn.execute("SELECT lead_id FROM facts WHERE id=?", (fact_id,)).fetchone()
    if not row:
        raise ValueError("informação não encontrada")
    reject_fact(conn, fact_id)
    requalify(conn, row["lead_id"])
    h._json(lead_detail(conn, row["lead_id"]))


def h_msg_generate(h, conn, q, b, lead_id):
    svc = build_services(conn)
    use_llm = b.get("usar_ollama", True)
    generate_message(conn, lead_id, svc.settings, svc.ollama if use_llm else None)
    requalify(conn, lead_id, svc.settings)
    h._json(lead_detail(conn, lead_id))


def h_msg_edit(h, conn, q, b, msg_id):
    msg = save_edit(conn, msg_id, b.get("body") or "")
    requalify(conn, msg["lead_id"])
    h._json(lead_detail(conn, msg["lead_id"]))


def h_msg_approve(h, conn, q, b, msg_id):
    msg = approve(conn, msg_id)
    requalify(conn, msg["lead_id"])
    h._json(lead_detail(conn, msg["lead_id"]))


def h_lead_create(h, conn, q, b):
    if not (b.get("name") or "").strip():
        raise ValueError("informe o nome")
    links = [(u.strip(), "manual:usuario") for u in (b.get("links") or "").split() if u.strip()]
    rec = LeadRecord(name=b["name"].strip(), source="manual", source_ref=f"manual:{now_iso()}:{b['name'][:30]}",
                     segment=b.get("segment") or None, phone=b.get("phone") or None, phone_source="manual",
                     whatsapp=b.get("whatsapp") or None, email=b.get("email") or None, email_source="manual",
                     street=b.get("street") or None, housenumber=b.get("housenumber") or None,
                     neighborhood=b.get("neighborhood") or None, city=b.get("city") or "Brasília",
                     uf=b.get("uf") or "DF", links=links, raw={"origem": "cadastro manual"})
    lead_id, created = upsert_record(conn, rec)
    conn.commit()
    requalify(conn, lead_id)
    h._json({"id": lead_id, "criado": created})


def h_discover(h, conn, q, b):
    segs = [s for s in (b.get("segments") or []) if s in SEGMENTS]
    if not segs:
        raise ValueError("escolha ao menos um segmento")
    if b.get("region_type") not in ("df", "municipio_go", "ra"):
        raise ValueError("região inválida")
    job = h.jobs.submit("descoberta", _job_discover(b), **b)
    h._json(job)


def h_process(h, conn, q, b):
    job = h.jobs.submit("verificacao_lote", _job_process(b), **b)
    h._json(job)


def h_jobs(h, conn, q, b):
    h._json({"jobs": h.jobs.list()})


def h_job(h, conn, q, b, job_id):
    job = h.jobs.jobs.get(job_id)
    if not job:
        raise ValueError("tarefa não encontrada")
    h._json(job)


def h_settings(h, conn, q, b):
    h._json(public_settings(conn))


def h_settings_save(h, conn, q, b):
    h._json(update_settings(conn, b))


def h_pilots(h, conn, q, b):
    h._json({"amostras": pilot_mod.list_samples(conn)})


def h_pilot_create(h, conn, q, b):
    sample = pilot_mod.create_sample(conn, size=int(b.get("size") or 30), statuses=b.get("statuses") or None,
                                     seed=int(b["seed"]) if b.get("seed") not in (None, "") else None,
                                     name=b.get("name") or None)
    h._json(sample)


def h_pilot(h, conn, q, b, sample_id):
    h._json(pilot_mod.get_sample(conn, sample_id))


def h_pilot_report(h, conn, q, b, sample_id):
    h._json(pilot_mod.report(conn, sample_id, get_settings(conn)))


def h_pilot_review(h, conn, q, b, item_id):
    item = pilot_mod.review_item(conn, item_id, b.get("result"), b.get("found_url") or None,
                                 b.get("how_found") or None, b.get("note") or None)
    requalify(conn, item["lead_id"])
    h._json(item)


def h_pilot_regress(h, conn, q, b, sample_id):
    h._json(h.jobs.submit("regressao_piloto", _job_regress(sample_id), amostra=sample_id))


def h_export(h, conn, q, b):
    data = _csv_export(conn, q.get("view", "campanha")).encode("utf-8-sig")
    h.send_response(200)
    h.send_header("Content-Type", "text/csv; charset=utf-8")
    h.send_header("Content-Disposition", f"attachment; filename=leads-{q.get('view', 'campanha')}.csv")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


ROUTES = [
    ("GET", r"/api/meta", h_meta),
    ("GET", r"/api/health", h_health),
    ("GET", r"/api/leads", h_leads),
    ("POST", r"/api/leads", h_lead_create),
    ("GET", r"/api/leads/(\d+)", h_lead),
    ("POST", r"/api/leads/(\d+)/verify", h_verify),
    ("POST", r"/api/leads/(\d+)/enrich", h_enrich),
    ("POST", r"/api/leads/(\d+)/site-status", h_site_status),
    ("POST", r"/api/leads/(\d+)/site-status/clear", h_site_status_clear),
    ("POST", r"/api/leads/(\d+)/chain", h_chain),
    ("POST", r"/api/leads/(\d+)/pipeline", h_pipeline),
    ("POST", r"/api/leads/(\d+)/materials", h_material),
    ("POST", r"/api/leads/(\d+)/facts", h_fact),
    ("POST", r"/api/facts/(\d+)/reject", h_fact_reject),
    ("POST", r"/api/leads/(\d+)/messages/generate", h_msg_generate),
    ("POST", r"/api/messages/(\d+)/edit", h_msg_edit),
    ("POST", r"/api/messages/(\d+)/approve", h_msg_approve),
    ("POST", r"/api/discover", h_discover),
    ("POST", r"/api/jobs/process", h_process),
    ("GET", r"/api/jobs", h_jobs),
    ("GET", r"/api/jobs/(\d+)", h_job),
    ("GET", r"/api/settings", h_settings),
    ("POST", r"/api/settings", h_settings_save),
    ("GET", r"/api/pilot", h_pilots),
    ("POST", r"/api/pilot", h_pilot_create),
    ("GET", r"/api/pilot/(\d+)", h_pilot),
    ("GET", r"/api/pilot/(\d+)/report", h_pilot_report),
    ("POST", r"/api/pilot/items/(\d+)", h_pilot_review),
    ("POST", r"/api/pilot/(\d+)/regress", h_pilot_regress),
    ("GET", r"/api/export\.csv", h_export),
]


def make_server(db_path: str, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    connect(db_path).close()  # cria o banco se preciso
    handler = type("BoundHandler", (Handler,), {"db_path": db_path, "jobs": JobManager(db_path)})
    return ThreadingHTTPServer((host, port), handler)


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = make_server(db_path, host, port)
    print(f"Lead Finder em http://{host}:{port}  (Ctrl+C para sair)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
