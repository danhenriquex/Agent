"""
Constrói e cacheia (como singletons a nível de módulo) o AnalyzerEngine
e o AnonymizerEngine do Presidio usados em toda a camada de mascaramento
de PII.

Carregar o modelo spaCy é caro (segundos, centenas de MB de memória) —
fazemos isso UMA VEZ por processo, não a cada mensagem. As funções
get_*_engine() são thread-safe e lazy: o modelo só carrega na primeira
chamada real, não na importação do módulo (importante pros testes que
não precisam de PERSON/NER — CPF/CNPJ/telefone são regex puro).
"""

import threading

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    PhoneRecognizer,
)
from presidio_anonymizer import AnonymizerEngine

from .recognizers import CnpjBrRecognizer, CpfBrRecognizer

_analyzer_engine: AnalyzerEngine | None = None
_anonymizer_engine: AnonymizerEngine | None = None
_lock = threading.Lock()

# Entidades que a camada de masking (masking.py) sabe tratar. Mantido aqui
# para as duas pontas (engine e masking) citarem a mesma lista de nomes,
# em vez de strings soltas duplicadas nos dois arquivos.
SUPPORTED_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "TELEFONE_BR",
    "CPF_BR",
    "CNPJ_BR",
    "CREDIT_CARD",
]


def get_analyzer_engine() -> AnalyzerEngine:
    global _analyzer_engine
    if _analyzer_engine is None:
        with _lock:
            if _analyzer_engine is None:
                _analyzer_engine = _build_analyzer_engine()
    return _analyzer_engine


def get_anonymizer_engine() -> AnonymizerEngine:
    global _anonymizer_engine
    if _anonymizer_engine is None:
        with _lock:
            if _anonymizer_engine is None:
                _anonymizer_engine = AnonymizerEngine()
    return _anonymizer_engine


def _build_analyzer_engine() -> AnalyzerEngine:
    nlp_configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "pt", "model_name": "pt_core_news_lg"}],
    }
    provider = NlpEngineProvider(nlp_configuration=nlp_configuration)
    nlp_engine = provider.create_engine()

    registry = RecognizerRegistry(supported_languages=["pt"])
    # Carrega os recognizers built-in do Presidio (inclui EMAIL_ADDRESS,
    # CREDIT_CARD, e PERSON já usando o nlp_engine em português acima).
    registry.load_predefined_recognizers(languages=["pt"], nlp_engine=nlp_engine)

    # Telefone BR: built-in do Presidio (python-phonenumbers), só
    # reconfigurado pra região BR e pro nome de entidade que usamos no
    # resto do sistema — mais robusto que qualquer regex escrita à mão
    # (valida DDD e formato de verdade, não só padrão visual).
    registry.add_recognizer(
        PhoneRecognizer(
            supported_entity="TELEFONE_BR",
            supported_regions=("BR",),
            supported_language="pt",
        )
    )

    # CPF/CNPJ: sem equivalente built-in, precisam ser nossos mesmo.
    registry.add_recognizer(CpfBrRecognizer())
    registry.add_recognizer(CnpjBrRecognizer())

    # CREDIT_CARD: apesar do formato ser universal (não depende de
    # idioma), o CreditCardRecognizer padrão do Presidio tem
    # supported_language="en" — load_predefined_recognizers(languages=
    # ["pt"]) SILENCIOSAMENTE pula ele (só loga um warning, não falha),
    # então sem isso o bloqueio de tier 3 nunca dispara. Descoberto via
    # teste automatizado, não documentação — vale registrar aqui.
    registry.add_recognizer(CreditCardRecognizer(supported_language="pt"))

    return AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp_engine,
        supported_languages=["pt"],
    )
