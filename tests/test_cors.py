"""
CORS em app/api.py só é habilitado quando HANDOFF_DASHBOARD_ORIGINS
está setado -- por padrão (env vazia), fica DESLIGADO, porque
/handoff/*/claim e /handoff/*/reply não têm autenticação nenhuma hoje
(ver claim_handoff em app/api.py): CORS aberto por padrão exporia essas
rotas de qualquer origem sem querer. O middleware é montado uma vez, no
module-level do FastAPI app -- por isso o teste precisa recarregar o
módulo (mesmo padrão de tests/test_handoff.py) para que a env setada
ANTES do reload seja o que decide se o middleware entra ou não.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("PII_HASH_SALT", "test-salt-not-for-production-use-32b")

    def _load(origins: str):
        monkeypatch.setenv("HANDOFF_DASHBOARD_ORIGINS", origins)

        import importlib

        import app.api as api_module

        importlib.reload(api_module)
        return TestClient(api_module.app)

    return _load


def test_cors_disabled_by_default_when_no_origins_configured(client):
    test_client = client("")

    response = test_client.get(
        "/health", headers={"Origin": "http://localhost:5173"}
    )

    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_configured_origin(client):
    test_client = client("http://localhost:5173")

    response = test_client.options(
        "/sessions",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_rejects_unconfigured_origin(client):
    test_client = client("http://localhost:5173")

    response = test_client.options(
        "/sessions",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers
