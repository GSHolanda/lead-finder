import pytest

from leadfinder import statuses as st
from leadfinder.db import connect
from leadfinder.enrichment import add_material, enrich_lead, get_facts, reject_fact
from leadfinder.leads import LeadRecord, get_lead, upsert_record
from leadfinder.outreach import QUESTION, approve, check_guardrails, generate_message, save_edit
from leadfinder.pilot import create_sample, report, review_item, wilson_interval
from leadfinder.qualification import qualify_lead
from leadfinder.verification.manual import set_manual_status
from leadfinder.verification.verifier import VerifyContext, verify_lead

from tests.fakes import FakeClient, FakeOllama, FakeSearch

SETTINGS = {"vendedor_nome": "Ana", "vendedor_cidade": "Brasília", "servico_descricao": "desenvolvimento de sites",
            "segmentos_alvo": [], "busca_min_consultas": 2, "adivinhar_dominio": False,
            "piloto_limite_erro": 0.10, "piloto_minimo_revisados": 3}


def osm_lead(conn, ref="node/1", **tags):
    base_tags = {"name": "Studio Flor de Lis", "shop": "beauty", "opening_hours": "Mo-Sa 09:00-19:00",
                 "contact:instagram": "studioflordelis"}
    base_tags.update(tags)
    rec = LeadRecord(name=base_tags["name"], source="osm", source_ref=ref, segment="beleza",
                     phone="61 98888-7777", street="CLS 405 Bloco B", housenumber="Loja 12",
                     neighborhood="Asa Sul", city="Brasília", uf="DF",
                     links=[("https://instagram.com/studioflordelis", "osm:contact:instagram")],
                     raw={"type": "node", "id": 1, "tags": base_tags})
    lid, _ = upsert_record(conn, rec)
    conn.commit()
    return lid


def verified_no_site(conn, lid):
    verify_lead(conn, lid, VerifyContext(client=FakeClient(), provider=FakeSearch(), settings=SETTINGS))


def test_enriquecimento_descarta_evidencia_inventada():
    conn = connect(":memory:")
    lid = osm_lead(conn)
    add_material(conn, lid, "Design de sobrancelhas e alongamento de cílios. Agende pelo WhatsApp.",
                 title="Bio do Instagram", source_url="https://instagram.com/studioflordelis")
    ollama = FakeOllama({
        "servicos": [{"valor": "design de sobrancelhas", "evidencia": "Design de sobrancelhas"},
                     {"valor": "alongamento de cílios", "evidencia": "alongamento de cílios"},
                     {"valor": "manicure", "evidencia": "fazemos manicure e pedicure"}],  # não está no texto
        "produtos": [], "regiao_atendida": [], "formas_contato": [
            {"valor": "agendamento pelo WhatsApp", "evidencia": "Agende pelo WhatsApp"}], "horario": [],
    })
    res = enrich_lead(conn, lid, ollama)
    values = {f["value"] for f in get_facts(conn, lid)}
    assert "design de sobrancelhas" in values
    assert "manicure" not in values
    assert any("descartado" in n for n in res["notas"])
    assert "Mo-Sa 09:00-19:00" in values  # campo estruturado do OSM


def test_fato_rejeitado_pelo_usuario_nao_volta():
    conn = connect(":memory:")
    lid = osm_lead(conn)
    enrich_lead(conn, lid, None)
    fact = next(f for f in get_facts(conn, lid) if f["kind"] == "horario")
    reject_fact(conn, fact["id"])
    enrich_lead(conn, lid, None)
    assert not any(f["kind"] == "horario" for f in get_facts(conn, lid))


def test_qualificacao_separa_quem_tem_site():
    conn = connect(":memory:")
    lid = osm_lead(conn)
    verified_no_site(conn, lid)
    enrich_lead(conn, lid, None)
    q = qualify_lead(conn, lid, SETTINGS)
    assert q["in_campaign"] is True
    assert q["score"] > 0
    keys = {b["chave"] for b in q["benefits"]}
    assert {"localizacao", "whatsapp"} <= keys
    set_manual_status(conn, lid, st.SITE_PROPRIO, url="https://flordelis.com.br", source="pesquisa_manual")
    q2 = qualify_lead(conn, lid, SETTINGS)
    assert q2["in_campaign"] is False
    assert "lista separada" in q2["exclusion"]


def test_rede_franquia_fica_fora_da_campanha():
    conn = connect(":memory:")
    rec = LeadRecord(name="Rede X", source="osm", source_ref="node/9", segment="beleza", phone="61 98888-1111",
                     is_chain=True, chain_reason="tag brand")
    lid, _ = upsert_record(conn, rec)
    verified_no_site(conn, lid)
    assert qualify_lead(conn, lid, SETTINGS)["in_campaign"] is False


def test_mensagem_padrao_pergunta_e_passa_nas_regras():
    conn = connect(":memory:")
    lid = osm_lead(conn)
    verified_no_site(conn, lid)
    enrich_lead(conn, lid, None)
    msg = generate_message(conn, lid, SETTINGS, ollama=None)
    assert QUESTION in msg["body"]
    assert msg["status"] == "rascunho"
    assert not msg["guardrails"]["bloqueios"]
    approved = approve(conn, msg["id"])
    assert approved["status"] == "aprovada"
    assert get_lead(conn, lid)["pipeline_status"] == "mensagem_aprovada"


def test_mensagem_do_modelo_que_afirma_ausencia_e_descartada():
    conn = connect(":memory:")
    lid = osm_lead(conn)
    verified_no_site(conn, lid)
    ollama = FakeOllama("Olá! Vi que vocês não têm site e estão perdendo clientes. Preparei uma proposta.")
    msg = generate_message(conn, lid, SETTINGS, ollama=ollama)
    assert msg["generator"] == "modelo_padrao"
    assert any("descartado" in a for a in msg["guardrails"]["avisos"])


def test_edicao_humana_com_promessa_nao_pode_ser_aprovada():
    conn = connect(":memory:")
    lid = osm_lead(conn)
    verified_no_site(conn, lid)
    msg = generate_message(conn, lid, SETTINGS)
    edited = save_edit(conn, msg["id"], msg["body"] + "\nGaranto a primeira página do Google!")
    assert edited["status"] == "bloqueada"
    assert edited["version"] == 2
    with pytest.raises(ValueError):
        approve(conn, edited["id"])


def test_ausencia_confirmada_pela_empresa_permite_afirmar():
    report_ = check_guardrails("Como vocês comentaram que ainda não têm site, posso ajudar.", True)
    assert not report_["bloqueios"]
    report2 = check_guardrails("Como vocês ainda não têm site, posso ajudar.", False)
    assert report2["bloqueios"]


def test_mensagem_nao_aprovada_sem_verificacao_suficiente():
    conn = connect(":memory:")
    lid = osm_lead(conn)
    msg = generate_message(conn, lid, SETTINGS)  # status ainda pendente
    with pytest.raises(ValueError):
        approve(conn, msg["id"])


def test_wilson():
    lo, hi = wilson_interval(0, 30)
    assert lo == 0 and 0.10 < hi < 0.13
    lo, hi = wilson_interval(3, 30)
    assert 0.03 < lo < 0.04 and 0.25 < hi < 0.27


def test_piloto_mede_sites_nao_encontrados_e_aplica_correcao():
    conn = connect(":memory:")
    ids = [osm_lead(conn, ref=f"node/{i}", name=f"Studio {n}") for i, n in enumerate(["Alfa", "Beta", "Gama", "Delta"])]
    for lid in ids:
        verified_no_site(conn, lid)
    sample = create_sample(conn, size=4, seed=42)
    items = sample["items"]
    assert len(items) == 4
    review_item(conn, items[0]["id"], "tinha_site", found_url="https://studioalfa.com.br", how_found="bio_instagram")
    review_item(conn, items[1]["id"], "sem_site_confirmado")
    review_item(conn, items[2]["id"], "sem_site_confirmado")
    review_item(conn, items[3]["id"], "inconclusivo")
    rep = report(conn, sample["id"], SETTINGS)
    assert rep["revisados"] == 3 and rep["tinham_site"] == 1
    assert rep["inconclusivos"] == 1
    assert rep["decisao"]["pode_escalar"] is False
    assert rep["erros"][0]["diagnostico"] == "bio_instagram"
    lead = get_lead(conn, items[0]["lead_id"])
    assert lead["site_status_manual"] == st.SITE_PROPRIO and lead["site_manual_source"] == "piloto"
    with pytest.raises(ValueError):
        create_sample(conn, size=4)  # todos já amostrados
