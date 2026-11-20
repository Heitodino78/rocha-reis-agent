"""Minimização antes de qualquer envio a um provedor EXTERNO de IA.

Remove padrões de dados pessoais que não são necessários para classificar a
intenção (telefone, e-mail, CPF/CNPJ, sequências longas de dígitos). Não é
anonimização completa: nomes próprios e outros dados livres podem permanecer, por
isso a ativação real exige avaliação de privacidade (STATUS_FASE_8.md).
"""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_PHONE = re.compile(r"\(?\+?\d[\d\s().-]{7,}\d")
_LONG_DIGITS = re.compile(r"\d{8,}")


def redact_personal_data(text: str) -> str:
    value = _EMAIL.sub("[e-mail removido]", text)
    value = _CNPJ.sub("[documento removido]", value)
    value = _CPF.sub("[documento removido]", value)
    value = _PHONE.sub(lambda m: "[telefone removido]" if sum(c.isdigit() for c in m.group()) >= 8 else m.group(), value)
    return _LONG_DIGITS.sub("[número removido]", value)
