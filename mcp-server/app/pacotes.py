"""Pacotes de domínio: o que muda de um tipo de documento para outro, fora do código.

Cada domínio é uma pasta em `dominios/<id>/` (na raiz do projeto; no Docker, montada em
`/app/dominios`) com um `dominio.yaml`: que documentos ele trata, de quem é cada documento (a
entidade: empresa, paciente, contrato), o recorte no tempo (período, data do exame), os metadados
que o indexador extrai, os campos que os agentes extraem, as categorias da análise, as perguntas
padrão e os textos da interface. Ao lado, opcionais: `prompts/<agente>.md` (o que o agente precisa
saber deste domínio; o método fica no prompt base), `intencoes.yaml` (frases do roteador do chat)
e `avaliacao/` (gabarito). O que não cabe em YAML (fórmulas, regras de inconsistência) é um
plugin em Python no sistema que o usa, apontado por `plugin`.

Este arquivo é igual nos três sistemas (fonte em comum/, cópias via `make comum`): o esquema é o
contrato, e um pacote inválido não sobe em nenhum deles. Só pydantic e pyyaml.
"""
import os
import re
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, field_validator, model_validator

_NOME = r"^[a-z][a-z0-9_]{1,39}$"
PADRAO = "financeiro"  # o domínio de quem não diz o seu (documentos e coleções de antes dos pacotes)


def _nome_valido(value: str) -> str:
    if not re.fullmatch(_NOME, value):
        raise ValueError(f"nome inválido: {value!r} (minúsculas, números e _, começando por letra)")
    return value


Nome = Annotated[str, AfterValidator(_nome_valido)]


class _Bloco(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # chave com erro de digitação é erro, não silêncio


class Rotulo(_Bloco):
    """De quem é o documento: a empresa, o paciente, o contrato."""
    nome: Nome
    rotulo: str
    plural: str
    ausente: str | None = None  # o que o cabeçalho de cada chunk diz sem ela (padrão: "<rotulo> não identificado")

    def texto_ausente(self) -> str:
        return self.ausente or f"{self.rotulo} não identificado"



class Recorte(_Bloco):
    """O tempo do documento. `periodo`: exercício ou trimestre, com o período anterior como
    comparável (demonstrações); `data`: um dia (exame, laudo); `nenhum`: sem recorte."""
    nome: Nome
    rotulo: str
    tipo: Literal["periodo", "data", "nenhum"]
    ausente: str | None = None  # o que o cabeçalho de cada chunk diz sem ele

    def texto_ausente(self) -> str:
        return self.ausente or f"{self.rotulo} não identificado"



class Metadado(_Bloco):
    """Um dado do documento inteiro, extraído na indexação. Só vale com o trecho da página que o
    prova (a regra de conferência é do indexador, pelo `tipo`)."""
    nome: Nome
    rotulo: str
    instrucao: str
    tipo: Literal["texto", "periodo", "data"] = "texto"
    papel: Literal["entidade", "recorte", "tipo_documento"] | None = None



class Documento(_Bloco):
    tipos: list[str]
    # Frases típicas das primeiras páginas: a sugestão de domínio no envio compara o PDF com elas.
    reconhecer: list[str] = []


class Campo(_Bloco):
    """Um dado que o extrator lê do documento, sempre apontando a origem."""
    nome: Nome
    rotulo: str
    tipo: Literal["valor", "percentual", "multiplo", "numero", "data", "texto"]
    principal: bool
    termos: list[str] = []  # procurados quando o agente diz que o campo não existe
    secao: str | None = None  # a seção de origem deve ter esta palavra (outra gera alerta)
    mencao: list[str] = []  # o que a linha de origem tem de mencionar; "a+b" exige as duas
    demonstracao: bool = False  # vem de tabela, nunca de frase
    unidade: str | None = None  # a unidade esperada quando não é dinheiro (ex.: mg/dL)



class Categoria(_Bloco):
    """Uma categoria da análise qualitativa, cada item com citação."""
    nome: Nome
    rotulo: str
    termos: list[str] = []  # procurados quando o agente diz que o documento não traz a categoria



class Pergunta(_Bloco):
    id: str
    enunciado: str

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if value == "livre" or not value[:1].isalpha():
            raise ValueError(f"id de pergunta inválido: {value!r}")
        return value


class Opcao(_Bloco):
    titulo: str
    exemplo: str


class Textos(_Bloco):
    """O que a interface e o chat dizem neste domínio."""
    titulo: str  # nome do sistema na tela
    envio: str  # o convite para enviar PDFs
    fora_do_escopo: str  # a resposta fixa a uma mensagem fora do domínio
    opcoes: list[Opcao]  # sugestões mostradas com o fora do escopo e na conversa vazia


class Pacote(_Bloco):
    id: Nome
    nome: str
    descricao: str
    plugin: str | None = None  # regras em código de cada sistema (ex.: fórmulas); None = só YAML
    documento: Documento
    entidade: Rotulo
    recorte: Recorte
    metadados: list[Metadado]
    campos: list[Campo]
    categorias: list[Categoria]
    perguntas: list[Pergunta]
    textos: Textos


    @model_validator(mode="after")
    def _coerente(self) -> "Pacote":
        for rotulo, nomes in (("metadado", [m.nome for m in self.metadados]), ("campo", [c.nome for c in self.campos]),
                              ("categoria", [c.nome for c in self.categorias]),
                              ("pergunta", [p.id for p in self.perguntas])):
            repetidos = sorted({nome for nome in nomes if nomes.count(nome) > 1})
            if repetidos:
                raise ValueError(f"{rotulo} repetido: {', '.join(repetidos)}")
        if not any(c.principal for c in self.campos) or not self.categorias:
            raise ValueError("é preciso ao menos um campo principal e uma categoria")
        papeis = [m.papel for m in self.metadados if m.papel]
        if papeis.count("entidade") != 1:
            raise ValueError("é preciso exatamente um metadado com papel 'entidade' (de quem é o documento)")
        if self.recorte.tipo != "nenhum" and papeis.count("recorte") != 1:
            raise ValueError("com recorte, é preciso exatamente um metadado com papel 'recorte'")
        return self

    def metadado(self, papel: str) -> Metadado | None:
        return next((m for m in self.metadados if m.papel == papel), None)


class PacoteInvalido(RuntimeError):
    pass


def pasta_dos_dominios() -> Path:
    """`DOMINIOS_PATH`, ou a pasta `dominios/` do contêiner (/app/dominios) ou da raiz do projeto."""
    if os.environ.get("DOMINIOS_PATH"):
        return Path(os.environ["DOMINIOS_PATH"])
    aqui = Path(__file__).resolve()
    for candidata in (aqui.parents[1] / "dominios", aqui.parents[2] / "dominios"):
        if candidata.is_dir():
            return candidata
    return aqui.parents[2] / "dominios"


def ler(pasta: Path) -> Pacote:
    """Um pacote, da pasta dele. Inválido: PacoteInvalido com o arquivo e o motivo."""
    arquivo = pasta / "dominio.yaml"
    try:
        bruto: Any = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
        pacote = Pacote.model_validate(bruto)
    except (OSError, ValueError, TypeError, yaml.YAMLError) as error:  # ValidationError é um ValueError
        raise PacoteInvalido(f"{arquivo}: {error}") from error
    if pacote.id != pasta.name:
        raise PacoteInvalido(f"{arquivo}: o id ({pacote.id}) tem de ser o nome da pasta ({pasta.name})")
    return pacote


@cache
def todos(pasta: Path | None = None) -> dict[str, Pacote]:
    """Os pacotes de `dominios/`, por id. Um pacote inválido impede a subida, com o motivo."""
    raiz = pasta or pasta_dos_dominios()
    pacotes = {sub.name: ler(sub) for sub in sorted(raiz.iterdir()) if (sub / "dominio.yaml").is_file()} \
        if raiz.is_dir() else {}
    if PADRAO not in pacotes:
        raise PacoteInvalido(f"{raiz}: falta o pacote padrão '{PADRAO}'")
    return pacotes


def pacote(dominio: str | None = None) -> Pacote:
    """O pacote de um domínio (None: o padrão). Domínio desconhecido: PacoteInvalido."""
    pacotes = todos()
    escolhido = dominio or PADRAO
    if escolhido not in pacotes:
        raise PacoteInvalido(f"domínio desconhecido: {escolhido!r}. Existem: {', '.join(pacotes)}")
    return pacotes[escolhido]


def arquivo_do_pacote(dominio: str | None, relativo: str) -> Path | None:
    """Um arquivo opcional da pasta do pacote (ex.: prompts/extrator.md), se existir."""
    caminho = pasta_dos_dominios() / (dominio or PADRAO) / relativo
    return caminho if caminho.is_file() else None
