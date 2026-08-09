"""
Runner de linha de comando para testar o fluxo multi-agente localmente,
sem FastAPI ainda (isso entra numa parte futura, junto com observabilidade
e a API real).

Uso:
    1. Copie .env.example para .env e preencha OPENROUTER_API_KEY
    2. Suba o LiteLLM Proxy:
           cd litellm_proxy && docker compose up
    3. Na raiz do projeto:
           python -m app.main
"""

import asyncio
import os

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agents import root_agent  # importar isso já carrega o .env (ver app/config/__init__.py)

APP_NAME = os.getenv("SDR_APP_NAME", "sdr-bot")
USER_ID = "demo_lead"
SESSION_ID = "demo_session"


async def send_message(runner: Runner, text: str) -> None:
    content = types.Content(role="user", parts=[types.Part(text=text)])

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=SESSION_ID,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            # event.author = qual agente da hierarquia respondeu esse turno.
            # Útil pra debug: confirma se o roteamento foi pro agente certo
            # sem precisar instrumentar nada ainda (isso vira tracing de
            # verdade na Parte 5, via LangFuse/Phoenix).
            print(f"\n[{event.author}] {event.content.parts[0].text}")


async def main() -> None:
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID,
    )

    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    print("=== SDR Bot (Parte 1: esqueleto multi-agente) ===")
    print("Digite 'sair' para encerrar.\n")

    while True:
        user_input = input("Você: ").strip()
        if user_input.lower() in {"sair", "exit", "quit"}:
            break
        if not user_input:
            continue

        await send_message(runner, user_input)


if __name__ == "__main__":
    asyncio.run(main())
