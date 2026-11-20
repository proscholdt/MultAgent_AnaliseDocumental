"""Datas escritas num documento: "12/03/2024", "12/03/24", "12.03.2024", "12 de março de 2024".

Um campo de data guarda a data em dd/mm/aaaa, e só entra com o trecho do documento que a prova: o
dia, o mês (em número ou por extenso) e o ano têm de estar no trecho citado.
"""
import re
import unicodedata

MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
_NUMERICA = re.compile(r"\b(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{2}|\d{4})\b")
_EXTENSO = re.compile(r"\b(\d{1,2})(?:º|°)?\s+de\s+([a-zç]+)\s+de\s+(\d{4})\b")


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.casefold()) if not unicodedata.combining(c))


def _ano(texto: str) -> int:
    ano = int(texto)
    return ano + 2000 if ano < 100 else ano


def _valida(dia: int, mes: int, ano: int) -> str | None:
    if not (1 <= dia <= 31 and 1 <= mes <= 12 and 1900 <= ano <= 2100):
        return None
    return f"{dia:02d}/{mes:02d}/{ano:04d}"


def normalizar_data(texto: str) -> str | None:
    """A primeira data do texto em dd/mm/aaaa, ou None se não houver uma data válida."""
    plano = _sem_acento(texto or "")
    if match := _NUMERICA.search(plano):
        return _valida(int(match[1]), int(match[2]), _ano(match[3]))
    if (match := _EXTENSO.search(plano)) and match[2] in MESES:
        return _valida(int(match[1]), MESES[match[2]], int(match[3]))
    return None


def data_no_trecho(data: str, trecho: str) -> bool:
    """O trecho escreve esta data (dd/mm/aaaa), em número ou por extenso?"""
    alvo = normalizar_data(data)
    plano = _sem_acento(trecho or "")
    datas = [_valida(int(m[1]), int(m[2]), _ano(m[3])) for m in _NUMERICA.finditer(plano)]
    datas += [_valida(int(m[1]), MESES[m[2]], int(m[3])) for m in _EXTENSO.finditer(plano) if m[2] in MESES]
    return alvo is not None and alvo in datas
