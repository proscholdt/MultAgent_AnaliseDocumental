"""As métricas dos agentes (OpenTelemetry → Prometheus): tokens, custo, delegações e execuções."""


from app import telemetria
from app.nucleo.agentes import ORCHESTRATOR

_medidor = telemetria.medidor()

_TOKENS = _medidor.create_counter("agentes.tokens", description="Tokens por agente, modelo e tipo")

_CUSTO = _medidor.create_counter("agentes.custo_usd", description="Custo em US$ por agente e modelo")

_DELEGACOES = _medidor.create_counter("agentes.delegacoes", description="Delegações por especialista e estado")

_ESPECIALISTA_S = _medidor.create_histogram("agentes.especialista.duracao", unit="s",
                                            description="Duração de cada chamada a um especialista")

_EXECUCOES = _medidor.create_counter("agentes.execucoes", description="Execuções por tipo e estado")

_EXECUCAO_S = _medidor.create_histogram("agentes.execucao.duracao", unit="s", description="Duração das execuções")

_INTENCOES = _medidor.create_counter("agentes.intencoes", description="Intenção das mensagens do chat, por quem decidiu")

def zerar_metricas(agentes: list[str], modelos: list[str]) -> None:
    """Cria as séries dos contadores em zero, na subida. Um contador do OpenTelemetry só passa a
    existir no primeiro incremento, e o Prometheus nunca vê o zero de antes: increase() perde o
    primeiro evento. Com as séries conhecidas já em zero, a conta fecha."""
    for tipo in ("pergunta", "relatorio", "relatorio_documento", "livre", "fora_do_escopo"):
        for estado in ("completa", "incompleta", "falhou"):
            _EXECUCOES.add(0, {"tipo": tipo, "estado": estado})
    for agente in agentes:
        for modelo in modelos:
            _CUSTO.add(0, {"agente": agente, "modelo": modelo})
            for tipo in ("entrada", "cache", "saida"):
                _TOKENS.add(0, {"agente": agente, "modelo": modelo, "tipo": tipo})
        if agente != ORCHESTRATOR:
            for estado in ("concluido", "limite_de_turnos", "orcamento_esgotado", "tempo_esgotado", "sem_permissao",
                           "erro"):
                _DELEGACOES.add(0, {"agente": agente, "estado": estado})

_STOPPED = ("limite_de_turnos", "orcamento_esgotado", "tempo_esgotado")


def registrar_uso(agente: str, modelo: str, uso: dict, custo: float | None) -> None:
    """Tokens e custo de uma chamada nas métricas e no span do nó (grafo.orquestrador,
    grafo.delegar): o Grafana soma por conversa."""
    for tipo, chave in (("entrada", "entrada"), ("cache", "entrada_cache"), ("saida", "saida")):
        if uso.get(chave):
            _TOKENS.add(int(uso[chave]), {"agente": agente, "modelo": modelo or "?", "tipo": tipo})
    if custo:
        _CUSTO.add(custo, {"agente": agente, "modelo": modelo})
    entrada, saida = int(uso.get("entrada") or 0), int(uso.get("saida") or 0)
    telemetria.trace.get_current_span().set_attributes({
        "agente": agente, "agente.modelo": modelo or "?", "agente.custo_usd": float(custo or 0.0),
        "agente.tokens": entrada + saida, "agente.tokens_entrada": entrada,
        "agente.tokens_cache": int(uso.get("entrada_cache") or 0), "agente.tokens_saida": saida})

