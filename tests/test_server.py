import json
import threading
import urllib.error
import urllib.request

import pytest

from leadfinder.db import connect
from leadfinder.leads import LeadRecord, upsert_record
from leadfinder.server import make_server


@pytest.fixture()
def server(tmp_path):
    db = str(tmp_path / "t.db")
    conn = connect(db)
    upsert_record(conn, LeadRecord(name="Oficina do Zé", source="manual", source_ref="m1", segment="automotivo",
                                   phone="61 3333-4444", neighborhood="Guará", city="Brasília", uf="DF"))
    conn.commit()
    conn.close()
    httpd = make_server(db, "127.0.0.1", 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(base, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_paginas_e_api(server):
    with urllib.request.urlopen(server + "/") as r:
        assert b"Lead Finder" in r.read()
    code, meta = call(server, "GET", "/api/meta")
    assert code == 200 and "site_proprio" in meta["site_status"]
    code, data = call(server, "GET", "/api/leads?view=todos")
    assert code == 200 and len(data["leads"]) == 1
    lead_id = data["leads"][0]["id"]
    code, detail = call(server, "GET", f"/api/leads/{lead_id}")
    assert detail["site"]["status"] == "pendente"
    assert any(l["label"] == "Google" for l in detail["site"]["busca_manual"])


def test_correcao_manual_pela_api(server):
    _, data = call(server, "GET", "/api/leads?view=todos")
    lead_id = data["leads"][0]["id"]
    code, detail = call(server, "POST", f"/api/leads/{lead_id}/site-status",
                        {"status": "nao_localizado", "source": "empresa", "note": "confirmado por telefone"})
    assert code == 200
    assert detail["site"]["status"] == "nao_localizado"
    assert detail["qualificacao"]["na_campanha"] is True
    code, err = call(server, "POST", f"/api/leads/{lead_id}/site-status", {"status": "site_proprio"})
    assert code == 400 and "URL" in err["erro"]


def test_post_exige_json_e_mesma_origem(server):
    req = urllib.request.Request(server + "/api/settings", data=b"a=1", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 415
    code, _ = call(server, "POST", "/api/settings", {"vendedor_nome": "X"}, headers={"Origin": "http://evil.example"})
    assert code == 403


def test_configuracoes_mascaram_chave(server):
    call(server, "POST", "/api/settings", {"brave_api_key": "segredo", "vendedor_nome": "Ana"})
    _, s = call(server, "GET", "/api/settings")
    assert s["brave_api_key"] == "••••••" and s["vendedor_nome"] == "Ana"
    call(server, "POST", "/api/settings", {"brave_api_key": "••••••"})
    conn_settings = call(server, "GET", "/api/settings")[1]
    assert conn_settings["brave_api_key"] == "••••••"
