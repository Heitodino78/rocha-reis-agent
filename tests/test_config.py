"""Configuração válida, precedência e falhas sem vazamento de valores."""

import traceback
from pathlib import Path

import pytest

from rocha_reis_agent.config import ConfigurationError, load_settings
from rocha_reis_agent.main import create_app


def test_defaults_do_not_require_dotenv_or_credentials() -> None:
    settings = load_settings()
    assert settings.app_env == "local"
    assert settings.app_name == "rocha-reis-agent"


def test_dotenv_is_read_and_environment_takes_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "APP_ENV=local\nAPP_NAME=synthetic-service\n", encoding="utf-8"
    )
    monkeypatch.setenv("APP_ENV", "test")

    app = create_app()
    assert app.state.settings.app_env == "test"
    assert app.title == "synthetic-service"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ENV", "SYNTHETIC_INVALID_ENV"),
        ("APP_ENV", ""),
        ("APP_NAME", "SYNTHETIC_INVALID_NAME!"),
        ("APP_NAME", ""),
        ("APP_NAME", "x" * 65),
    ],
)
def test_invalid_configuration_prevents_startup_without_exposing_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as caught:
        create_app()

    rendered = "".join(traceback.format_exception(caught.value))
    assert "Configuração inválida" in rendered
    if value:
        assert value not in rendered
    assert "ValidationError" not in rendered


def test_unknown_dotenv_key_is_rejected_safely(tmp_path: Path) -> None:
    marker = "SYNTHETIC_UNKNOWN_VALUE"
    (tmp_path / ".env").write_text(f"APP_ENVI={marker}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    assert marker not in "".join(traceback.format_exception(caught.value))


def test_unreadable_dotenv_is_rejected_safely(tmp_path: Path) -> None:
    (tmp_path / ".env").write_bytes(b"APP_ENV=\xff")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_dotenv_example_is_valid(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[1] / ".env.example"
    (tmp_path / ".env").write_bytes(example.read_bytes())
    assert load_settings().app_env == "local"


@pytest.mark.parametrize("name,value", [
    ("SIM_MAX_MESSAGE_CHARS", "0"), ("SIM_MAX_MESSAGE_CHARS", "2001"),
    ("SIM_MAX_HISTORY_ENTRIES", "1001"), ("SIM_MAX_EVENTS_PER_SESSION", "10001"),
    ("SIM_MAX_CONVERSATIONS", "1001"), ("SIM_CONTEXT_TURNS", "101"),
    ("SIM_CONTEXT_TURNS", "SYNTHETIC_PRIVATE_INVALID_LIMIT"),
])
def test_invalid_simulator_limits_prevent_startup_safely(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert value not in str(caught.value)
    if value.startswith("SYNTHETIC_"):
        assert value not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("name,value",[
    ("AI_PROVIDER","real-provider-private-marker"),
    ("AI_HISTORY_LIMIT","11"),("AI_TIMEOUT_SECONDS","0"),
    ("AI_TIMEOUT_SECONDS","31"),("AI_PROMPT_VERSION","../private"),
])
def test_invalid_ai_configuration_is_rejected_without_values(monkeypatch,name,value):
    monkeypatch.setenv(name,value)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert value not in str(caught.value)
