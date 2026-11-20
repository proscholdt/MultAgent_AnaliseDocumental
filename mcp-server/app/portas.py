"""Portas: o que os casos de uso precisam do mundo de fora, sem saber quem atende.

`app.dados.base.Store` (SQLAlchemy), `app.dados.documentos.Documents` e
`app.dados.indexador.IndexerClient` são os adaptadores de hoje. Um teste, ou outro banco, só
precisa cumprir estes contratos.
"""
from typing import Any, Protocol

from app.dominio.documento import IndexedDocument


class Repositorio(Protocol):
    """Leitura da base de dados extraídos de um usuário."""

    async def extraction(self, user_id: str, doc_id: str) -> Any | None: ...

    async def analysis(self, user_id: str, doc_id: str, fingerprint: str) -> list[Any]: ...

    async def alerts(self, user_id: str, doc_id: str, fingerprint: str | None = None) -> list[Any]: ...

    async def summary(self, user_id: str, doc_id: str, fingerprint: str) -> Any | None: ...

    async def answers(self, user_id: str, colecao_id: str | None) -> list[Any]: ...

    async def report(self, user_id: str, colecao_id: str | None) -> Any | None: ...

    async def company_report(self, user_id: str, doc_id: str) -> Any | None: ...


class LeitorDeDocumentos(Protocol):
    """Um documento indexado do usuário, com todos os chunks."""

    async def get(self, caller: Any, doc_id: str) -> IndexedDocument: ...


class CatalogoDeDocumentos(Protocol):
    """A lista de documentos do usuário no indexador."""

    async def documents(self, user_token: str) -> list[dict[str, Any]]: ...
