"""
Carrega o .env como efeito colateral da primeira vez que algo sob
app.config é importado.

Isso acontece cedo o suficiente — antes dos agentes serem construídos,
que já leem variáveis de ambiente durante a própria importação (ver
app/config/models.py) — sem precisar espalhar `load_dotenv()` em
main.py/api.py antes dos imports (o que violaria a ordem de imports,
regra E402 do ruff).
"""

from dotenv import load_dotenv

load_dotenv()
