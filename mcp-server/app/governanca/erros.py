"""Erros que viram resposta para quem chamou."""


class AuthError(Exception):
    """Quem chama não se identificou direito. `status` é o código HTTP equivalente."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


class ToolRejected(Exception):
    """O portão de uma tool recusou a chamada. Volta para o agente como erro da tool, com o
    que não conferiu e, quando existem, as opções válidas, para ele corrigir e tentar de novo."""

    def __init__(self, message: str, *, problemas: list[str] | None = None,
                 opcoes: list[str] | None = None) -> None:
        super().__init__(message)
        self.message, self.problemas, self.opcoes = message, problemas or [], opcoes or []

    def payload(self) -> dict:
        body: dict = {"erro": self.message}
        if self.problemas:
            body["problemas"] = self.problemas
        if self.opcoes:
            body["opcoes"] = self.opcoes
        return body


class IndexerError(Exception):
    """O indexador não respondeu como esperado."""


class IndexerAuthError(IndexerError):
    """O indexador recusou o token do usuário (expirado ou inválido). Não adianta repetir."""


class IndexerNotFound(IndexerError):
    """Documento que não existe para este usuário."""
