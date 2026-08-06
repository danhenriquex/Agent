"""
Teste do endpoint /health via FastAPI TestClient.

Propositalmente NÃO testamos /chat aqui — isso exigiria uma chamada real
de LLM (custo, rede, chave de API), o que não pertence ao smoke test de
CI. Testes de comportamento real do /chat entram na Parte 6 (eval set),
como jobs de CI separados e explicitamente marcados como tal.
"""

from fastapi.testclient import TestClient

from app.api import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
