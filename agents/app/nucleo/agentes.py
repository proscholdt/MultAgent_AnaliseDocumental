"""Um agente é uma pasta, `agentes/<nome>/`, com o arquivo `<nome>.md` (um cabeçalho YAML e o
prompt no corpo) e o `Dockerfile` do serviço dele:

    ---
    nome: extrator
    descricao: Extrai os indicadores financeiros de um documento.
    modelo: gpt-5.4-nano      # o nome do modelo; "groq:..." vai à Groq
    raciocinio: low           # nenhum | minimal | low | medium | high; sem a chave, o padrão do ajustes.yaml
    max_turnos: 40
    ---
    Você extrai indicadores...

A pasta é relida quando muda, sem reiniciar. Um arquivo inválido não derruba os outros: ele
fica de fora, e o motivo aparece em GET /api/agentes. Um `.md` solto na raiz de `agentes/` (o
formato antigo) também fica de fora, com o lugar certo no motivo.

O que o arquivo NÃO define é o que o agente pode chamar: isso é a política do servidor MCP.
"""
import dataclasses
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

import yaml

from app import pacotes

# A mesma regra de nome do servidor MCP, sem hífen: o nome também vira nome de tool do orquestrador.
NAME = re.compile(r"[a-z][a-z0-9_]{1,39}")
ORCHESTRATOR = "orquestrador"
# Nomes que o servidor MCP usa para quem não é um agente com modelo.
RESERVED = {"sistema"}
REPLIES = ("base", "texto")
_HEADER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_KEYS = {"nome", "descricao", "modelo", "raciocinio", "max_turnos", "resposta"}
EFFORTS = ("nenhum", "minimal", "low", "medium", "high")


class AgentFileError(ValueError):
    pass


@dataclass(frozen=True)
class AgentFile:
    nome: str
    descricao: str  # o que o orquestrador lê para decidir quando chamar
    modelo: str  # o nome do modelo no provedor (com "groq:" na frente, na Groq)
    max_turnos: int
    # O que volta para o orquestrador quando o agente termina. "base": só o relato montado pelo
    # código (o que foi gravado e o que ainda falta). "texto": também a mensagem final do agente,
    # para agentes cujo produto é um texto e não uma gravação.
    resposta: str
    prompt: str
    digest: str
    # Esforço de raciocínio deste agente: None = o padrão do ajustes.yaml; "" = sem raciocínio.
    raciocinio: str | None = None
    # O pacote do domínio já entrou no prompt (with_domain): aplicar de novo duplicaria o complemento
    # e, no modo substitui, apagaria o que veio depois (a lista de especialistas do orquestrador).
    com_dominio: bool = False


def vocabulario(pacote: pacotes.Pacote) -> dict[str, str]:
    """As palavras do domínio que os prompts usam por marcador ($entidade, $campos...). O prompt
    base de cada agente é o método; o que ele diz sobre o domínio vem daqui e do complemento."""
    ids = [pergunta.id for pergunta in pacote.perguntas]
    campos = []
    for campo in pacote.campos:
        if campo.principal:
            extra = f", em {campo.unidade}" if campo.unidade else ""
            campos.append(f"- `{campo.nome}`: {campo.rotulo} ({campo.tipo}{extra})")
    categorias = [f"| `{c.nome}` | {c.descricao or c.rotulo} |" for c in pacote.categorias]
    return {
        "documentos": pacote.nome[:1].lower() + pacote.nome[1:],
        "entidade": pacote.entidade.rotulo.lower(), "entidades": pacote.entidade.plural.lower(),
        "recorte": pacote.recorte.rotulo.lower(), "n_perguntas": str(len(ids)),
        "perguntas": f"`{ids[0]}` a `{ids[-1]}`" if len(ids) > 1 else f"`{ids[0]}`",
        "n_categorias": str(len(pacote.categorias)),
        "campos": "\n".join(campos),
        "categorias": "| categoria | o que procurar |\n|---|---|\n" + "\n".join(categorias),
    }


def descricao_no_dominio(nome: str, descricao: str, dominio: str | None) -> str:
    """O que o orquestrador lê sobre um especialista: a `descricao` do prompt dele no pacote, se o
    pacote der uma; senão, a do arquivo do agente, nas palavras do domínio."""
    pacote = pacotes.pacote(dominio or pacotes.PADRAO)
    path = pacotes.arquivo_do_pacote(pacote.id, f"prompts/{nome}.md")
    if path is not None and (match := _HEADER.match(path.read_text(encoding="utf-8").lstrip(_BOM))):
        descricao = str((yaml.safe_load(match.group(1)) or {}).get("descricao") or descricao)
    return Template(descricao).safe_substitute(vocabulario(pacote))


def with_domain(spec: AgentFile, dominio: str | None) -> AgentFile:
    """O agente no domínio: o prompt base (o método) com as palavras do domínio no lugar dos
    marcadores, mais o que o pacote diz a ele (`dominios/<id>/prompts/<agente>.md`).

    O arquivo do pacote tem um cabeçalho opcional entre linhas '---' com:
    - `modo`: `complemento` (padrão: o texto entra no fim do prompt do agente) ou `substitui` (o
      texto é o prompt deste agente neste domínio; modelo, turnos e raciocínio continuam os do
      arquivo do agente);
    - `descricao`: o que o orquestrador lê sobre o agente neste domínio.
    Sem domínio, vale o padrão. Os marcadores ($entidade, $documentos, $campos, $categorias,
    $n_perguntas, $perguntas, $recorte) valem no prompt base e no do pacote; os marcadores dos
    valores ({{doc_id.campo}}) e o "R$" não são tocados."""
    if spec.com_dominio:
        return spec
    pacote = pacotes.pacote(dominio or pacotes.PADRAO)
    palavras = vocabulario(pacote)
    path = pacotes.arquivo_do_pacote(pacote.id, f"prompts/{spec.nome}.md")
    mode, body, descricao = "complemento", "", spec.descricao
    if path is not None:
        text = path.read_text(encoding="utf-8").lstrip(_BOM)
        body = text
        if match := _HEADER.match(text):
            header = yaml.safe_load(match.group(1)) or {}
            mode, body = str(header.get("modo", "complemento")), match.group(2)
            descricao = str(header.get("descricao") or descricao)
        if mode not in ("complemento", "substitui"):
            raise AgentFileError(f"{path}: modo {mode!r} desconhecido (use complemento ou substitui)")
    base = Template(spec.prompt).safe_substitute(palavras)
    extra = Template(body.strip()).safe_substitute(palavras)
    prompt = extra if mode == "substitui" else (f"{base.rstrip()}\n\n{extra}" if extra else base)
    return dataclasses.replace(spec, prompt=prompt, descricao=Template(descricao).safe_substitute(palavras),
                               com_dominio=True)


_BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"  # arquivo salvo no Windows pode começar com ele


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
    modelo = header.get("modelo")
    if not isinstance(modelo, str) or len(modelo.strip()) < 3:
        raise AgentFileError("'modelo' é obrigatório: o nome do modelo, como 'gpt-5.4-nano' ou "
                             "'groq:openai/gpt-oss-120b'")
    raciocinio = header.get("raciocinio")
    if raciocinio is not None and raciocinio not in EFFORTS:
        raise AgentFileError(f"'raciocinio' tem de ser um destes: {', '.join(EFFORTS)}")
    resposta = str(header.get("resposta", "base"))
    if resposta not in REPLIES:
        raise AgentFileError("'resposta' tem de ser 'base' ou 'texto'")
    prompt = match.group(2).strip()
    if len(prompt) < 40:
        raise AgentFileError("o corpo do arquivo (o prompt do agente) está vazio ou curto demais")
    return AgentFile(nome=nome, descricao=" ".join(descricao.split()), modelo=modelo.strip(),
                     max_turnos=max_turnos, resposta=resposta, prompt=prompt,
                     digest=hashlib.sha256(text.encode()).hexdigest()[:12],
                     raciocinio=None if raciocinio is None else ("" if raciocinio == "nenhum" else raciocinio))


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


def agent_file(nome: str) -> str:
    """Onde mora o arquivo de um agente, relativo à pasta de agentes. É a chave dos erros."""
    return f"{nome}/{nome}.md"


class Registry:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._stamp: tuple | None = None
        self._snapshot = Snapshot()

    def current(self) -> Snapshot:
        files = sorted([*self.folder.glob("*/*.md"), *self.folder.glob("*.md")]) if self.folder.is_dir() else []
        stamp = tuple((path.relative_to(self.folder).as_posix(), path.stat().st_mtime_ns, path.stat().st_size)
                      for path in files)
        if stamp != self._stamp:
            agents, errors = {}, {}
            for path in files:
                key = path.relative_to(self.folder).as_posix()
                try:
                    if path.parent == self.folder:
                        raise AgentFileError(f"o arquivo fica na pasta do agente: agentes/{agent_file(path.stem)}")
                    agent = parse_agent(path.read_text(encoding="utf-8"), path.name)
                    if path.parent.name != agent.nome:
                        raise AgentFileError(f"o arquivo do agente '{agent.nome}' fica em agentes/{agent_file(agent.nome)}")
                    agents[agent.nome] = agent
                except (AgentFileError, UnicodeDecodeError) as error:
                    errors[key] = str(error)
            digest = hashlib.sha256("".join(sorted(a.digest for a in agents.values())).encode()).hexdigest()[:12]
            self._stamp, self._snapshot = stamp, Snapshot(agents, errors, digest)
        return self._snapshot
