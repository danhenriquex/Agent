"""
Factory de session service persistente + helper get-or-create.

Substitui InMemorySessionService (app/main.py, app/api.py) por
DatabaseSessionService: SQLite local por padrão (zero infra pra rodar
`make api`/`make cli`), Postgres em produção via SESSION_DB_URL.

DatabaseSessionService foi escolhido no lugar do Redis mencionado no TODO
antigo de app/api.py porque já é uma classe pronta do ADK -- SQLite dev e
Postgres prod com a mesma interface -- e serve melhor como fonte de
verdade relacional para histórico de eventos/estado estruturado do que
Redis, mais adequado a cache/pub-sub.
"""

import os

from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.sessions import BaseSessionService, DatabaseSessionService, Session

_DEFAULT_SESSION_DB_URL = "sqlite+aiosqlite:///./sdr_bot_sessions.db"


def get_session_service() -> BaseSessionService:
    """SQLite local por padrão; aponte SESSION_DB_URL pra Postgres em produção.

    O default SQLite grava um arquivo no filesystem local do processo --
    em Cloud Run isso não sobrevive a restart/autoscaling (o problema
    original do TODO em app/api.py). SESSION_DB_URL deve apontar pra um
    Postgres real (ex: Cloud SQL) antes de qualquer deploy real.
    """
    db_url = os.getenv("SESSION_DB_URL", _DEFAULT_SESSION_DB_URL)
    return DatabaseSessionService(db_url=db_url)


async def get_or_create_session(
    session_service: BaseSessionService,
    *,
    app_name: str,
    user_id: str,
    session_id: str,
    initial_state: dict | None = None,
) -> Session:
    """Get-then-create-on-miss.

    DatabaseSessionService.create_session() levanta AlreadyExistsError
    quando chamado de novo com o mesmo session_id -- então duas
    requisições que tentam criar a MESMA sessão nova ao mesmo tempo (ex:
    webhook duplicado do WhatsApp) fariam a perdedora da corrida receber
    um 500 se não fosse por isso. Em vez de propagar o erro, buscamos de
    novo e retornamos o que a vencedora já persistiu.
    """
    session = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if session is not None:
        return session
    try:
        return await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            state=initial_state,
        )
    except AlreadyExistsError:
        session = await session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        if session is None:
            raise
        return session
