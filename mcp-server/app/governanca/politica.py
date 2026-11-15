"""Política de governança: quais tools cada agente vê e chama.

O arquivo (policy.yaml) fica neste servidor, e não no sistema dos agentes: quem escreve um
agente não concede a própria permissão. Ele é relido quando muda, sem reiniciar. Um arquivo
inválido não derruba nem abre o servidor: vale a última política boa, e o erro fica visível
em /governanca. Agente que não está na política não tem acesso a nada.
"""
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.governanca.identidade import AGENT_NAME

log = logging.getLogger(__name__)


class PolicyError(Exception):
    """O arquivo de política não pode ser usado como está."""


@dataclass(frozen=True)
class AgentPolicy:
    tools: frozenset[str]
    calls_per_run: int | None  # None = sem limite de chamadas por execução


@dataclass(frozen=True)
class Policy:
    agents: dict[str, AgentPolicy]
    digest: str  # impressão do arquivo: vai em cada linha da auditoria

    def tools_of(self, agent: str) -> frozenset[str]:
        entry = self.agents.get(agent)
        return entry.tools if entry else frozenset()

    def allows(self, agent: str, tool: str) -> bool:
        return tool in self.tools_of(agent)

    def calls_per_run(self, agent: str) -> int | None:
        entry = self.agents.get(agent)
        return entry.calls_per_run if entry else None


def parse_policy(text: str, known_tools: set[str]) -> Policy:
    """Lê e confere a política. Nome de tool que não existe é erro: quase sempre é digitação,
    e deixar passar daria a impressão de que o agente tem um acesso que ele não tem."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"YAML inválido: {exc}") from exc
    agents_raw = (raw or {}).get("agentes") if isinstance(raw, dict) else None
    if not isinstance(agents_raw, dict) or not agents_raw:
        raise PolicyError("A política precisa de uma seção 'agentes' com pelo menos um agente.")
    agents: dict[str, AgentPolicy] = {}
    for name, entry in agents_raw.items():
        if not isinstance(name, str) or not AGENT_NAME.fullmatch(name):
            raise PolicyError(f"Nome de agente inválido: {name!r} (minúsculas, dígitos, '-' e '_').")
        if not isinstance(entry, dict) or not isinstance(entry.get("tools"), list):
            raise PolicyError(f"Agente '{name}': falta a lista 'tools'.")
        tools = entry["tools"]
        unknown = sorted(str(tool) for tool in tools if tool not in known_tools)
        if unknown:
            raise PolicyError(f"Agente '{name}': tools que não existem: {', '.join(unknown)}.")
        limit = (entry.get("limites") or {}).get("chamadas_por_execucao")
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise PolicyError(f"Agente '{name}': 'chamadas_por_execucao' tem de ser um inteiro positivo.")
        agents[name] = AgentPolicy(tools=frozenset(tools), calls_per_run=limit)
    return Policy(agents=agents, digest=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12])


class PolicyStore:
    """Mantém a política em vigor e a relê quando o arquivo muda."""

    def __init__(self, path: Path, known_tools: set[str]) -> None:
        self._path, self._known_tools = path, known_tools
        self._mtime = path.stat().st_mtime_ns
        # Na subida, política inválida é erro: não há uma anterior para manter.
        self._policy = parse_policy(path.read_text(encoding="utf-8"), known_tools)
        self.last_error: str | None = None

    def current(self) -> Policy:
        try:
            mtime = self._path.stat().st_mtime_ns
        except OSError as exc:
            self.last_error = f"Arquivo de política inacessível: {exc}"
            return self._policy
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                self._policy = parse_policy(self._path.read_text(encoding="utf-8"), self._known_tools)
                self.last_error = None
                log.info("Política recarregada (%s).", self._policy.digest)
            except (PolicyError, OSError) as exc:
                self.last_error = str(exc)
                log.error("Política nova recusada; continua valendo a anterior. Motivo: %s", exc)
        return self._policy
