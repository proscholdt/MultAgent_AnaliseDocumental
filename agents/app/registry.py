"""Um agente é um arquivo: `agentes/<nome>.md`, com um cabeçalho YAML e o prompt no corpo.

    ---
    nome: extrator
    descricao: Extrai os indicadores financeiros de um documento.
    modelo: padrao
    max_turnos: 40
    ---
    Você extrai indicadores...

A pasta é relida quando muda, sem reiniciar. Um arquivo inválido não derruba os outros: ele
fica de fora, e o motivo aparece em GET /api/agentes.

O que o arquivo NÃO define é o que o agente pode chamar: isso é a política do servidor MCP.
"""
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# A mesma regra de nome do servidor MCP, sem hífen: o nome também vira nome de tool do orquestrador.
NAME = re.compile(r"[a-z][a-z0-9_]{1,39}")
ORCHESTRATOR = "orquestrador"
# Nomes que o servidor MCP usa para quem não é um agente com modelo.
RESERVED = {"sistema"}
REPLIES = ("base", "texto")
_HEADER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_KEYS = {"nome", "descricao", "modelo", "max_turnos", "resposta"}


class AgentFileError(ValueError):
    pass


@dataclass(frozen=True)
class AgentFile:
    nome: str
    descricao: str  # o que o orquestrador lê para decidir quando chamar
    modelo: str  # apelido; o modelo real está em MODELOS, no .env
    max_turnos: int
    # O que volta para o orquestrador quando o agente termina. "base": só o relato montado pelo
    # código (o que foi gravado e o que ainda falta). "texto": também a mensagem final do agente,
    # para agentes cujo produto é um texto e não uma gravação.
    resposta: str
    prompt: str
    digest: str


def parse_agent(text: str, file_name: str) -> AgentFile:
    # Arquivo salvo no Windows pode começar com o BOM (U+FEFF), e aí o "---" do cabeçalho não casa.
    match = _HEADER.match(text.lstrip("\ufeff"))
    if not match:
        raise AgentFileError("falta o cabeçalho entre linhas '---' no começo do arquivo")
    try:
        header: Any = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise AgentFileError(f"cabeçalho com YAML inválido ({str(error).splitlines()[0]})") from error
    if not isinstance(header, dict):
        raise AgentFileError("o cabeçalho tem de ser uma lista de 'chave: valor'")
    unknown = sorted(set(header) - _KEYS)
    if unknown:
        raise AgentFileError(f"chave desconhecida no cabeçalho: {', '.join(unknown)}")

    nome, descricao = str(header.get("nome") or ""), str(header.get("descricao") or "").strip()
    if not NAME.fullmatch(nome):
        raise AgentFileError("'nome' tem de ter de 2 a 40 letras minúsculas, dígitos ou '_', começando por letra")
    if nome in RESERVED:
        raise AgentFileError(f"'{nome}' é um nome reservado")
    if file_name != f"{nome}.md":
        raise AgentFileError(f"o arquivo do agente '{nome}' tem de se chamar {nome}.md")
    if len(descricao) < 20:
        raise AgentFileError("'descricao' tem de dizer, em uma frase, o que o agente faz (é o que o orquestrador lê)")
    max_turnos = header.get("max_turnos", 30)
    if isinstance(max_turnos, bool) or not isinstance(max_turnos, int) or not 1 <= max_turnos <= 200:
        raise AgentFileError("'max_turnos' tem de ser um inteiro de 1 a 200")
    resposta = str(header.get("resposta", "base"))
    if resposta not in REPLIES:
        raise AgentFileError("'resposta' tem de ser 'base' ou 'texto'")
    prompt = match.group(2).strip()
    if len(prompt) < 40:
        raise AgentFileError("o corpo do arquivo (o prompt do agente) está vazio ou curto demais")
    return AgentFile(nome=nome, descricao=" ".join(descricao.split()), modelo=str(header.get("modelo", "padrao")),
                     max_turnos=max_turnos, resposta=resposta, prompt=prompt,
                     digest=hashlib.sha256(text.encode()).hexdigest()[:12])


@dataclass(frozen=True)
class Snapshot:
    """Os agentes como estavam num instante. Uma execução guarda o `digest` do retrato que usou."""

    agents: dict[str, AgentFile] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # arquivo -> por que ficou de fora
    digest: str = ""

    @property
    def orchestrator(self) -> AgentFile | None:
        return self.agents.get(ORCHESTRATOR)

    @property
    def specialists(self) -> list[AgentFile]:
        return [agent for name, agent in sorted(self.agents.items()) if name != ORCHESTRATOR]


class Registry:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._stamp: tuple | None = None
        self._snapshot = Snapshot()

    def current(self) -> Snapshot:
        files = sorted(self.folder.glob("*.md")) if self.folder.is_dir() else []
        stamp = tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in files)
        if stamp != self._stamp:
            agents, errors = {}, {}
            for path in files:
                try:
                    agent = parse_agent(path.read_text(encoding="utf-8"), path.name)
                    agents[agent.nome] = agent
                except (AgentFileError, UnicodeDecodeError) as error:
                    errors[path.name] = str(error)
            digest = hashlib.sha256("".join(sorted(a.digest for a in agents.values())).encode()).hexdigest()[:12]
            self._stamp, self._snapshot = stamp, Snapshot(agents, errors, digest)
        return self._snapshot
