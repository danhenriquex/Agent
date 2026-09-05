"""
Testes de app/session_service.py -- usam SQLite real (via aiosqlite, já
dependência do projeto), não um mock, porque o comportamento que importa
aqui é exatamente o que um mock esconderia: get-or-create idempotente e
recuperação de AlreadyExistsError numa corrida entre duas chamadas
criando a mesma sessão nova ao mesmo tempo.
"""

from unittest.mock import AsyncMock

import pytest
from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.sessions import DatabaseSessionService

from app.session_service import get_or_create_session, get_session_service


def test_get_session_service_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("SESSION_DB_URL", raising=False)

    service = get_session_service()

    assert isinstance(service, DatabaseSessionService)


def test_get_session_service_respects_env_var(tmp_path, monkeypatch):
    db_path = tmp_path / "custom_sessions.db"
    monkeypatch.setenv("SESSION_DB_URL", f"sqlite+aiosqlite:///{db_path}")

    service = get_session_service()

    assert isinstance(service, DatabaseSessionService)


async def test_get_or_create_creates_when_missing(tmp_path):
    db_path = tmp_path / "sessions.db"
    service = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{db_path}")

    session = await get_or_create_session(
        service, app_name="sdr-bot", user_id="u1", session_id="s1", initial_state={"x": 1}
    )

    assert session.id == "s1"
    assert session.state.get("x") == 1


async def test_get_or_create_returns_existing_without_error(tmp_path):
    db_path = tmp_path / "sessions.db"
    service = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{db_path}")
    await get_or_create_session(
        service, app_name="sdr-bot", user_id="u1", session_id="s1", initial_state={"x": 1}
    )

    session = await get_or_create_session(
        service, app_name="sdr-bot", user_id="u1", session_id="s1", initial_state={"x": 999}
    )

    assert session.id == "s1"
    assert session.state.get("x") == 1


async def test_get_or_create_recovers_from_already_exists_error(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    service = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{db_path}")
    await get_or_create_session(service, app_name="sdr-bot", user_id="u1", session_id="s1")

    monkeypatch.setattr(
        service,
        "create_session",
        AsyncMock(side_effect=AlreadyExistsError("Session with id s1 already exists.")),
    )

    session = await get_or_create_session(
        service, app_name="sdr-bot", user_id="u1", session_id="s1"
    )

    assert session.id == "s1"


async def test_get_or_create_propagates_when_recovery_finds_nothing(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    service = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{db_path}")

    monkeypatch.setattr(
        service,
        "create_session",
        AsyncMock(side_effect=AlreadyExistsError("Session with id s1 already exists.")),
    )

    with pytest.raises(AlreadyExistsError):
        await get_or_create_session(service, app_name="sdr-bot", user_id="u1", session_id="s1")
