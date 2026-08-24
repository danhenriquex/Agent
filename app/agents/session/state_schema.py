"""
Chaves do session.state compartilhado entre os agentes.

Centralizar isso aqui evita o erro clássico de sistema multi-agente: um
agente escreve "lead_name" e outro lê "leadName" ou "nome_lead". Toda
leitura/escrita de estado no projeto deve usar essas constantes, nunca
strings soltas espalhadas pelos agentes.

Isso também documenta o "contrato" entre agentes — qualquer pessoa lendo
este arquivo entende o que cada agente espera receber e o que produz, sem
precisar ler os prompts inteiros de cada um.
"""

# Escrito pelo Qualification Agent, via set_qualification_status.
# dict (QualificationDetails.model_dump(), ver app/agents/qualification.py):
# {status, pain, product_of_interest, reasoning, company_size,
# additional_notes} — o "opportunity brief" da oportunidade. Deixou de
# ser texto livre pra que o eval set (Parte 6) e quem assumir a
# conversa depois (SchedulingAgent hoje, via book_meeting; um
# Closer/CRM real amanhã) consigam ler pain/product_of_interest de
# forma confiável em vez de reinterpretar prosa.
STATE_QUALIFICATION_NOTES = "qualification_notes"
# "in_progress" | "qualified" | "disqualified" — escrito pela tool
# set_qualification_status (Parte 3). Consumido por
# app/agents/guardrails/action_allowlist.py pra decidir se book_meeting
# pode executar de verdade. Mantido como string plana (em vez de dentro
# do dict acima) de propósito: é o contrato de igualdade que a
# allowlist já depende de checar.
STATE_QUALIFICATION_STATUS = "qualification_status"

# Escrito pelo Knowledge Agent (Parte 4 vai popular de fato via RAG/MCP)
STATE_LAST_RETRIEVED_CONTEXT = "last_retrieved_context"

# Escrito pelo Objection Agent
STATE_OBJECTIONS_RAISED = "objections_raised"

# Escrito pelo Scheduling Agent
STATE_MEETING_SLOT = "meeting_slot"

# Escrito pelo Escalate Agent
STATE_ESCALATED = "escalated"

# Escrito pela camada de guardrails (Partes 2 e 3): PII (redação de
# cartão), prompt injection, política de saída, allowlist de ação, e
# tentativa de transfer_to_agent não autorizada. list[str], útil pra
# debug e pra futuro dashboard de observabilidade (Parte 5).
STATE_PII_TOKEN_MAP = "pii_token_map"        # token -> valor original; NUNCA vai ao LLM nem a logs
STATE_GUARDRAIL_FLAGS = "guardrail_flags"
