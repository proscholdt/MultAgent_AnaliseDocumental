"""Telemetria: traces e métricas por OpenTelemetry, enviados (OTLP/HTTP) ao coletor da pilha de
observabilidade (Grafana Alloy), que repassa os traces ao Tempo e as métricas ao Prometheus. Os
logs não passam por aqui: o coletor lê a saída dos contêineres e manda ao Loki.

Liga sozinha quando `OTEL_EXPORTER_OTLP_ENDPOINT` está definido. Sem ele, nada é exportado, e os
spans e métricas do código viram operações vazias. Se o coletor estiver fora do ar, o serviço
segue: os erros de exportação são silenciados.

Cada linha de log ganha `trace_id=` e `span_id=`: no Grafana, a linha leva ao trace dela.
"""
import logging
import os
from typing import Any

from opentelemetry import context, metrics, propagate, trace

FORMATO_LOG = ("%(asctime)s level=%(levelname)s logger=%(name)s trace_id=%(otelTraceID)s "
               "span_id=%(otelSpanID)s %(message)s")

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
    provedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provedor)
    leitor = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=10_000)
    metrics.set_meter_provider(MeterProvider(resource=recurso, metric_readers=[leitor]))
    HTTPXClientInstrumentor().instrument()  # chamadas de saída levam o traceparent adiante
    LoggingInstrumentor().instrument(set_logging_format=True, logging_format=FORMATO_LOG, log_level=logging.INFO)
    logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)  # coletor fora do ar não vira ruído
    _ligada = True
    return True


def instrumentar(app: Any) -> None:
    """Um span por requisição recebida pela API (menos /health)."""
    if _ligada:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health")


def tracer() -> trace.Tracer:
    return trace.get_tracer("demonstracoes")


def medidor() -> metrics.Meter:
    return metrics.get_meter("demonstracoes")


def portador() -> dict[str, str]:
    """O contexto do trace atual, para ir junto numa mensagem (fila, por exemplo)."""
    carga: dict[str, str] = {}
    propagate.inject(carga)
    return carga


def contexto(carga: dict[str, str] | None) -> context.Context:
    """O contexto que veio numa mensagem, para o trabalho continuar o mesmo trace."""
    return propagate.extract(carga or {})
