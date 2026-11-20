"""Somente orientação por link configurado, sem agendamento ou acesso de rede."""

import pytest

from rocha_reis_agent.domain.contracts import Intent
from rocha_reis_agent.memory import InMemoryKnowledgeRepository


@pytest.mark.parametrize("text", [
    "Quero agendar um corte para sábado.",
    "Tem horário disponível amanhã?",
    "Você marcou?",
    "Quero cancelar minha reserva.",
    "Preciso remarcar meu corte.",
])
def test_booking_only_sends_configured_link_and_denies_operations(send, knowledge, text):
    result = send(text)
    assert Intent.BOOKING in result.intents
    assert knowledge.booksy_url in result.reply
    assert "Não consultei disponibilidade" in result.reply
    assert "nem realizei ou confirmei reserva" in result.reply
    assert "SIMULAÇÃO" in result.reply


@pytest.mark.parametrize("url", [
    None, "", "javascript:alert(1)", "http://example.invalid/profile",
    "https://evil.example/profile", "https://booksy.com.evil.example/profile",
    "https://booksy.com@evil.example/profile", "https://example.invalid/",
    "https://example.invalid/profile#fragment", "https://example.invalid:444/profile",
    "https://example.invalid/a b", "https://example.invalid/\\profile",
])
def test_missing_or_invalid_link_never_leaks_into_response(agent, send, knowledge, url):
    agent.knowledge = InMemoryKnowledgeRepository(knowledge.model_copy(update={"booksy_url": url}))
    result = send("Quero agendar.")
    assert "não há link Booksy válido" in result.reply
    assert "https://" not in result.reply
    assert "javascript:" not in result.reply
    assert "humano" in result.reply


def test_synthetic_link_is_not_accepted_as_official(knowledge):
    assert knowledge.booking_link() == knowledge.booksy_url
    assert knowledge.model_copy(update={"synthetic": False}).booking_link() is None
