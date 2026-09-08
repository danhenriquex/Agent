"""
LLM-as-judge: pontua a resposta real de um agente contra o critério
qualitativo de um item do golden set (`eval/golden_set.py`).

`judge-model` (litellm_proxy/config.yaml) era DELIBERADAMENTE de uma
família diferente dos modelos sendo avaliados -- um modelo julgando a
própria família de saída como boa é um viés de auto-avaliação
documentado em LLM-as-judge. ESSA PROPRIEDADE ESTÁ PARCIALMENTE
QUEBRADA HOJE: qualification/knowledge/objection-model foram trocados
de claude-sonnet-5 pra gpt-4o-mini por custo (juiz é gpt-4o, MESMA
família GPT-4o); knowledge-model depois foi pra claude-haiku-4.5 por
qualidade (narrava intenção de tool call em vez de chamar a tool de
verdade -- validado via `make eval-run` antes de trocar, ao contrário
da troca original pra gpt-4o-mini), então a dimensão de faithfulness
já não compartilha família com o juiz -- qualification/objection-model
continuam gpt-4o-mini. Ver o TODO no bloco do judge-model em
litellm_proxy/config.yaml antes de confiar nos scores de uma run.
Mudar o modelo-juiz é uma mudança em config.yaml, não neste arquivo --
mesmo racional de `get_model_for_role` em app/agents/config/models.py.

Duas responsabilidades ficam deliberadamente separadas neste módulo:
- `build_judge_prompt` / `parse_judge_response`: puras, sem I/O --
  testáveis em camada 2 (tests/test_eval_judge.py), sem custar chamada
  de LLM nem precisar do proxy rodando.
- `judge_response`: a única função que efetivamente chama o
  modelo-juiz via LiteLLM Proxy -- usada só pelo pipeline real
  (eval/runner.py), nunca em `make test`.
"""

from __future__ import annotations

import json
import os
import re

import litellm
from pydantic import BaseModel, Field, ValidationError

JUDGE_MODEL_ALIAS = "litellm_proxy/judge-model"
PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://localhost:4000")
PROXY_KEY = os.getenv("LITELLM_PROXY_KEY", "sk-local-master-key")

# Mesmo racional de _DEFAULT_MAX_TOKENS em app/agents/config/models.py:
# sem um teto explícito, a OpenRouter pré-autoriza crédito baseado no
# teto de output do modelo por trás do alias, não no uso real -- e
# rejeita com 402 se o saldo não cobrir o PIOR CASO. O JudgeScore é um
# JSON pequeno (score + 2-4 frases de justificativa); 512 tokens é
# folga generosa, não um limite apertado.
_JUDGE_MAX_TOKENS = 512


class JudgeScore(BaseModel):
    """Schema esperado da resposta do juiz -- score 1-5 POR DIMENSÃO,
    nunca um número solto, pra que uma regressão detectada aponte pra
    ONDE a qualidade caiu (tom? aderência ao critério? faithfulness?),
    não só "piorou"."""

    criteria_adherence: int = Field(ge=1, le=5)
    tone_and_persona: int = Field(ge=1, le=5)
    # None quando o item não tem reference_context (a pergunta não é
    # sobre produto/preço, então "faithfulness" não se aplica) -- ver
    # _JUDGE_SYSTEM_PROMPT.
    faithfulness: int | None = Field(default=None, ge=1, le=5)
    justification: str

    @property
    def overall(self) -> float:
        scores = [self.criteria_adherence, self.tone_and_persona]
        if self.faithfulness is not None:
            scores.append(self.faithfulness)
        return sum(scores) / len(scores)


_JUDGE_SYSTEM_PROMPT = (
    "Você é um avaliador (LLM-as-judge) independente e rigoroso de um bot "
    "de SDR (pré-vendas B2B) chamado Bia, da empresa Helssing. Vai receber "
    "uma conversa e a resposta final do bot, e precisa pontuar essa "
    "resposta de 1 (péssima) a 5 (excelente) em cada dimensão abaixo. Seja "
    "criterioso -- não dê 5 por padrão só porque a resposta é educada; "
    "reserve 5 pra quando a resposta realmente exemplifica o critério.\n\n"
    "Responda APENAS com um objeto JSON válido, sem markdown, sem texto "
    "fora do JSON, exatamente neste formato:\n"
    '{"criteria_adherence": <1-5>, "tone_and_persona": <1-5>, '
    '"faithfulness": <1-5 ou null>, "justification": "<2-4 frases em '
    'português explicando as notas>"}\n\n'
    "criteria_adherence: o quanto a resposta atende ao CRITÉRIO DE "
    "SUCESSO descrito na mensagem do usuário.\n"
    "tone_and_persona: tom apropriado -- empático em objeção, nunca "
    "condescendente ou hostil, nunca finge ser humano se perguntado, "
    "nunca soa como um interrogatório de formulário.\n"
    "faithfulness: use null se NENHUM contexto de referência foi "
    "fornecido (a pergunta não é sobre produto/preço, então não há como "
    "verificar). Se um contexto foi fornecido, pontue 1 se a resposta "
    "afirma qualquer coisa sobre o produto que não vem dele (ou contradiz "
    "um fato negativo explícito do contexto, como 'não oferecemos X'), e "
    "5 se toda alegação sobre o produto é consistente com o contexto."
)


def build_judge_prompt(
    conversation: list[str],
    response_text: str,
    qualitative_criteria: str,
    reference_context: str | None = None,
) -> str:
    """Monta o prompt de avaliação. Função pura -- sem chamada de rede,
    testável isoladamente."""
    transcript = "\n".join(f"Lead: {msg}" for msg in conversation)
    parts = [
        f"CONVERSA (mensagens do lead, em ordem):\n{transcript}\n",
        f"RESPOSTA FINAL DO BOT:\n{response_text}\n",
        f"CRITÉRIO DE SUCESSO:\n{qualitative_criteria}\n",
    ]
    if reference_context:
        parts.append(
            "CONTEXTO DE REFERÊNCIA (fonte da verdade para a dimensão "
            f"faithfulness):\n{reference_context}\n"
        )
    return "\n".join(parts)


def _strip_code_fence(text: str) -> str:
    """Modelos ocasionalmente envolvem o JSON pedido em ```json ... ```
    mesmo quando instruídos a não fazer isso -- extrai o conteúdo de
    dentro do fence antes de tentar de novo, em vez de falhar direto."""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def parse_judge_response(raw_text: str) -> JudgeScore:
    """Faz o parse da resposta em texto do modelo-juiz pro schema
    JudgeScore. Tenta o texto cru primeiro, depois uma versão sem
    code-fence markdown -- levanta ValueError com a resposta original
    anexada se nenhuma das duas formas render um JSON válido que bata
    com o schema (nunca deixa um score inválido passar silenciosamente
    pro pipeline de regressão)."""
    last_error: Exception | None = None
    for candidate in (raw_text, _strip_code_fence(raw_text)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        try:
            return JudgeScore.model_validate(data)
        except ValidationError as exc:
            last_error = exc
            continue
    raise ValueError(
        f"Resposta do juiz não é o JSON esperado (schema JudgeScore): {raw_text!r}"
    ) from last_error


def judge_response(
    conversation: list[str],
    response_text: str,
    qualitative_criteria: str,
    reference_context: str | None = None,
    model: str = JUDGE_MODEL_ALIAS,
) -> JudgeScore:
    """Chama o judge-model via LiteLLM Proxy e retorna o score
    estruturado. Única função deste módulo que faz I/O de rede -- usada
    só pelo pipeline real de avaliação, nunca em `make test`."""
    prompt = build_judge_prompt(
        conversation, response_text, qualitative_criteria, reference_context
    )
    completion = litellm.completion(
        model=model,
        api_base=PROXY_URL,
        api_key=PROXY_KEY,
        temperature=0,
        max_tokens=_JUDGE_MAX_TOKENS,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    raw = completion.choices[0].message.content
    return parse_judge_response(raw)
