"""A avaliação ao fim de cada relatório: tira o retrato da base, compara com o gabarito e publica as
notas como métricas (Prometheus, painel "Agentes"). Só conta e leitura: não chama modelo.

Documento sem gabarito não é avaliado: a execução registra quais ficaram de fora. Sem nenhum
documento com gabarito, não há nota, e a execução diz por quê.
"""
import json
import logging
from typing import Any

from app import telemetria
from app.config import Settings
from app.ferramentas.avaliacao import evaluate
from app.ferramentas.retrato import snapshot
from app.nucleo.tokens import Identity

log = logging.getLogger(__name__)

_AVALIACOES = telemetria.medidor().create_counter(
    "agentes.avaliacoes", description="Avaliações ao fim de um relatório, por resultado")
_ACERTOS = telemetria.medidor().create_gauge(
    "agentes.avaliacao.acertos", description="Acertos na última avaliação contra o gabarito, por medida")
_DE = telemetria.medidor().create_gauge(
    "agentes.avaliacao.de", description="Quantos itens a última avaliação conferiu, por medida")
_TAXA = telemetria.medidor().create_gauge(
    "agentes.avaliacao.taxa", description="Taxa de acerto da última avaliação, de 0 a 1, por medida")

# As medidas do resumo que viram métrica. "invencao" é ao contrário: quanto menos, melhor.
MEDIDAS = ("status_certo", "valor_informado_certo", "pagina_certa", "ano_anterior_certo", "valor_calculado_certo",
           "alertas_automaticos", "inconsistencias_na_fonte", "perguntas_certas", "invencao", "citacoes_validas")


def zerar_metricas() -> None:
    for resultado in ("avaliada", "sem_gabarito", "erro"):
        _AVALIACOES.add(0, {"resultado": resultado})


def _publish(summary: dict[str, Any], tipo: str) -> None:
    for medida in MEDIDAS:
        item = summary.get(medida) or {}
        acertos = item.get("inventados") if medida == "invencao" else item.get("validas") if medida == "citacoes_validas" \
            else item.get("acertos")
        de = item.get("total") if medida == "citacoes_validas" else item.get("de")
        if not de:
            continue  # nada conferido nesta medida (por exemplo, perguntas num relatório de uma empresa)
        labels = {"medida": medida, "tipo": tipo}
        _ACERTOS.set(acertos, labels)
        _DE.set(de, labels)
        _TAXA.set(round(acertos / de, 4), labels)
        # E no span da execução, para o Grafana mostrar a nota de uma conversa.
        telemetria.trace.get_current_span().set_attribute(
            f"avaliacao.{medida}", acertos if medida == "invencao" else round(acertos / de, 4))


async def avaliar(settings: Settings, identity: Identity, tipo: str, doc_id: str | None) -> dict[str, Any] | None:
    """A nota do relatório que acabou de terminar, ou None se a avaliação está desligada."""
    if not settings.avaliar_relatorios:
        return None
    try:
        gabarito = json.loads(settings.gabarito_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _AVALIACOES.add(1, {"resultado": "sem_gabarito"})
        return {"avaliado": False, "motivo": f"gabarito não encontrado em {settings.gabarito_path}"}
    try:
        retrato = await snapshot(settings, identity)
        report = evaluate(gabarito, retrato, {doc_id} if doc_id else None)
    except Exception as error:  # noqa: BLE001 - a nota é um extra: a falha dela não derruba o relatório
        log.warning("avaliação não feita: %s", error)
        _AVALIACOES.add(1, {"resultado": "erro"})
        return {"avaliado": False, "motivo": f"{type(error).__name__}: {error}"[:300]}
    if not report["avaliados"]:
        _AVALIACOES.add(1, {"resultado": "sem_gabarito"})
        return {"avaliado": False, "motivo": "nenhum documento deste relatório tem gabarito",
                "sem_gabarito": report["sem_gabarito"]}
    _AVALIACOES.add(1, {"resultado": "avaliada"})
    _publish(report["resumo"], tipo)
    return {"avaliado": True, "avaliados": report["avaliados"], "sem_gabarito": report["sem_gabarito"],
            "perguntas_nao_avaliadas": report["perguntas_nao_avaliadas"], "resumo": report["resumo"]}
