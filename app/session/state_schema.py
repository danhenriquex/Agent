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

# Escrito pelo Qualification Agent
STATE_QUALIFICATION_NOTES = "qualification_notes"      # texto livre nesta Parte 1
# "in_progress" | "qualified" | "disqualified"
STATE_QUALIFICATION_STATUS = "qualification_status"
# TODO Parte 6: qualification_notes deveria virar um dict estruturado
# (budget, authority, need, timeline) para o eval set conseguir medir
# "qualificação correta" de forma objetiva, não só ler texto livre.

# Escrito pelo Knowledge Agent (Parte 4 vai popular de fato via RAG/MCP)
STATE_LAST_RETRIEVED_CONTEXT = "last_retrieved_context"

# Escrito pelo Objection Agent
STATE_OBJECTIONS_RAISED = "objections_raised"

# Escrito pelo Scheduling Agent
STATE_MEETING_SLOT = "meeting_slot"

# Escrito pelo Escalate Agent
STATE_ESCALATED = "escalated"

# Escrito pela camada de guardrail/PII (Partes 2 e 3 — placeholder aqui
# para já deixar o contrato visível desde a Parte 1)
STATE_PII_TOKEN_MAP = "pii_token_map"        # token -> valor original; NUNCA vai ao LLM nem a logs
STATE_GUARDRAIL_FLAGS = "guardrail_flags"    # list[str]: violações detectadas na sessão
