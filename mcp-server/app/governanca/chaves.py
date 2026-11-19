"""Chaves públicas dos serviços dos agentes: quem assinou o token e por quais agentes ele fala.

Cada serviço (o orquestrador e cada especialista) tem a própria chave Ed25519. O token leva no
cabeçalho `kid` o nome do serviço, e o servidor confere com `<kid>.pem` desta pasta. Um serviço
só fala pelo próprio agente; o orquestrador fala também pelo "sistema" (a conferência final).
"""
import re
from pathlib import Path

import jwt

from app.governanca.erros import AuthError

_KID = re.compile(r"[a-z][a-z0-9_-]{1,39}")
# Agentes que um serviço pode assumir além do próprio nome.
_TAMBEM = {"orquestrador": frozenset({"sistema"})}


def ativas(pasta: Path) -> bool:
    """Há chaves públicas configuradas? Então só tokens EdDSA valem."""
    return pasta.is_dir() and any(pasta.glob("*.pem"))


def fala_por(kid: str, agent: str) -> bool:
    return agent == kid or agent in _TAMBEM.get(kid, frozenset())


def decodificar(token: str, pasta: Path, audience: str, required: list[str]) -> dict:
    """Confere assinatura EdDSA com a chave do serviço que o `kid` nomeia e que o serviço fala
    pelo agente do token."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise AuthError(401, f"Token do agente inválido: {exc}") from exc
    kid = header.get("kid")
    if header.get("alg") != "EdDSA" or not isinstance(kid, str) or not _KID.fullmatch(kid):
        raise AuthError(401, "Token do agente sem assinatura do serviço (EdDSA com kid).")
    arquivo = pasta / f"{kid}.pem"
    if not arquivo.is_file():
        raise AuthError(401, f"Serviço '{kid}' sem chave pública registrada.")
    try:
        claims = jwt.decode(token, arquivo.read_bytes(), algorithms=["EdDSA"], audience=audience,
                            options={"require": required})
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(401, "Token do agente expirado.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(401, f"Token do agente inválido: {exc}") from exc
    if not isinstance(claims.get("agent"), str) or not fala_por(kid, claims["agent"]):
        raise AuthError(403, f"O serviço '{kid}' não pode falar pelo agente '{claims.get('agent')}'.")
    return claims
