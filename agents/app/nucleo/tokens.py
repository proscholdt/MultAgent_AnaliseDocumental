"""Os dois tokens que este sistema assina. O modelo nunca vê nenhum deles.

- Token do agente, para o servidor MCP: diz qual agente está chamando. É o que a política do
  servidor usa para decidir quais tools existem para ele.
- Token A2A, do orquestrador para um especialista: o especialista só atende quem prova ser o
  orquestrador, e só quando o token foi emitido para ele (o `aud` leva o nome do especialista).

Os dois levam a execução (`run_id`) e o usuário (`usr`), e valem pouco tempo.
"""
import time
from dataclasses import dataclass

import jwt

from app.config import Settings
from app.nucleo.agentes import ORCHESTRATOR

CATALOG = "catalogo"  # destinatário do token que lista os especialistas


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    """Em nome de quem uma execução roda. O token do usuário é o JWT do indexador: segue no
    cabeçalho X-User-Token até o indexador, que é quem isola os documentos por usuário."""

    run_id: str
    user_id: str
    user_token: str


def _sign(claims: dict, secret: str, ttl: int) -> str:
    return jwt.encode({**claims, "exp": int(time.time()) + ttl}, secret, algorithm="HS256")


def mcp_token(settings: Settings, agent: str, identity: Identity) -> str:
    return _sign({"agent": agent, "run_id": identity.run_id, "usr": identity.user_id, "aud": settings.mcp_audience},
                 settings.mcp_agent_secret, settings.execucao_max_segundos + 300)


def mcp_headers(settings: Settings, agent: str, identity: Identity) -> dict[str, str]:
    return {"Authorization": f"Bearer {mcp_token(settings, agent, identity)}", "X-User-Token": identity.user_token}


def a2a_token(settings: Settings, target: str, identity: Identity) -> str:
    return _sign({"agent": ORCHESTRATOR, "run_id": identity.run_id, "usr": identity.user_id, "aud": f"a2a:{target}"},
                 settings.a2a_secret, settings.execucao_max_segundos + 300)


def a2a_headers(settings: Settings, target: str, identity: Identity) -> dict[str, str]:
    return {"Authorization": f"Bearer {a2a_token(settings, target, identity)}", "X-User-Token": identity.user_token}


def verify_a2a_token(settings: Settings, authorization: str | None, target: str) -> dict:
    """Confere que a chamada vem do orquestrador e foi emitida para este especialista."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("Falta o token do orquestrador (Authorization: Bearer).")
    try:
        claims = jwt.decode(token, settings.a2a_secret, algorithms=["HS256"], audience=f"a2a:{target}",
                            options={"require": ["exp", "aud", "agent", "run_id", "usr"]})
    except jwt.ExpiredSignatureError as error:
        raise AuthError("Token do orquestrador expirado.") from error
    except jwt.PyJWTError as error:
        raise AuthError("Token inválido: não foi emitido pelo orquestrador para este especialista.") from error
    if claims["agent"] != ORCHESTRATOR:
        raise AuthError("Só o orquestrador pode chamar um especialista.")
    return claims
