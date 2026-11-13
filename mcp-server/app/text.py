"""Conferências de texto usadas pelos portões de análise, alertas, respostas e resumo.

O texto livre do modelo (uma afirmação, uma resposta) não é conferível palavra por palavra. O
que dá para conferir, e é conferido: a citação existe literalmente no chunk citado; e todo
número do texto está escrito numa citação ou veio de um marcador preenchido pelo código.
"""
import re

from app.errors import ToolRejected
from app.numbers import contains_number, number_tokens, to_decimal

_YEAR = re.compile(r"(?:19|20)\d{2}")
_NUMBERED_NOTE = re.compile(r"\d{1,2}(?:\.\d{1,2})*\.?\s")
_SUFFIXED = re.compile(r"(\d[\d.,]*\d|\d)\s*(%|x\b|p\.p\.)")


def squash(text: str) -> str:
    return " ".join(text.split())


def check_quote(quote: str, chunk: dict, *, min_chars: int) -> str:
    """A citação, normalizada, se ela existe literalmente no chunk. Senão, recusa."""
    quote = squash(quote)
    if len(quote) < min_chars:
        raise ToolRejected(f"A citação é curta demais (mínimo de {min_chars} caracteres): copie a frase inteira.")
    if quote not in squash(chunk["text"]):
        raise ToolRejected(f"A citação não está no chunk {chunk['chunk_index']}. Copie o texto literalmente, "
                           "sem resumir nem corrigir.")
    return quote


def unbacked_numbers(text: str, sources: list[str]) -> list[str]:
    """Números do texto que nenhuma das fontes escreve. Ficam de fora os anos e os inteiros
    pequenos sem sufixo ("3 empresas", "5 perguntas"): contam coisas, não são valores."""
    suffixed = {match.group(1).rstrip(".,") for match in _SUFFIXED.finditer(text)}
    missing = []
    for token in dict.fromkeys(number_tokens(text)):
        value = to_decimal(token)
        if value is None or _YEAR.fullmatch(token):
            continue
        small_count = value == value.to_integral_value() and value < 100 and token not in suffixed \
            and not any(separator in token for separator in ".,")
        if small_count:
            continue
        if not any(contains_number(source, token) for source in sources):
            missing.append(token)
    return missing


def source_kind(section_path: list[str]) -> str:
    """De que parte do documento o trecho vem, pela seção do chunk. É uma regra simples: nota
    numerada, parecer do auditor, demonstração; o resto é texto do relatório."""
    joined = " > ".join(section_path).casefold()
    if "auditor" in joined:
        return "parecer do auditor"
    if "notas explicativas" in joined or any(_NUMBERED_NOTE.match(section) for section in section_path):
        return "nota explicativa"
    if "balanço" in joined or "demonstraç" in joined:
        return "demonstração financeira"
    return "relatório"
