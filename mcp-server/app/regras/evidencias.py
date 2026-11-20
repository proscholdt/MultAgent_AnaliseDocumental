"""Evidência no documento: onde os termos de um campo aparecem (a prova de que ele não existe) e as
tabelas da seção em que um campo mora. O que é de um domínio (o valor que reconcilia com a
demonstração, o indicador que a empresa informa com o próprio nome) fica no plugin dele."""
import re
from typing import Any

from app.dominio.documento import IndexedDocument

SENTENCE = re.compile(r"(?<=[.;])\s+")
_EXCERPT = 90  # caracteres de contexto em volta de um termo achado


def squash_spaces(text: str) -> str:
    return " ".join(text.split())


def statement_tables(document: IndexedDocument, section: str | None) -> list[dict[str, Any]]:
    """As tabelas da seção em que o campo mora (o `secao` do pacote), pelo título da seção do chunk."""
    return [chunk for chunk in document.chunks if chunk["chunk_type"] == "table"
            and (section is None or section in " > ".join(chunk.get("section_path") or []).casefold())]


def occurrences(document: IndexedDocument, terms: tuple[str, ...]) -> dict[int, str]:
    """Páginas em que algum termo do campo aparece, com um pedaço do texto em volta."""
    found: dict[int, str] = {}
    for chunk in document.chunks:
        text = squash_spaces(chunk["text"])
        folded = text.casefold()
        position = min((p for term in terms if (p := folded.find(term)) >= 0), default=-1)
        if position < 0:
            continue
        excerpt = text[max(0, position - _EXCERPT):position + _EXCERPT]
        for page in range(chunk["page_start"], chunk["page_end"] + 1):
            found.setdefault(page, excerpt)
    return found


def mentions(info: Any, text: str) -> bool:
    """O texto (sem acento) fala do campo? Cada termo de `mention` é uma alternativa; "a+b" exige as duas partes."""
    return any(all(part in text for part in term.split("+")) for term in info.mention)
