"""Erros que viram resposta para quem chamou."""
from app.dominio.erros import ToolRejected  # noqa: F401 - reexportado: o portão é do domínio


class AuthError(Exception):
    """Quem chama não se identificou direito. `status` é o código HTTP equivalente."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


class IndexerError(Exception):
    """O indexador não respondeu como esperado."""


class IndexerAuthError(IndexerError):
    """O indexador recusou o token do usuário (expirado ou inválido). Não adianta repetir."""


class IndexerNotFound(IndexerError):
    """Documento que não existe para este usuário."""
