from leadfinder import statuses as st
from leadfinder.db import connect
from leadfinder.leads import LeadRecord, get_lead, upsert_record
from leadfinder.verification.manual import clear_manual_status, set_manual_status
from leadfinder.verification.search import NullProvider
from leadfinder.verification.verifier import VerifyContext, verify_lead

from tests.fakes import FakeClient, FakeSearch, html

SETTINGS = {"busca_max_candidatos": 5, "busca_min_consultas": 2, "adivinhar_dominio": False}


def make_lead(conn, **kw):
    base = dict(name="Pizzaria Bella Massa", source="osm", source_ref=f"node/{len(kw)}{kw.get('name', '')}",
                phone="(61) 99876-5432", neighborhood="Taguatinga", city="Brasília", uf="DF")
    base.update(kw)
    lead_id, _ = upsert_record(conn, LeadRecord(**base))
    conn.commit()
    return lead_id


def run(conn, lead_id, client, provider=None, settings=None):
    ctx = VerifyContext(client=client, provider=provider or NullProvider(), settings=settings or SETTINGS)
    return verify_lead(conn, lead_id, ctx)


def checks(conn, run_id):
    return [dict(r) for r in conn.execute("SELECT * FROM verification_checks WHERE run_id=?", (run_id,))]


def test_site_da_fonte_com_telefone_confirmado():
    conn = connect(":memory:")
    lid = make_lead(conn, links=[("https://bellamassa.com.br", "osm:website")])
    client = FakeClient({"https://bellamassa.com.br": html("Bella Massa", "Pedidos (61) 99876-5432")})
    res = run(conn, lid, client)
    assert res["status"] == st.SITE_PROPRIO
    assert res["confidence"] >= 0.9
    assert get_lead(conn, lid)["site_url_auto"].startswith("https://bellamassa.com.br")


def test_sem_provedor_de_busca_fica_inconclusiva():
    conn = connect(":memory:")
    lid = make_lead(conn, links=[("https://instagram.com/bellamassa", "osm:contact:instagram")])
    res = run(conn, lid, FakeClient())
    assert res["status"] == st.INCONCLUSIVA
    assert any(c["check_type"] == "busca" and c["outcome"] == "nao_configurado" for c in checks(conn, res["run_id"]))


def test_busca_suficiente_com_instagram_vira_somente_redes():
    conn = connect(":memory:")
    lid = make_lead(conn, links=[("https://instagram.com/bellamassa", "osm:contact:instagram")])
    search = FakeSearch({"Bella": [("https://www.ifood.com.br/delivery/x", "Bella Massa iFood", "")]})
    res = run(conn, lid, FakeClient(), provider=search)
    assert res["status"] == st.SOMENTE_REDES
    assert 0.6 <= res["confidence"] <= 0.9
    assert len(search.queries) == 3  # nome+cidade, nome+bairro, telefone


def test_sem_presenca_vira_nao_localizado():
    conn = connect(":memory:")
    lid = make_lead(conn)
    res = run(conn, lid, FakeClient(), provider=FakeSearch())
    assert res["status"] == st.NAO_LOCALIZADO


def test_falha_de_busca_nao_significa_ausencia_de_site():
    conn = connect(":memory:")
    lid = make_lead(conn)
    res = run(conn, lid, FakeClient(), provider=FakeSearch(fail=True))
    assert res["status"] == st.INCONCLUSIVA
    assert "Falha de consulta" in res["reason"]


def test_dominio_da_fonte_fora_do_ar_e_possivel_indisponivel():
    conn = connect(":memory:")
    lid = make_lead(conn, links=[("http://bellamassa-antigo.com.br", "osm:website")])
    res = run(conn, lid, FakeClient(), provider=FakeSearch())
    assert res["status"] == st.POSSIVEL_INDISPONIVEL


def test_dominio_estacionado_e_possivel_indisponivel():
    conn = connect(":memory:")
    lid = make_lead(conn, links=[("https://bellamassa.com.br", "osm:website")])
    client = FakeClient({"https://bellamassa.com.br": html("Domínio", "This domain is for sale")})
    res = run(conn, lid, client, provider=FakeSearch())
    assert res["status"] == st.POSSIVEL_INDISPONIVEL


def test_candidato_da_busca_so_com_nome_nao_e_associado():
    conn = connect(":memory:")
    lid = make_lead(conn)
    search = FakeSearch({"Bella": [("https://pizzariabellamassa.com.br", "Pizzaria Bella Massa", "")]})
    client = FakeClient({"https://pizzariabellamassa.com.br": html("Pizzaria Bella Massa", "A melhor pizza de Brasília")})
    res = run(conn, lid, client, provider=search)
    assert res["status"] == st.INCONCLUSIVA  # nome + região, mas sem telefone/endereço


def test_homonimo_em_outra_cidade_e_descartado():
    conn = connect(":memory:")
    lid = make_lead(conn)
    search = FakeSearch({"Bella": [("https://bellamassapoa.com.br", "Pizzaria Bella Massa", "")]})
    client = FakeClient({"https://bellamassapoa.com.br": html("Pizzaria Bella Massa", "Porto Alegre - RS. Fone (51) 3333-4444")})
    res = run(conn, lid, client, provider=search)
    assert res["status"] == st.NAO_LOCALIZADO
    ev = res["summary"]["avaliacoes"][0]
    assert ev["resultado"] == "homonimo"


def test_candidato_da_busca_com_telefone_confirma_site():
    conn = connect(":memory:")
    lid = make_lead(conn)
    search = FakeSearch({"Bella": [("https://bellamassadf.com.br/cardapio", "Cardápio", "")]})
    client = FakeClient({
        "https://bellamassadf.com.br/cardapio": html("Cardápio", "Pizzas"),
        "https://bellamassadf.com.br/": html("Bella Massa", "WhatsApp: 61 99876-5432"),
    })
    res = run(conn, lid, client, provider=search)
    assert res["status"] == st.SITE_PROPRIO


def test_diretorio_com_telefone_nao_e_site_proprio():
    conn = connect(":memory:")
    lid = make_lead(conn)
    phones = " ".join(f"(61) 3{i}{i}{i}-1{i}{i}{i}" for i in range(1, 9))
    search = FakeSearch({"Bella": [("https://guialocalbsb.com.br/pizzarias", "Pizzarias", "")]})
    client = FakeClient({"https://guialocalbsb.com.br/pizzarias": html(
        "Pizzarias em Brasília", f"Pizzaria Bella Massa (61) 99876-5432 {phones}")})
    res = run(conn, lid, client, provider=search)
    assert res["status"] == st.NAO_LOCALIZADO
    assert res["summary"]["avaliacoes"][0]["resultado"] == "diretorio"


def test_linktree_da_empresa_leva_ao_site():
    conn = connect(":memory:")
    lid = make_lead(conn, links=[("https://linktr.ee/bellamassa", "osm:website")])
    client = FakeClient({
        "https://linktr.ee/bellamassa": html("Bella", '<a href="https://bellamassa.com.br">Site</a>'
                                                       '<a href="https://wa.me/5561998765432">Zap</a>'),
        "https://bellamassa.com.br": html("Bella Massa", "Bem-vindo"),
    })
    res = run(conn, lid, client, provider=FakeSearch())
    assert res["status"] == st.SITE_PROPRIO
    assert res["summary"]["agregador_lido"] is True


def test_robots_bloqueado_nao_e_contornado():
    conn = connect(":memory:")
    lid = make_lead(conn, links=[("https://bellamassa.com.br", "osm:website")])
    client = FakeClient(robots_block={"https://bellamassa.com.br"})
    res = run(conn, lid, client, provider=FakeSearch())
    assert res["status"] == st.SITE_PROPRIO  # existe e veio da fonte, mas não foi lido
    assert res["confidence"] == 0.6
    assert client.requests.count("https://bellamassa.com.br") == 1


def test_email_do_cnpj_pode_ser_do_contador():
    conn = connect(":memory:")
    lid = make_lead(conn, email="fiscal@contabilxyz.com.br", email_source="cnpj", source="cnpj",
                    source_ref="12345678000199")
    client = FakeClient({"https://contabilxyz.com.br": html("Contábil XYZ", "Escritório de contabilidade (61) 3333-0000")})
    res = run(conn, lid, client, provider=FakeSearch())
    assert res["status"] == st.NAO_LOCALIZADO


def test_correcao_humana_nao_e_sobrescrita():
    conn = connect(":memory:")
    lid = make_lead(conn)
    set_manual_status(conn, lid, st.SITE_PROPRIO, url="https://bellamassa.com.br", source="pesquisa_manual",
                      note="achei no Google")
    run(conn, lid, FakeClient(), provider=FakeSearch())
    lead = get_lead(conn, lid)
    assert lead["site_status_manual"] == st.SITE_PROPRIO
    assert lead["site_status_auto"] != st.SITE_PROPRIO
    assert st.effective_status(lead) == st.SITE_PROPRIO
    hist = [dict(r) for r in conn.execute("SELECT * FROM status_history WHERE lead_id=? ORDER BY id", (lid,))]
    assert any(h["actor"] == "humano" for h in hist)
    assert any("DIVERGÊNCIA" in (h["note"] or "") for h in hist)
    clear_manual_status(conn, lid)
    assert st.effective_status(get_lead(conn, lid)) == get_lead(conn, lid)["site_status_auto"]
