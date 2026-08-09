"""
Recognizers customizados para PII brasileira que o Presidio não cobre
nativamente — os recognizers built-in são calibrados pra formatos
US/EU (CREDIT_CARD, EMAIL_ADDRESS funcionam como estão; PERSON precisa
de um backend de NLP em português, ver engine.py).

Telefone brasileiro NÃO tem recognizer customizado aqui — o Presidio já
inclui PhoneRecognizer (usa python-phonenumbers, o mesmo motor por trás
do libphonenumber do Google), que já suporta a região 'BR' nativamente
e valida DDD/formato de verdade, o que é mais robusto que qualquer regex
que escreveríamos à mão. Configuração dele fica em engine.py.

CPF e CNPJ usam PatternRecognizer com validação de dígito verificador
de verdade, não só formato. Um número no formato de CPF que falha o
dígito verificador não é um CPF real — tratá-lo como PII geraria falsos
positivos desnecessários (ex: qualquer ID de pedido de 11 dígitos seria
capturado).

Os algoritmos de dígito verificador foram validados contra números de
teste publicamente conhecidos antes de entrar aqui (111.444.777-35 para
CPF, 11.222.333/0001-81 para CNPJ) — ver histórico de desenvolvimento.
"""

from typing import ClassVar

from presidio_analyzer import Pattern, PatternRecognizer


def _cpf_check_digits(base9: str) -> str:
    """Calcula os 2 dígitos verificadores de um CPF a partir dos 9 dígitos base."""
    digits = [int(d) for d in base9]

    s = sum(d * w for d, w in zip(digits, range(10, 1, -1)))
    r = s % 11
    d1 = 0 if r < 2 else 11 - r

    digits10 = digits + [d1]

    s2 = sum(d * w for d, w in zip(digits10, range(11, 1, -1)))
    r2 = s2 % 11
    d2 = 0 if r2 < 2 else 11 - r2

    return f"{d1}{d2}"


def _cnpj_check_digits(base12: str) -> str:
    """Calcula os 2 dígitos verificadores de um CNPJ a partir dos 12 dígitos base."""
    digits = [int(d) for d in base12]

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    s = sum(d * w for d, w in zip(digits, w1))
    r = s % 11
    d1 = 0 if r < 2 else 11 - r

    digits13 = digits + [d1]

    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    s2 = sum(d * w for d, w in zip(digits13, w2))
    r2 = s2 % 11
    d2 = 0 if r2 < 2 else 11 - r2

    return f"{d1}{d2}"


class CpfBrRecognizer(PatternRecognizer):
    """Detecta CPF (pessoa física), validando o dígito verificador de
    verdade — e rejeitando explicitamente sequências de dígito repetido
    (ex: 111.111.111-11), que passam no checksum matematicamente mas
    nunca são CPFs válidos emitidos na prática."""

    PATTERNS: ClassVar[list[Pattern]] = [
        Pattern(
            name="cpf (com pontuação)",
            regex=r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
            score=0.5,
        ),
        Pattern(
            name="cpf (só dígitos)",
            regex=r"\b\d{11}\b",
            score=0.3,
        ),
    ]

    CONTEXT: ClassVar[list[str]] = [
        "cpf",
        "documento",
        "identidade",
    ]

    def __init__(self):
        super().__init__(
            supported_entity="CPF_BR",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="pt",
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        digits = "".join(ch for ch in pattern_text if ch.isdigit())

        if len(digits) != 11:
            return False

        if len(set(digits)) == 1:
            return False

        return _cpf_check_digits(digits[:9]) == digits[9:]


class CnpjBrRecognizer(PatternRecognizer):
    """Detecta CNPJ (pessoa jurídica), com a mesma lógica de validação
    real de dígito verificador."""

    PATTERNS: ClassVar[list[Pattern]] = [
        Pattern(
            name="cnpj (com pontuação)",
            regex=r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b",
            score=0.5,
        ),
        Pattern(
            name="cnpj (só dígitos)",
            regex=r"\b\d{14}\b",
            score=0.3,
        ),
    ]

    CONTEXT: ClassVar[list[str]] = [
        "cnpj",
        "empresa",
        "razão social",
    ]

    def __init__(self):
        super().__init__(
            supported_entity="CNPJ_BR",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="pt",
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        digits = "".join(ch for ch in pattern_text if ch.isdigit())

        if len(digits) != 14:
            return False

        if len(set(digits)) == 1:
            return False

        return _cnpj_check_digits(digits[:12]) == digits[12:]
