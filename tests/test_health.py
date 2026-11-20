"""Smoke HTTP e falha inesperada, usando apenas transporte em memória."""

import logging

import pytest
from fastapi.testclient import TestClient

from rocha_reis_agent.main import create_app


def test_health_returns_ok_without_credentials(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"] == "application/json"
    assert client.app.state.settings.app_env == "local"


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [("get", "/missing", 404), ("post", "/health", 405)],
)
def test_http_errors_are_preserved(
    client: TestClient, method: str, path: str, status: int
) -> None:
    response = client.request(method, path)
    assert response.status_code == status
    assert "detail" in response.json()


def test_unexpected_error_is_safe_in_response_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()
    private_marker = "SYNTHETIC_PRIVATE_VALUE_DO_NOT_LOG"

    @app.get("/test-error")
    async def fail() -> None:
        raise RuntimeError(private_marker)

    with caplog.at_level(logging.ERROR), TestClient(app) as test_client:
        response = test_client.get("/test-error", params={"private": private_marker})

    assert response.status_code == 500
    assert response.json() == {"detail": "Erro interno do servidor."}
    assert private_marker not in response.text
    assert private_marker not in caplog.text
    records = [r for r in caplog.records if r.name == "rocha_reis_agent"]
    assert len(records) == 1
    assert records[0].getMessage() == "request_failed"
    assert records[0].exc_info is None
