from leadfinder import urls
from leadfinder.db import connect
from leadfinder.discovery.importers import cnpj_row_to_record, import_csv
from leadfinder.discovery.osm import OVERPASS_MIRRORS, build_query, element_to_record, run_overpass
from leadfinder.discovery.segments import segment_for_cnae
from leadfinder.leads import LeadRecord, get_lead, get_links, upsert_record
from leadfinder.net import FetchResult
from leadfinder.normalize import find_phones, name_in_text, norm_text, normalize_phone, phones_match


def test_telefones():
    assert normalize_phone("+55 (61) 99876-5432") == "61998765432"
    assert normalize_phone("061 3333-4444") == "6133334444"
    assert normalize_phone("123") is None
    assert phones_match("6198765432", "61998765432")  # celular antigo sem o 9
    found = find_phones("Ligue (61) 3333-4444 ou WhatsApp 61 9 9876-5432. https://wa.me/5561988887777")
    assert found == {"6133334444", "61998765432", "61988887777"}
    assert not find_phones("CNPJ 12.345.678/0001-99 CEP 70000-000")


def test_nome_exige_termos_distintivos():
    assert name_in_text("Pizzaria Bella Massa", norm_text("A Bella Massa tem a melhor pizza"))
    assert not name_in_text("Pizzaria Bella Massa", norm_text("Pizzaria do bairro"))


def test_classificacao_de_links():
    assert urls.classify_url("instagram.com/x").kind == urls.REDE_SOCIAL
    assert urls.classify_url("https://wa.me/5561999990000").kind == urls.WHATSAPP
    assert urls.classify_url("https://linktr.ee/x").kind == urls.AGREGADOR
    assert urls.classify_url("https://www.ifood.com.br/delivery/x").kind == urls.DELIVERY
    assert urls.classify_url("https://booksy.com/pt-br/x").kind == urls.AGENDAMENTO
    assert urls.classify_url("https://maps.app.goo.gl/x").kind == urls.DIRETORIO
    assert urls.classify_url("https://brasilia.cylex.com.br/x").kind == urls.DIRETORIO
    assert urls.classify_url("https://loja.wixsite.com/x").kind == urls.SITE_PLATAFORMA
    assert urls.classify_url("https://www.minhaempresa.com.br/").kind == urls.PROPRIO
    assert urls.registrable_domain("www.loja.minhaempresa.com.br") == "minhaempresa.com.br"
    assert urls.email_domain("contato@minhaempresa.com.br") == "minhaempresa.com.br"
    assert urls.email_domain("fulano@gmail.com") is None


def test_elemento_osm_vira_lead_com_links():
    el = {"type": "node", "id": 5, "lat": -15.8, "lon": -47.9,
          "tags": {"name": "Salão Bela", "shop": "hairdresser", "phone": "+55 61 98888-0000",
                   "contact:instagram": "@salaobela", "addr:suburb": "Taguatinga"}}
    rec = element_to_record(el, default_uf="DF")
    assert rec.segment == "beleza"
    assert rec.links == [("https://instagram.com/salaobela", "osm:contact:instagram")]
    assert element_to_record({"type": "node", "id": 1, "tags": {"shop": "bakery"}}) is None
    branded = element_to_record({"type": "node", "id": 2, "tags": {"name": "Rede", "shop": "clothes", "brand": "Rede"}})
    assert branded.is_chain


def test_prioridade_de_descoberta():
    conn = connect(":memory:")
    a, _ = upsert_record(conn, LeadRecord(name="A", source="osm", source_ref="n/1",
                                          links=[("https://instagram.com/a", "osm:instagram")]))
    b, _ = upsert_record(conn, LeadRecord(name="B", source="osm", source_ref="n/2",
                                          links=[("https://b.com.br", "osm:website")]))
    assert get_lead(conn, a)["discovery_priority"] == "alta"
    assert get_lead(conn, b)["discovery_priority"] == "baixa"


def test_deduplicacao_entre_fontes_por_telefone():
    conn = connect(":memory:")
    a, created = upsert_record(conn, LeadRecord(name="Padaria Pão Dourado", source="osm", source_ref="n/1",
                                                phone="61 3333-4444"))
    b, created2 = upsert_record(conn, LeadRecord(name="PAO DOURADO PADARIA", source="cnpj", source_ref="123",
                                                 phone="(61) 3333-4444", cnpj="12345678000199", email="x@y.com"))
    assert a == b and created and not created2
    lead = get_lead(conn, a)
    assert lead["cnpj"] == "12345678000199" and lead["email_source"] == "cnpj"
    c, created3 = upsert_record(conn, LeadRecord(name="Oficina Rápida", source="cnpj", source_ref="456",
                                                 phone="(61) 3333-4444"))
    assert c != a and created3  # mesmo telefone, nome sem termo em comum


def test_query_overpass():
    q = build_query(["beleza"], area_clause='area["ISO3166-2"="BR-DF"]->.a;')
    assert 'nwr["shop"="hairdresser"]["name"](area.a);' in q
    q2 = build_query(["otica"], around=(-15.8, -47.9, 2000))
    assert "(around:2000,-15.8,-47.9)" in q2


class _OverpassClient:
    def __init__(self, replies):
        self.replies = replies  # {url: [FetchResult, ...]}
        self.calls = []

    def post_json(self, url, payload=None, form=None, headers=None, timeout=None):
        self.calls.append(url)
        res = self.replies[url].pop(0)
        return res, ({"elements": []} if res.ok else None)


def test_overpass_repete_e_usa_espelho():
    a, b = OVERPASS_MIRRORS[0], OVERPASS_MIRRORS[1]
    client = _OverpassClient({
        a: [FetchResult(url=a, status=502, error="http"), FetchResult(url=a, error="conexao")],
        b: [FetchResult(url=b, status=200, ok=True)],
    })
    waits = []
    data, err = run_overpass(client, {"overpass_url": a}, "q", sleep=waits.append)
    assert data == {"elements": []} and err is None
    assert client.calls == [a, a, b] and len(waits) == 1


def test_overpass_remark_de_erro_nao_e_lista_vazia():
    a, b = OVERPASS_MIRRORS[0], OVERPASS_MIRRORS[1]
    timed_out = {"elements": [], "remark": 'runtime error: Query timed out in "query" at line 1'}

    class Client(_OverpassClient):
        def post_json(self, url, **kw):
            self.calls.append(url)
            return (FetchResult(url=url, status=200, ok=True),
                    timed_out if url == a else {"elements": [{"id": 1}]})

    client = Client({})
    data, err = run_overpass(client, {"overpass_url": a}, "q", sleep=lambda s: None)
    assert data == {"elements": [{"id": 1}]}
    assert client.calls == [a, a, b]


def test_overpass_nao_repete_erro_de_consulta():
    a = OVERPASS_MIRRORS[0]
    client = _OverpassClient({a: [FetchResult(url=a, status=400, error="http", error_detail="HTTP 400")]})
    client.replies.update({u: [FetchResult(url=u, status=400, error="http", error_detail="HTTP 400")]
                           for u in OVERPASS_MIRRORS[1:]})
    data, err = run_overpass(client, {"overpass_url": a}, "q", sleep=lambda s: None)
    assert data is None and "HTTP 400" in err
    assert len(client.calls) == len(OVERPASS_MIRRORS)


def test_cnpj_e_csv(tmp_path):
    row = ["12345678", "0001", "99", "1", "PADARIA BOM PAO", "02", "20200101", "00", "", "", "20200101",
           "1091102", "", "QUADRA", "QNM 10", "5", "", "CEILANDIA", "72210100", "DF", "9701", "61", "33334444",
           "", "", "", "", "", "", ""]
    rec = cnpj_row_to_record(row)
    assert rec.name == "Padaria Bom Pao" and rec.city == "Brasília" and rec.segment == "cafe_padaria"
    assert segment_for_cnae("9602501") == "beleza"
    csv_path = tmp_path / "lista.csv"
    csv_path.write_text("nome;telefone;instagram;bairro\nAteliê Linha Fina;61 99999-1111;@linhafina;Sobradinho\n;;;\n",
                        encoding="utf-8")
    conn = connect(":memory:")
    res = import_csv(conn, str(csv_path), default_city="Brasília", default_uf="DF")
    assert res.imported == 1 and res.skipped == 1
    lead = conn.execute("SELECT * FROM leads").fetchone()
    assert [l["kind"] for l in get_links(conn, lead["id"])] == ["rede_social"]
