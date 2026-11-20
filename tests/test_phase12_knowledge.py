"""Fase 12 — dados estruturados do salão: categorias, duração, profissionais e formas de preço,
sempre pelo fluxo rascunho → aprovação → revogação, com conflitos e compatibilidade."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from panel_support import Panel, add_user, create_panel_app, panel_settings
from rocha_reis_agent.knowledge import Duration
from rocha_reis_agent.knowledge_catalog import FactRevision, KnowledgeScope, PriceFact
from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
from test_phase11_scenarios import Salon
from whatsapp_support import catalog


@pytest.fixture
def admin(tmp_path):
    app = create_panel_app(panel_settings(tmp_path, knowledge_source="database"))
    knowledge = SQLiteKnowledgeRepository(app.state.conversation_repository)
    knowledge.initialize(KnowledgeScope.SYNTHETIC)
    knowledge.import_catalog(catalog(), author="fixture-sintetica")
    add_user(app, "admin.teste", "ADMIN")
    add_user(app, "ana.teste", "OPERATOR")
    panel, operator = Panel(app), Panel(app)
    panel.login("admin.teste")
    operator.login("ana.teste")
    panel.operator = operator
    return panel


def draft(panel, kind, key, fields, expected=None):
    response = panel.post("/knowledge/drafts", {"kind": kind, "key": key, "fields": fields,
                                                "source": "Dado FICTÍCIO de teste", "expected_version": expected})
    return response


def approve(panel, fact_id):
    latest = panel.get(f"/knowledge/history?fact_id={fact_id}").json()[-1]
    return panel.post("/knowledge/approve", {"fact_id": fact_id, "version": latest["version"]})


def revoke(panel, fact_id):
    latest = panel.get(f"/knowledge/history?fact_id={fact_id}").json()[-1]
    return panel.post("/knowledge/revoke", {"fact_id": fact_id, "version": latest["version"]})


def preview(panel, question):
    return panel.post("/knowledge/preview", {"question": question}).json()


def current_version(panel, fact_id):
    return panel.get(f"/knowledge/history?fact_id={fact_id}").json()[-1]["version"]


def setup_structure(panel):
    assert draft(panel, "category", "cabelo", {"name": "Cabelo (fictícia)"}).status_code == 201
    assert approve(panel, "category:cabelo").status_code == 200
    assert draft(panel, "professional", "pro-a", {"name": "Profissional Fictícia A"}).status_code == 201
    assert approve(panel, "professional:pro-a").status_code == 200


# 1, 2 — categorias
def test_valid_category_and_duplicate_name_is_blocked(admin):
    setup_structure(admin)
    assert draft(admin, "category", "cabelos", {"name": "  cabelo (FICTÍCIA) "}).status_code == 201
    response = approve(admin, "category:cabelos")
    assert response.status_code == 409 and "category:cabelo" in response.json()["detail"]
    assert draft(admin, "category", "x", {"name": ""}).status_code == 422
    assert draft(admin, "category", "Inválido!", {"name": "Algo"}).status_code == 422


# 3 — serviço sem categoria continua válido e o texto antigo é preservado
def test_service_without_new_fields_renders_exactly_as_before(admin):
    reply = preview(admin, "Vocês fazem corte?")["reply"]
    assert reply == "[SIMULAÇÃO — DADOS FICTÍCIOS] Corte de teste: Serviço fictício para validar o simulador."


# 4, 5, 6 — duração
@pytest.mark.parametrize("low,high,expected", [("45", "", "Duração estimada: cerca de 45 min (pode variar)."),
                                               ("40", "90", "Duração estimada: entre 40 min e 1h30 (pode variar)."),
                                               ("120", "", "Duração estimada: cerca de 2h (pode variar).")])
def test_fixed_and_range_durations_are_estimates(admin, low, high, expected):
    assert draft(admin, "service", "corte", {"name": "Corte de teste", "aliases": "corte, corte de cabelo",
                                             "description": "Serviço fictício para validar o simulador.",
                                             "duration_min": low, "duration_max": high},
                 expected=current_version(admin, "service:corte")).status_code == 201
    assert approve(admin, "service:corte").status_code == 200
    reply = preview(admin, "Vocês fazem corte?")["reply"]
    assert expected in reply and "exat" not in reply.lower()


@pytest.mark.parametrize("low,high", [("0", ""), ("4", ""), ("721", ""), ("60", "60"), ("60", "30"), ("", "40"),
                                      ("1.5", ""), ("-10", ""), ("quarenta", "")])
def test_invalid_durations_are_refused_by_the_server(admin, low, high):
    response = draft(admin, "service", "novo", {"name": "Serviço fictício novo", "duration_min": low, "duration_max": high})
    assert response.status_code == 422


def test_duration_model_rejects_non_integer_and_inverted_ranges():
    with pytest.raises(ValidationError):
        Duration(min_minutes=60, max_minutes=30)
    with pytest.raises(ValidationError):
        Duration(min_minutes="45")  # type: ignore[arg-type]


# 7, 8, 9 — profissionais
def test_professional_association_inactive_and_change(admin):
    setup_structure(admin)
    assert draft(admin, "professional", "pro-b", {"name": "Profissional Fictícia B"}).status_code == 201
    assert approve(admin, "professional:pro-b").status_code == 200
    assert draft(admin, "service", "escova", {"name": "Escova de teste", "aliases": "escova", "category": "cabelo",
                                              "professionals": "pro-a, pro-b"},
                 expected=current_version(admin, "service:escova")).status_code == 201
    assert approve(admin, "service:escova").status_code == 200
    reply = preview(admin, "Qual profissional faz escova?")["reply"]
    assert "Escova de teste é realizado por: Profissional Fictícia A, Profissional Fictícia B." in reply
    # Desativar B (nova versão aprovada) tira B das respostas, sem tocar no serviço.
    assert draft(admin, "professional", "pro-b", {"name": "Profissional Fictícia B", "active": "false"},
                 expected=current_version(admin, "professional:pro-b")).status_code == 201
    assert approve(admin, "professional:pro-b").status_code == 200
    reply = preview(admin, "Qual profissional faz escova?")["reply"]
    assert "Fictícia A" in reply and "Fictícia B" not in reply
    pending = [p["detail"] for p in admin.get("/knowledge").json()["pending"]]
    assert any("pro-b" in d and "inativo" in d for d in pending)
    # Profissional inativo não pode ser associado numa nova aprovação de serviço.
    assert draft(admin, "service", "corte", {"name": "Corte de teste", "aliases": "corte", "professionals": "pro-b"},
                 expected=current_version(admin, "service:corte")).status_code == 201
    response = approve(admin, "service:corte")
    assert response.status_code == 409 and "pro-b" in response.json()["detail"]


def test_unknown_references_are_refused_at_draft(admin):
    assert draft(admin, "service", "x", {"name": "Serviço X", "category": "inexistente"}).status_code == 422
    assert draft(admin, "service", "x", {"name": "Serviço X", "professionals": "ninguem"}).status_code == 422


def test_professional_is_not_a_panel_account(admin):
    setup_structure(admin)
    logins = [u["login"] for u in admin.get("/users").json()]
    assert all("pro-a" not in login and "Fictícia A" not in login for login in logins)
    assert len(logins) == 2


# 10, 11, 12, 14 — aprovação, revogação, histórico, rascunho não usado
def test_unapproved_category_is_not_used_and_revocation_removes_it(admin):
    assert draft(admin, "category", "cabelo", {"name": "Cabelo (fictícia)"}).status_code == 201
    # Serviço não pode ser APROVADO apontando categoria ainda em rascunho.
    assert draft(admin, "service", "corte", {"name": "Corte de teste", "aliases": "corte", "category": "cabelo"},
                 expected=current_version(admin, "service:corte")).status_code == 201
    response = approve(admin, "service:corte")
    assert response.status_code == 409 and "cabelo" in response.json()["detail"]
    assert approve(admin, "category:cabelo").status_code == 200
    assert approve(admin, "service:corte").status_code == 200
    assert "Categoria: Cabelo (fictícia)." in preview(admin, "Vocês fazem corte?")["reply"]
    assert revoke(admin, "category:cabelo").status_code == 200
    assert "Categoria" not in preview(admin, "Vocês fazem corte?")["reply"]
    history = admin.get("/knowledge/history?fact_id=category:cabelo").json()
    assert [h["status"] for h in history] == ["DRAFT", "APPROVED", "ARCHIVED"]
    actions = [e["action"] for e in admin.get("/audit").json()]
    assert {"knowledge_drafted", "knowledge_approved", "knowledge_revoked"} <= set(actions)


def test_catalog_is_grouped_by_active_category(admin):
    setup_structure(admin)
    assert draft(admin, "service", "corte", {"name": "Corte de teste", "aliases": "corte, corte de cabelo",
                                             "category": "cabelo"},
                 expected=current_version(admin, "service:corte")).status_code == 201
    approve(admin, "service:corte")
    reply = preview(admin, "Quais serviços vocês têm?")["reply"]
    assert "Cabelo (fictícia): Corte de teste" in reply and "Outros serviços: Escova de teste" in reply


# 13 — conflitos de nome de profissional
def test_duplicate_professional_name_is_blocked(admin):
    setup_structure(admin)
    draft(admin, "professional", "pro-a2", {"name": "profissional fictícia a"})
    assert approve(admin, "professional:pro-a2").status_code == 409


# Formas de preço
@pytest.mark.parametrize("fields,expected", [
    ({"pricing": "fixed", "amount": "45,00"}, "Corte de teste: R$ 45,00."),
    ({"pricing": "starting", "amount": "45"}, "Corte de teste: a partir de R$ 45,00 (o valor final pode variar)."),
    ({"pricing": "range", "amount": "45", "amount_max": "80"}, "Corte de teste: de R$ 45,00 a R$ 80,00."),
    ({"pricing": "on_evaluation"}, "Corte de teste: o valor é definido após avaliação presencial."),
])
def test_price_kinds_are_rendered_honestly(admin, fields, expected):
    assert draft(admin, "price", "corte", fields, expected=current_version(admin, "price:corte")).status_code == 201
    assert approve(admin, "price:corte").status_code == 200
    reply = preview(admin, "Quanto custa o corte?")["reply"]
    assert expected in reply
    assert not any("Sem preço aprovado: Corte" in p["detail"] for p in admin.get("/knowledge").json()["pending"])


@pytest.mark.parametrize("fields", [{"pricing": "range", "amount": "80", "amount_max": "45"},
                                    {"pricing": "range", "amount": "45"}, {"pricing": "starting"},
                                    {"pricing": "inventado", "amount": "1"}, {"pricing": "fixed", "amount": "abc"}])
def test_inconsistent_prices_are_refused(admin, fields):
    assert draft(admin, "price", "corte", fields, expected=current_version(admin, "price:corte")).status_code == 422


def test_old_price_documents_without_pricing_remain_fixed():
    old = {"kind": "price", "service_id": "corte", "amount": "45.00", "note": None}
    fact = PriceFact.model_validate(old)
    assert fact.pricing == "fixed" and fact.amount == Decimal("45.00")
    revision = catalog().revisions[0].model_dump(mode="json")
    assert FactRevision.model_validate_json(json.dumps(revision)).version == 1


# RBAC no backend (itens 29, 30)
def test_operator_cannot_create_or_approve_structured_data(admin):
    operator = admin.operator
    assert operator.post("/knowledge/drafts", {"kind": "category", "key": "x", "fields": {"name": "X"},
                                               "source": "y", "expected_version": None}).status_code == 403
    setup_structure(admin)
    draft(admin, "category", "z", {"name": "Z fictícia"})
    assert operator.post("/knowledge/approve", {"fact_id": "category:z", "version": 1}).status_code == 403
    assert operator.get("/knowledge").status_code == 403


def test_agent_uses_only_approved_structured_data_end_to_end(tmp_path):
    salon = Salon(tmp_path)
    admin = salon.panels["admin.teste"]
    setup_structure(admin)
    draft(admin, "service", "corte", {"name": "Corte de teste", "aliases": "corte, corte de cabelo",
                                      "description": "Serviço fictício para validar o simulador.",
                                      "category": "cabelo", "duration_min": "40", "duration_max": "60",
                                      "professionals": "pro-a"}, expected=1)
    salon.say("5511900000301", "Vocês fazem corte?")
    assert "Duração" not in salon.last_reply()  # rascunho: nada novo e o serviço em revisão some
    approve(admin, "service:corte")
    salon.say("5511900000302", "Vocês fazem corte?")
    reply = salon.last_reply()
    assert "Categoria: Cabelo (fictícia)." in reply and "entre 40 min e 1h" in reply
    assert "Realizado por: Profissional Fictícia A." in reply
