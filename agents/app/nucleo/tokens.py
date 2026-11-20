"""Os dois tokens que este sistema assina. O modelo nunca vê nenhum deles.

- Token do agente, para o servidor MCP: diz qual agente está chamando. É o que a política do
  servidor usa para decidir quais tools existem para ele.
- Token A2A, do orquestrador para um especialista: o especialista só atende quem prova ser o
  orquestrador, e só quando o token foi emitido para ele (o `aud` leva o nome do especialista).

Os dois levam a execução (`run_id`) e o usuário (`usr`), e valem pouco tempo.
"""
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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
    # O domínio e a coleção da execução (dominios/<id>/). Sem coleção: o domínio padrão e todos os
    # documentos do usuário, como antes dos pacotes de domínio.
    dominio: str | None = None
    colecao_id: str | None = None

    def claims(self) -> dict[str, str]:
        """O que os tokens levam do escopo da execução: o servidor MCP lê `dom` e `col`."""
        return {key: value for key, value in (("dom", self.dominio), ("col", self.colecao_id)) if value}


@dataclass(frozen=True)
class ChavePropria:
    servico: str
    pem: bytes


@lru_cache(maxsize=8)
def _ler_propria(pasta: str, _mtime: float) -> ChavePropria | None:
    arquivos = sorted(Path(pasta).glob("*.pem"))
    if len(arquivos) != 1:
        return None  # nenhuma ou ambígua: modo antigo (segredo compartilhado)
    return ChavePropria(servico=arquivos[0].stem, pem=arquivos[0].read_bytes())


def chave_propria(settings: Settings) -> ChavePropria | None:
    """A chave privada deste serviço, se configurada (`<chaves_dir>/propria/<servico>.pem`)."""
    pasta = Path(settings.chaves_dir) / "propria"
    if not pasta.is_dir():
        return None
    return _ler_propria(str(pasta), pasta.stat().st_mtime)


def chave_publica(settings: Settings, servico: str) -> bytes | None:
    arquivo = Path(settings.chaves_dir) / "publicas" / f"{servico}.pem"
    return arquivo.read_bytes() if arquivo.is_file() else None


def _sign(settings: Settings, claims: dict, secret: str, ttl: int) -> str:
    claims = {**claims, "exp": int(time.time()) + ttl}
    propria = chave_propria(settings)
    if propria is not None:
        return jwt.encode(claims, propria.pem, algorithm="EdDSA", headers={"kid": propria.servico})
    return jwt.encode(claims, secret, algorithm="HS256")


def mcp_token(settings: Settings, agent: str, identity: Identity) -> str:
    return _sign(settings, {"agent": agent, "run_id": identity.run_id, "usr": identity.user_id, "aud": settings.mcp_audience,
                            **identity.claims()},
                 settings.mcp_agent_secret, settings.execucao_max_segundos + 300)


def mcp_headers(settings: Settings, agent: str, identity: Identity) -> dict[str, str]:
    return {"Authorization": f"Bearer {mcp_token(settings, agent, identity)}", "X-User-Token": identity.user_token}


def a2a_token(settings: Settings, target: str, identity: Identity) -> str:
    return _sign(settings, {"agent": ORCHESTRATOR, "run_id": identity.run_id, "usr": identity.user_id, "aud": f"a2a:{target}",
                            **identity.claims()},
                 settings.a2a_secret, settings.execucao_max_segundos + 300)


def a2a_headers(settings: Settings, target: str, identity: Identity) -> dict[str, str]:
    return {"Authorization": f"Bearer {a2a_token(settings, target, identity)}", "X-User-Token": identity.user_token}


def verify_a2a_token(settings: Settings, authorization: str | None, target: str) -> dict:
    """Confere que a chamada vem do orquestrador e foi emitida para este especialista."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("Falta o token do orquestrador (Authorization: Bearer).")
    # Com a chave pública do orquestrador presente, só vale a assinatura dele (EdDSA); o segredo
    # compartilhado deixa de abrir a porta.
    publica = chave_publica(settings, ORCHESTRATOR)
    key, algorithm = (publica, "EdDSA") if publica is not None else (settings.a2a_secret, "HS256")
    try:
        claims = jwt.decode(token, key, algorithms=[algorithm], audience=f"a2a:{target}",
                            options={"require": ["exp", "aud", "agent", "run_id", "usr"]})
    except jwt.ExpiredSignatureError as error:
        raise AuthError("Token do orquestrador expirado.") from error
    except jwt.PyJWTError as error:
        raise AuthError("Token inválido: não foi emitido pelo orquestrador para este especialista.") from error
    if claims["agent"] != ORCHESTRATOR:
        raise AuthError("Só o orquestrador pode chamar um especialista.")
    return claims
