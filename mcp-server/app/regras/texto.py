"""Conferências de texto usadas pelos portões de análise, alertas, respostas e resumo.

O texto livre do modelo (uma afirmação, uma resposta) não é conferível palavra por palavra. O
que dá para conferir, e é conferido: a citação existe literalmente no chunk citado; e todo
número do texto está escrito numa citação ou veio de um marcador preenchido pelo código.
"""
import re
import unicodedata
from functools import cache

from app.dominio.erros import ToolRejected
from app.regras.campos import atual
from app.regras.numeros import contains_number, number_tokens, to_decimal

_YEAR = re.compile(r"(?:19|20)\d{2}")
_SUFFIXED = re.compile(r"(\d[\d.,]*\d|\d)\s*(%|x\b|p\.p\.)")


# Uma afirmação que começa negando a existência ("O documento não traz guidance") não é um
# achado: é uma ausência, e ausência tem tool própria, que guarda a evidência da busca. Os sujeitos
# do núcleo valem para qualquer documento; o plugin do domínio acrescenta os seus ("a empresa").
_SUBJECTS = ("documento", "relatório", "texto", "nota")
_DENIAL_VERB = (r"(?:também\s+)?não\s+(?:traz|trouxe|apresenta|informa|divulga|menciona|contém|"
                r"detalha|há|(?:foi|foram)\s+(?:identificad|encontrad|localizad))")


@cache
def _denial(subjects: tuple[str, ...]) -> re.Pattern:
    alternatives = "|".join(re.escape(subject).replace(r"\ ", r"\s+") for subject in subjects)
    return re.compile(rf"^(?:(?:o|a|as|os)\s+(?:{alternatives})\s+)?{_DENIAL_VERB}", re.IGNORECASE)


_OTHER_LETTERS = "ªºµ"  # letras que não são do alfabeto latino e são normais em português

def squash(text: str) -> str:
    return " ".join(text.split())


def plain(text: str) -> str:
    """Texto para comparar termos: minúsculas e sem acento."""
    return "".join(ch for ch in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(ch))


def check_script(text: str, what: str) -> None:
    """Recusa texto com letras de outro alfabeto. Modelos pequenos às vezes soltam uma palavra em
    árabe ou armênio no meio de uma frase em português (visto na primeira execução real)."""
    foreign = [word for word in text.split()
               if any(ch.isalpha() and ch not in _OTHER_LETTERS and "LATIN" not in unicodedata.name(ch, "")
                      for ch in word)]
    if foreign:
        raise ToolRejected(f"{what} tem palavras em outro alfabeto ({', '.join(foreign[:3])}). Reescreva em português.")


def denies_existence(statement: str) -> bool:
    return bool(_denial((*_SUBJECTS, *atual().plugin.sujeitos_de_negacao)).match(statement.strip()))


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
    """De que parte do documento o trecho vem, pela seção do chunk: o plugin do domínio sabe (nota
    explicativa, parecer do auditor); sem ele, "documento"."""
    return atual().plugin.tipo_de_fonte(section_path) or "documento"
