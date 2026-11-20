"""Telemetria: traces e métricas por OpenTelemetry, enviados (OTLP/HTTP) ao coletor da pilha de
observabilidade (Grafana Alloy), que repassa os traces ao Tempo e as métricas ao Prometheus. Os
logs não passam por aqui: o coletor lê a saída dos contêineres e manda ao Loki.

Liga sozinha quando `OTEL_EXPORTER_OTLP_ENDPOINT` está definido. Sem ele, nada é exportado, e os
spans e métricas do código viram operações vazias. Se o coletor estiver fora do ar, o serviço
segue: os erros de exportação são silenciados.

Cada linha de log ganha `trace_id=` e `span_id=`: no Grafana, a linha leva ao trace dela.

A conversa viaja junto: o orquestrador põe o id dela no baggage (`conversa(...)`), o baggage segue
o traceparent em toda chamada HTTP, e cada serviço copia o id para os spans que abre e para as
linhas de log (`conversa=`). No Grafana, a visão geral filtra uma conversa inteira por ele.
"""
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import baggage, context, metrics, propagate, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.sdk.trace import Span, SpanProcessor

FORMATO_LOG = ("%(asctime)s level=%(levelname)s logger=%(name)s trace_id=%(otelTraceID)s "
               "span_id=%(otelSpanID)s%(conversa)s %(message)s")
CONVERSA = "conversa.id"

_ligada = False


def ligada() -> bool:
    return _ligada


def configurar(servico: str) -> bool:
    """Liga a exportação para este processo. Pode ser chamada mais de uma vez."""
    global _ligada
    if _ligada or not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return _ligada
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    recurso = Resource.create({"service.name": servico, "deployment.environment": os.getenv("AMBIENTE", "local")})
    provedor = TracerProvider(resource=recurso)
    provedor.add_span_processor(ConversaNosSpans())
    provedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provedor)
    leitor = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=10_000)
    metrics.set_meter_provider(MeterProvider(resource=recurso, metric_readers=[leitor]))
    HTTPXClientInstrumentor().instrument()  # chamadas de saída levam o traceparent adiante
    LoggingInstrumentor().instrument(set_logging_format=True, logging_format=FORMATO_LOG, log_level=logging.INFO)
    _conversa_nos_logs()
    logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)  # coletor fora do ar não vira ruído
    _ligada = True
    return True


class ConversaNosSpans(SpanProcessor):
    """Todo span aberto com uma conversa no baggage leva o id dela."""

    def on_start(self, span: Span, parent_context: context.Context | None = None) -> None:
        conversa = baggage.get_baggage(CONVERSA, parent_context)
        if conversa:
            span.set_attribute(CONVERSA, str(conversa))


def _conversa_nos_logs() -> None:
    """Toda linha de log escrita com uma conversa no baggage ganha `conversa=<id>`."""
    fabrica = logging.getLogRecordFactory()

    def com_conversa(*args: Any, **kwargs: Any) -> logging.LogRecord:
        registro = fabrica(*args, **kwargs)
        conversa = baggage.get_baggage(CONVERSA)
        registro.conversa = f" conversa={conversa}" if conversa else ""
        return registro

    logging.setLogRecordFactory(com_conversa)


@contextmanager
def conversa(conversa_id: str | None) -> Iterator[None]:
    """Enquanto o bloco roda, a conversa está no baggage: os spans e os logs deste serviço e dos
    que ele chamar levam o id dela."""
    if not conversa_id:
        yield
        return
    token = context.attach(baggage.set_baggage(CONVERSA, conversa_id))
    try:
        yield
    finally:
        context.detach(token)


def cabecalho_da_conversa() -> dict[str, str]:
    """O baggage de agora como cabeçalho HTTP, para um cliente que a instrumentação não alcança
    (o do MCP): sem ele, o servidor do outro lado não sabe de que conversa é a chamada."""
    carga: dict[str, str] = {}
    W3CBaggagePropagator().inject(carga)
    return carga


def conversa_dos_cabecalhos(cabecalhos: Mapping[str, str]) -> str | None:
    """A conversa do baggage de uma requisição HTTP, para quem atende fora do contexto dela (o SDK
    do MCP leva o traceparent até a tool, mas não o baggage)."""
    valor = baggage.get_baggage(CONVERSA, propagate.extract({k.lower(): v for k, v in cabecalhos.items()}))
    return str(valor) if valor else None


def instrumentar(app: Any) -> None:
    """Um span por requisição recebida pela API (menos /health)."""
    if _ligada:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health")


def tracer() -> trace.Tracer:
    return trace.get_tracer("analise-documentos")


def medidor() -> metrics.Meter:
    return metrics.get_meter("analise-documentos")


def portador() -> dict[str, str]:
    """O contexto do trace atual, para ir junto numa mensagem (fila, por exemplo)."""
    carga: dict[str, str] = {}
    propagate.inject(carga)
    return carga


def contexto(carga: dict[str, str] | None) -> context.Context:
    """O contexto que veio numa mensagem, para o trabalho continuar o mesmo trace."""
    return propagate.extract(carga or {})
