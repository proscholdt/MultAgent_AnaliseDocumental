"""Erros do domínio: o portão de uma regra recusou o que chegou."""


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
