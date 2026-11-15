"""A conversa no baggage: vai para os spans, atravessa a chamada a outro serviço e entra nos logs."""
import logging

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import telemetria


def test_conversa_vai_para_os_spans_e_atravessa_a_chamada():
    exportador = InMemorySpanExporter()
    provedor = TracerProvider()
    provedor.add_span_processor(telemetria.ConversaNosSpans())
    provedor.add_span_processor(SimpleSpanProcessor(exportador))
    tracer = provedor.get_tracer("teste")

    with telemetria.conversa("conv-abc"), tracer.start_as_current_span("dentro"):
        carga = telemetria.portador()  # o que vai no cabeçalho da chamada HTTP
    with tracer.start_as_current_span("fora"):
        pass
    with tracer.start_as_current_span("no outro servico", context=telemetria.contexto(carga)):
        pass

    conversas = {span.name: span.attributes.get("conversa.id") for span in exportador.get_finished_spans()}
    assert conversas == {"dentro": "conv-abc", "fora": None, "no outro servico": "conv-abc"}
    assert "conversa.id=conv-abc" in carga["baggage"]


def test_linha_de_log_ganha_a_conversa():
    original = logging.getLogRecordFactory()
    try:
        telemetria._conversa_nos_logs()
        formato = logging.Formatter("antes%(conversa)s depois")

        def linha() -> str:
            return formato.format(logging.getLogRecordFactory()("x", logging.INFO, __file__, 1, "m", None, None))

        with telemetria.conversa("conv-abc"):
            assert linha() == "antes conversa=conv-abc depois"
        assert linha() == "antes depois"
    finally:
        logging.setLogRecordFactory(original)


def test_conversa_lida_dos_cabecalhos_da_requisicao():
    with telemetria.conversa("conv-abc"):
        cabecalhos = {nome.title(): valor for nome, valor in telemetria.portador().items()}  # "Baggage: ..."
    assert telemetria.conversa_dos_cabecalhos(cabecalhos) == "conv-abc"
    assert telemetria.conversa_dos_cabecalhos({"authorization": "Bearer x"}) is None


def test_cabecalho_da_conversa_para_o_cliente_do_mcp():
    with telemetria.conversa("conv-abc"):
        cabecalho = telemetria.cabecalho_da_conversa()
    assert cabecalho == {"baggage": "conversa.id=conv-abc"}
    assert telemetria.conversa_dos_cabecalhos(cabecalho) == "conv-abc"
    assert telemetria.cabecalho_da_conversa() == {}


def test_sem_conversa_nao_mexe_no_contexto():
    with telemetria.conversa(""):
        assert telemetria.portador().get("baggage") is None
