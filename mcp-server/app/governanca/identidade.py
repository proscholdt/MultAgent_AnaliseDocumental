"""Quem está chamando: o agente e o usuário.

São duas identidades independentes, que respondem a perguntas diferentes:

- O agente (cabeçalho Authorization) diz QUAIS TOOLS existem para a chamada. É um JWT curto,
  assinado pelo sistema dos agentes, com o nome do agente e a execução a que pertence.
- O usuário (cabeçalho X-User-Token) diz QUAIS DOCUMENTOS existem. É o JWT do indexador, que
  este servidor só repassa ao indexador. Para saber quem é, pergunta ao próprio indexador.

O token do agente também carrega o usuário para quem foi emitido (`usr`), e os dois têm de
bater: um token de agente emitido para a Ana não serve com o JWT do Bruno.
"""
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

import jwt

from app import pacotes
from app.config import Settings
from app.dados.indexador import IndexerClient
from app.governanca import chaves
from app.governanca.erros import AuthError

AGENT_NAME = re.compile(r"[a-z][a-z0-9_-]{1,39}")
_REQUIRED_CLAIMS = ["exp", "aud", "agent", "run_id", "usr"]


@dataclass(frozen=True)
class Caller:
    agent: str
    run_id: str
    user_id: str
    user_token: str
    # O domínio e a coleção da execução (claims `dom` e `col` do token do agente). Sem eles: o domínio
    # padrão e todos os documentos do usuário, como antes dos pacotes de domínio.
    dominio: str | None = None
    colecao_id: str | None = None


def bearer(headers: Mapping[str, str]) -> str | None:
    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def verify_agent_token(token: str, settings: Settings) -> dict:
    """Confere assinatura, validade e destinatário do token do agente e devolve o conteúdo.
    Com chaves públicas configuradas, só EdDSA com a chave do serviço (`chaves.py`)."""
    if chaves.ativas(settings.chaves_publicas_dir):
        claims = chaves.decodificar(token, settings.chaves_publicas_dir, settings.mcp_audience, _REQUIRED_CLAIMS)
        if not AGENT_NAME.fullmatch(claims["agent"]):
            raise AuthError(401, "Token do agente com nome de agente inválido.")
        return claims
    try:
        claims = jwt.decode(token, settings.mcp_agent_secret, algorithms=["HS256"],
                            audience=settings.mcp_audience, options={"require": _REQUIRED_CLAIMS})
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(401, "Token do agente expirado.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(401, f"Token do agente inválido: {exc}") from exc
    if not isinstance(claims["agent"], str) or not AGENT_NAME.fullmatch(claims["agent"]):
        raise AuthError(401, "Token do agente com nome de agente inválido.")
    return claims


def check_request(headers: Mapping[str, str], settings: Settings) -> dict:
    """Conferência barata, feita antes de qualquer mensagem MCP: tem token de agente válido e
    tem token de usuário? Quem é o usuário só é resolvido depois (custa uma chamada)."""
    token = bearer(headers)
    if token is None:
        raise AuthError(401, "Falta o token do agente (Authorization: Bearer ...).")
    claims = verify_agent_token(token, settings)
    if not (headers.get("x-user-token") or "").strip():
        raise AuthError(401, "Falta o token do usuário (X-User-Token).")
    return claims


class UserResolver:
    """Descobre o usuário de um JWT do indexador perguntando ao indexador, com cache curto."""

    def __init__(self, indexer: IndexerClient, ttl_seconds: int) -> None:
        self._indexer, self._ttl = indexer, ttl_seconds
        self._cache: dict[str, tuple[str, float]] = {}

    async def resolve(self, user_token: str) -> str:
        cached = self._cache.get(user_token)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        user = await self._indexer.me(user_token)  # IndexerAuthError se o token não vale mais
        self._cache[user_token] = (user["user_id"], time.monotonic() + self._ttl)
        if len(self._cache) > 1000:  # tokens antigos: o cache não cresce sem limite
            now = time.monotonic()
            self._cache = {token: entry for token, entry in self._cache.items() if entry[1] > now}
        return user["user_id"]


async def identify(headers: Mapping[str, str], settings: Settings, users: UserResolver) -> Caller:
    """A identidade completa de uma mensagem. É refeita a cada mensagem: não há sessão."""
    claims = check_request(headers, settings)
    user_token = headers["x-user-token"].strip()
    user_id = await users.resolve(user_token)
    if claims["usr"] != user_id:
        raise AuthError(403, "O token do agente foi emitido para outro usuário.")
    dominio = claims.get("dom") or None
    if dominio is not None and dominio not in pacotes.todos():
        raise AuthError(403, f"O token do agente traz um domínio desconhecido: {dominio!r}.")
    return Caller(agent=claims["agent"], run_id=str(claims["run_id"]), user_id=user_id, user_token=user_token,
                  dominio=dominio, colecao_id=claims.get("col") or None)
