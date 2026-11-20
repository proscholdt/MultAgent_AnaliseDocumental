"""Tabelas do servidor MCP. A chave de tudo é usuário + documento: o doc_id é o hash do
arquivo e se repete quando dois usuários enviam o mesmo PDF."""
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.dados.banco import Base


def now() -> datetime:
    return datetime.now(UTC)


class StoredValue(Base):
    """Um valor que passou pelo portão: um campo, de um período, de um documento.

    Salvar de novo o mesmo campo desativa o registro anterior, que fica como histórico."""

    __tablename__ = "valores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    doc_id: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(80))  # impressão do índice em que o valor foi lido
    run_id: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(40))
    campo: Mapped[str] = mapped_column(String(40))
    periodo: Mapped[str] = mapped_column(String(32))  # 2022, 3T2025; num laudo, a data (12/03/2024)
    medida: Mapped[str] = mapped_column(String(20))  # "valor" | "variacao_informada"
    status: Mapped[str] = mapped_column(String(16))  # "encontrado" | "nao_encontrado"
    origem: Mapped[str | None] = mapped_column(String(8), nullable=True)  # "tabela" | "texto"
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pagina: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pagina_final: Mapped[int | None] = mapped_column(Integer, nullable=True)
    secao: Mapped[str] = mapped_column(Text, default="")
    linha: Mapped[str | None] = mapped_column(Text, nullable=True)
    coluna: Mapped[str | None] = mapped_column(Text, nullable=True)
    trecho: Mapped[str | None] = mapped_column(Text, nullable=True)  # o que está escrito na fonte
    texto_valor: Mapped[str | None] = mapped_column(Text, nullable=True)  # o valor como escrito (número ou texto)
    valor: Mapped[str | None] = mapped_column(String(64), nullable=True)  # o número lido (Decimal em texto)
    tipo: Mapped[str | None] = mapped_column(String(12), nullable=True)  # valor | percentual | multiplo
    unidade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unidade_origem: Mapped[str | None] = mapped_column(String(16), nullable=True)
    escopo: Mapped[str | None] = mapped_column(String(16), nullable=True)  # consolidado | controladora
    # genérico: coluna da base (o plugin diz se a tabela tinha um escopo melhor que o escolhido).
    alternativa_consolidada: Mapped[bool] = mapped_column(default=False)
    periodo_confirmado: Mapped[bool] = mapped_column(default=True)  # o período está escrito na fonte citada?
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidencia: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)  # observação do agente, não conferida
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ativo: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (Index("ix_valores_doc", "user_id", "doc_id", "ativo"),)


class Extraction(Base):
    """Extração publicada de um documento: o JSON que o teste pede, montado pelo código."""

    __tablename__ = "extracoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    doc_id: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(80))
    run_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (Index("ix_extracoes_doc", "user_id", "doc_id"),)


class Alert(Base):
    """Alerta de validação ou de inconsistência na fonte. Só é acrescentado: reextrair até uma
    checagem passar não apaga o que foi sinalizado antes."""

    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    doc_id: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(80))
    run_id: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(40))
    tipo: Mapped[str] = mapped_column(String(12))  # "validacao" (código) | "fonte" (agente, com citação)
    codigo: Mapped[str] = mapped_column(String(40))
    severidade: Mapped[str] = mapped_column(String(12))  # "atencao" | "informacao"
    campo: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mensagem: Mapped[str] = mapped_column(Text)
    detalhes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    digest: Mapped[str] = mapped_column(String(32))  # o mesmo alerta não é gravado duas vezes
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (Index("ix_alertas_doc", "user_id", "doc_id"),)


class AnalysisItem(Base):
    """Um item da análise qualitativa, com a citação literal que o sustenta."""

    __tablename__ = "analise"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    doc_id: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(80))
    run_id: Mapped[str] = mapped_column(String(64))
    categoria: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16))  # "encontrado" | "nao_encontrado"
    afirmacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    citacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pagina: Mapped[int | None] = mapped_column(Integer, nullable=True)
    secao: Mapped[str] = mapped_column(Text, default="")
    fonte: Mapped[str | None] = mapped_column(String(32), nullable=True)  # tipo da fonte, pela seção
    tom: Mapped[str | None] = mapped_column(String(12), nullable=True)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidencia: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ativo: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (Index("ix_analise_doc", "user_id", "doc_id", "ativo"),)


class Answer(Base):
    """Resposta a uma pergunta, com as citações conferidas."""

    __tablename__ = "respostas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    # As perguntas comparam os documentos de uma coleção: a p1 dos laudos não é a p1 do financeiro.
    # Vazio: execução sem coleção (todos os documentos do usuário).
    colecao_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    run_id: Mapped[str] = mapped_column(String(64))
    pergunta_id: Mapped[str] = mapped_column(String(40))
    pergunta: Mapped[str] = mapped_column(Text)
    texto: Mapped[str] = mapped_column(Text)  # como o agente escreveu, com marcadores
    renderizado: Mapped[str] = mapped_column(Text)  # com números e citações postos pelo código
    citacoes: Mapped[list] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ativo: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (Index("ix_respostas_user", "user_id", "ativo"),)


class Summary(Base):
    """Resumo executivo de uma empresa."""

    __tablename__ = "resumos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    doc_id: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(80))
    run_id: Mapped[str] = mapped_column(String(64))
    texto: Mapped[str] = mapped_column(Text)
    renderizado: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ativo: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (Index("ix_resumos_doc", "user_id", "doc_id", "ativo"),)


class Report(Base):
    """Relatório consolidado montado pelo código."""

    __tablename__ = "relatorios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    colecao_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)  # um por coleção
    run_id: Mapped[str] = mapped_column(String(64))
    markdown: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (Index("ix_relatorios_user", "user_id"),)


class CompanyReport(Base):
    """Relatório de uma empresa só, montado pelo código. Vale para a impressão do documento em que
    foi montado: se o documento for reindexado, ele deixa de valer."""

    __tablename__ = "relatorios_empresa"  # genérico: nome da tabela na base, de antes dos domínios

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    doc_id: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(80))
    run_id: Mapped[str] = mapped_column(String(64))
    markdown: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (Index("ix_relatorios_empresa_doc", "user_id", "doc_id"),)  # genérico: nome na base


class AuditRow(Base):
    """Uma linha por listagem ou chamada de tool. Só é acrescentada, nunca alterada."""

    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    run_id: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))  # "lista" | "chamada"
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ok | recusada (política ou limite) | invalida (argumentos) | rejeitada (portão) | erro
    outcome: Mapped[str] = mapped_column(String(12))
    detail: Mapped[str] = mapped_column(Text, default="")
    arguments: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    policy_digest: Mapped[str] = mapped_column(String(16))

    __table_args__ = (Index("ix_auditoria_run_agent", "run_id", "agent"),)


class CallCounter(Base):
    """Chamadas de tool de um agente numa execução. O incremento é um só comando (upsert), então o
    limite vale mesmo com várias réplicas do servidor atendendo o mesmo agente ao mesmo tempo."""

    __tablename__ = "contagem_chamadas"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent: Mapped[str] = mapped_column(String(40), primary_key=True)
    chamadas: Mapped[int] = mapped_column(Integer, default=0)
