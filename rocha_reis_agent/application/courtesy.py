"""Cortesia isolada depois do encerramento (U1 da revisão funcional, 22/11/2026).

REGRA PROVISÓRIA DE PRODUTO — sujeita à aprovação do responsável pelo salão na homologação 11B; não é
decisão comercial aprovada. Usada num ÚNICO ponto: quando o cliente escreve numa conversa ENCERRADA
(AgentService.handle). Nenhum outro componente deve classificar cortesia.

Conservadora e determinística (sem IA): a mensagem só é cortesia se TODA ela for formada por expressões
de uma lista fechada ("obrigada", "valeu", "ok", "tudo certo"…), emojis de uma lista fechada (👍 🙏 😊…) e
pontuação simples. Qualquer outra coisa reabre o atendimento:
- palavra fora da lista ("Obrigado, mas preciso de ajuda", "não está tudo certo", "Bom dia");
- interrogação ("Ok?", "?"), reticências ("Então...", "Ok..."), emoji fora da lista (😡, 😢);
- texto longo, vazio de palavras/emojis, ou qualquer mídia (áudio, imagem…: nunca é classificada aqui).
Na dúvida, reabre.
"""

from __future__ import annotations

import re
import unicodedata

MAX_CHARS = 80
MAX_WORDS = 8

# Grafia sem acento, minúscula e com letras repetidas reduzidas ("obrigadaaa" → "obrigada").
PHRASES: frozenset[tuple[str, ...]] = frozenset(tuple(p.split()) for p in (
    "obrigado", "obrigada", "obrigadao", "obrigadinho", "obrigadinha", "obg", "obgd", "obgda", "brigado", "brigada",
    "muito obrigado", "muito obrigada", "obrigado mesmo", "obrigada mesmo", "grato", "grata",
    "valeu", "vlw", "valeu mesmo",
    "ok", "okay", "oki", "blz", "beleza", "show", "perfeito", "perfeita", "otimo", "otima", "combinado",
    "certo", "tudo certo", "ta bom", "ta otimo", "top", "joia",
)) | frozenset(  # "(muito) obrigado/a pelo atendimento / pela atenção / por tudo"
    tuple(f"{lead}{thanks} {suffix}".split()) for lead in ("", "muito ") for thanks in ("obrigado", "obrigada")
    for suffix in ("pelo atendimento", "pela atencao", "por tudo"))
EMOJI = frozenset("👍🙏😊🙂☺😀😃😄😁🥰😍😘❤💖💕💗👏✨🤗🌹💐")
EMOJI_MODIFIERS = frozenset("️︎‍") | {chr(c) for c in range(0x1F3FB, 0x1F400)}  # variação, ZWJ, tons de pele
PUNCTUATION = frozenset(".!,;:-~")
_REPEAT = re.compile(r"(\w)\1+")


def _words(text: str) -> list[str] | None:
    """Palavras normalizadas, ou None se houver algo que impede classificar como cortesia."""
    if len(text) > MAX_CHARS or "?" in text or "¿" in text or ".." in text or "…" in text:
        return None
    letters: list[str] = []
    for char in unicodedata.normalize("NFKC", text):
        if char in EMOJI or char in EMOJI_MODIFIERS:
            letters.append(" ")
        elif char.isspace() or char in PUNCTUATION:
            letters.append(" ")
        elif char.isalpha():
            letters.append(char)
        else:
            return None  # dígito, símbolo ou emoji fora da lista: pode ser outra coisa
    plain = "".join(c for c in unicodedata.normalize("NFD", "".join(letters).casefold())
                    if unicodedata.category(c) != "Mn")
    return _REPEAT.sub(r"\1", plain).split()


def _covered(words: list[str]) -> bool:
    """Toda a sequência é composta por expressões da lista (a mais longa primeiro)."""
    if not words:
        return True
    for size in range(min(LONGEST, len(words)), 0, -1):
        if tuple(words[:size]) in _PHRASES and _covered(words[size:]):
            return True
    return False


_PHRASES = frozenset(tuple(_REPEAT.sub(r"\1", w) for w in phrase) for phrase in PHRASES)
LONGEST = max(len(phrase) for phrase in _PHRASES)


def is_isolated_courtesy(text: str) -> bool:
    """True SOMENTE quando a mensagem inteira é agradecimento/confirmação simples (ver docstring do módulo)."""
    if not isinstance(text, str) or not text.strip():
        return False
    words = _words(text.strip())
    if words is None or len(words) > MAX_WORDS:
        return False
    has_emoji = any(char in EMOJI for char in text)
    if not words and not has_emoji:
        return False  # só pontuação ("!", ".") não é cortesia reconhecida
    return _covered(words)
