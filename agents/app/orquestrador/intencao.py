"""A intenção de uma mensagem do chat, antes do orquestrador: roteamento semântico e regras.

O roteador decide quando tem certeza; quando não tem, o orquestrador (LLM) decide, como antes.
São três peças, porque o roteamento sozinho não resolve tudo:

- **Rotas** (`config/intencoes.yaml`): frases de exemplo para `relatorio`, `pergunta` e
  `fora_do_escopo`. A mensagem vira um embedding e ganha a rota da frase mais parecida, se a
  semelhança passar do limiar e ficar à frente da segunda rota pela margem. Senão: sem decisão.
- **Empresa**: "relatório da Millet" é `relatorio_empresa` com o doc_id da Millet. Quem acha a
  empresa é uma regra: as palavras da mensagem contra os nomes dos documentos do usuário
  (empresa e arquivo, sem as palavras genéricas como "Participações" e "Holding"). Uma empresa
  citada: relatório dela; nenhuma ou mais de uma: o consolidado.
- **Seguimento**: "e a menor?", "explique melhor" só fazem sentido com o pedido anterior. Uma
  mensagem que começa como continuação é comparada junto com o pedido anterior da conversa.

O arquivo de rotas é relido quando muda; as frases de exemplo ganham embedding uma vez por versão.
"""
import logging
import math
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

ROTAS = ("relatorio", "pergunta", "fora_do_escopo")
# Devolve os vetores e quantos tokens a chamada gastou.
Embedder = Callable[[list[str], str], Awaitable[tuple[list[list[float]], int]]]

_SEGUIMENTO = re.compile(
    r"^\s*(?:e|mas|tamb[eé]m|por\s*qu[eê]|porque|explique|explica|detalhe|detalha|compare|e\s+quanto|e\s+sobre)\b",
    re.IGNORECASE)
# Palavras de nome de empresa que não identificam a empresa.
_GENERICAS = {"participacoes", "holding", "companhia", "empresa", "empresas", "grupo", "brasil", "brasilia",
              "energia", "servicos", "comercio", "industria", "industrial", "investimentos", "pdf"}


def plain(text: str) -> str:
    """Minúsculas e sem acento, para comparar palavras."""
    return "".join(ch for ch in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(ch))


def _palavras(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", plain(text)))


def apelidos(documento: dict[str, Any]) -> set[str]:
    """As palavras que identificam a empresa de um documento: do nome dela e do nome do arquivo."""
    nome = f"{documento.get('entidade') or ''} {Path(documento.get('arquivo') or '').stem}"
    return {p for p in _palavras(nome) if len(p) >= 4 and p not in _GENERICAS}


def empresas_citadas(mensagem: str, documentos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Os documentos cuja empresa a mensagem cita pelo nome."""
    palavras = _palavras(mensagem)
    return [doc for doc in documentos if apelidos(doc) & palavras]


def seguimento(mensagem: str, anterior: str | None, max_palavras: int) -> bool:
    """A mensagem continua o pedido anterior ("e a menor?", "explique melhor")."""
    return bool(anterior) and len(mensagem.split()) <= max_palavras and bool(_SEGUIMENTO.match(mensagem))


@dataclass(frozen=True)
class Rotas:
    exemplos: dict[str, list[str]]
    limiar: float
    margem: float
    max_palavras_seguimento: int
    modelo: str
    ligado: bool = True


def ler_rotas(path: Path) -> Rotas:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rotas = data.get("rotas") or {}
    faltam = [rota for rota in ROTAS if not rotas.get(rota)]
    if faltam:
        raise ValueError(f"{path}: faltam frases de exemplo para {', '.join(faltam)}")
    extras = set(rotas) - set(ROTAS)
    if extras:
        raise ValueError(f"{path}: rota desconhecida: {', '.join(sorted(extras))}")
    return Rotas(exemplos={rota: [str(frase) for frase in rotas[rota]] for rota in ROTAS},
                 limiar=float(data.get("limiar", 0.6)), margem=float(data.get("margem", 0.05)),
                 max_palavras_seguimento=int(data.get("max_palavras_seguimento", 8)),
                 modelo=str(data.get("modelo_embedding", "text-embedding-3-small")),
                 ligado=bool(data.get("ligado", True)))


@dataclass(frozen=True)
class Escolha:
    intencao: str | None  # pergunta | relatorio | relatorio_empresa | fora_do_escopo; None: o LLM decide
    doc_id: str | None
    rota: str | None  # a rota mais parecida, mesmo quando não decidiu
    semelhanca: float
    margem: float
    segunda: str | None
    exemplo: str  # a frase de exemplo mais parecida
    texto: str  # o que foi comparado: a mensagem, ou o pedido anterior e a mensagem
    seguimento: bool
    empresas: list[str] = field(default_factory=list)
    motivo: str = ""  # por que não decidiu
    tokens: int = 0
    modelo: str = ""

    def view(self) -> dict[str, Any]:
        out = asdict(self)
        out["semelhanca"], out["margem"] = round(self.semelhanca, 3), round(self.margem, 3)
        return out


def _normal(vetor: list[float]) -> list[float]:
    norma = math.sqrt(sum(x * x for x in vetor)) or 1.0
    return [x / norma for x in vetor]


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class Roteador:
    def __init__(self, path: Path, embed: Embedder) -> None:
        self.path, self._embed = path, embed
        self._lido: int | None = None
        self._rotas: Rotas | None = None
        self._vetores: dict[str, list[tuple[str, list[float]]]] = {}
        self.erro: str | None = None  # arquivo inválido: continua valendo a versão anterior

    def para(self, path: Path) -> "Roteador":
        """O roteador de outras frases de exemplo (as de um domínio), com o mesmo embedder."""
        cache = self.__dict__.setdefault("_outros", {})
        if path not in cache:
            cache[path] = Roteador(path, self._embed)
        return cache[path]

    async def rotas(self) -> tuple[Rotas | None, int]:
        """As rotas e os vetores das frases de exemplo, relidos se o arquivo mudou."""
        try:
            stamp = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            self.erro = f"{self.path} não existe"
            return None, 0
        if stamp == self._lido:
            return self._rotas, 0
        try:
            rotas = ler_rotas(self.path)
        except (ValueError, yaml.YAMLError) as error:
            self._lido, self.erro = stamp, str(error).splitlines()[0][:300]
            log.warning("rotas de intenção não aplicadas: %s", self.erro)
            return self._rotas, 0
        frases = [(rota, frase) for rota in ROTAS for frase in rotas.exemplos[rota]]
        vetores, tokens = await self._embed([frase for _, frase in frases], rotas.modelo)
        self._vetores = {rota: [] for rota in ROTAS}
        for (rota, frase), vetor in zip(frases, vetores, strict=True):
            self._vetores[rota].append((frase, _normal(vetor)))
        self._rotas, self._lido, self.erro = rotas, stamp, None
        return rotas, tokens

    async def escolher(self, mensagem: str, anterior: str | None, documentos: list[dict[str, Any]]) -> Escolha:
        rotas, tokens = await self.rotas()
        if rotas is None or not rotas.ligado:
            return Escolha(None, None, None, 0.0, 0.0, None, "", mensagem, False,
                           motivo="roteador desligado" if rotas else (self.erro or "sem rotas"))
        e_seguimento = seguimento(mensagem, anterior, rotas.max_palavras_seguimento)
        texto = f"{anterior} {mensagem}" if e_seguimento else mensagem
        [vetor], gasto = await self._embed([texto], rotas.modelo)
        vetor = _normal(vetor)
        melhores = {rota: max(((_cos(vetor, v), frase) for frase, v in self._vetores[rota]), default=(-1.0, ""))
                    for rota in ROTAS}
        ordem = sorted(ROTAS, key=lambda rota: melhores[rota][0], reverse=True)
        rota, segunda = ordem[0], ordem[1]
        semelhanca, exemplo = melhores[rota]
        margem = semelhanca - melhores[segunda][0]
        # A empresa da própria mensagem vale mais: "e da Millet?" depois de "relatório da Light" é a Millet.
        citadas = empresas_citadas(mensagem, documentos) or (
            empresas_citadas(anterior or "", documentos) if e_seguimento else [])
        base = {"rota": rota, "semelhanca": semelhanca, "margem": margem, "segunda": segunda, "exemplo": exemplo,
                "texto": texto, "seguimento": e_seguimento, "empresas": [d.get("entidade") or d.get("arquivo")
                                                                         for d in citadas],
                "tokens": tokens + gasto, "modelo": rotas.modelo}
        if semelhanca < rotas.limiar:
            return Escolha(None, None, motivo=f"semelhança {semelhanca:.2f} abaixo do limiar {rotas.limiar}", **base)
        if margem < rotas.margem:
            return Escolha(None, None, motivo=f"{rota} e {segunda} empatados (margem {margem:.2f})", **base)
        if rota == "relatorio" and len(citadas) == 1:
            return Escolha("relatorio_empresa", citadas[0]["doc_id"], **base)
        return Escolha(rota, None, **base)


def embedder_openai(api_key: str) -> Embedder:
    """Embeddings pela API da OpenAI (o mesmo modelo que o indexador usa nos chunks)."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)

    async def embed(textos: list[str], modelo: str) -> tuple[list[list[float]], int]:
        resposta = await client.embeddings.create(model=modelo, input=textos)
        return [item.embedding for item in resposta.data], resposta.usage.total_tokens

    return embed
