"""
Mapeia cada papel de agente para o alias configurado no LiteLLM Proxy.

Este módulo é o único lugar do código que sabe qual modelo cada agente usa.
Trocar um modelo (ex: qualification-model de Claude pra GPT) é uma mudança
em litellm_proxy/config.yaml, não em app/agents/*.py — isso é proposital:
separa a decisão de "qual modelo" da lógica de "o que o agente faz".

Nota sobre o prefixo "litellm_proxy/": é a forma como o SDK do LiteLLM
(usado internamente pelo LiteLlm do ADK) sabe que deve falar com um proxy
self-hosted em vez de ir direto num provedor. Ver:
https://docs.litellm.ai/docs/providers/litellm_proxy
"""

import os

from google.adk.models.lite_llm import LiteLlm

PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://localhost:4000")
PROXY_KEY = os.getenv("LITELLM_PROXY_KEY", "sk-local-master-key")

# Bug real encontrado rodando `make eval-run` (Parte 6) contra a
# OpenRouter de verdade: nenhum agente definia `max_tokens`, então cada
# request implicitamente pedia o teto de OUTPUT do modelo por trás do
# alias (65536 pro Sonnet, mais ainda pro Opus) -- e a OpenRouter faz
# pré-autorização de crédito baseada nesse teto, não no uso real. Com
# saldo abaixo do necessário pra cobrir o PIOR CASO (65536+ tokens),
# toda chamada era rejeitada com 402 antes mesmo de gerar uma palavra,
# mesmo a resposta de verdade precisando de uma fração disso -- uma
# resposta de chat de SDR nunca precisa de mais que uma fração deste
# valor. Capar explicitamente evita o 402 independente do saldo restante
# na conta.
_DEFAULT_MAX_TOKENS = 2048

# Aliases devem bater exatamente com "model_name" em litellm_proxy/config.yaml
_ROLE_TO_ALIAS = {
    "orchestrator": "orchestrator-model",
    "qualification": "qualification-model",
    "knowledge": "knowledge-model",
    "objection": "objection-model",
    "scheduling": "scheduling-model",
    "escalate": "escalate-model",
}


def get_model_for_role(role: str) -> LiteLlm:
    """Retorna um LiteLlm configurado para falar com o LiteLLM Proxy local.

    O ADK enxerga isso como "só mais um modelo" — quem decide para qual
    provedor real (OpenRouter -> Claude/GPT/Llama) isso vai é o proxy,
    baseado no config.yaml. Isso é o que permite trocar/adicionar fallback
    de modelo sem tocar no código do agente.

    Args:
        role: uma das chaves de _ROLE_TO_ALIAS (ex: "qualification").

    Raises:
        ValueError: se o papel não for reconhecido — falha explícita é
            melhor que silenciosamente cair num modelo errado.
    """
    if role not in _ROLE_TO_ALIAS:
        raise ValueError(
            f"Papel de agente desconhecido: '{role}'. "
            f"Papéis válidos: {sorted(_ROLE_TO_ALIAS)}"
        )

    alias = _ROLE_TO_ALIAS[role]

    return LiteLlm(
        model=f"litellm_proxy/{alias}",
        api_base=PROXY_URL,
        api_key=PROXY_KEY,
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
