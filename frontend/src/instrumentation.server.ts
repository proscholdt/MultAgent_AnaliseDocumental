/**
 * Telemetria do servidor da tela (OpenTelemetry). Carregado pelo SvelteKit antes de tudo.
 *
 * Liga só com OTEL_EXPORTER_OTLP_ENDPOINT definido. Os spans do SvelteKit (handle, load, actions)
 * e as chamadas `fetch` às APIs (instrumentação do undici, que leva o traceparent adiante) vão ao
 * coletor da pilha de observabilidade. Assim uma pergunta no chat é um trace só, da tela até o
 * indexador.
 */
import { diag, DiagLogLevel } from '@opentelemetry/api';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-proto';
import { UndiciInstrumentation } from '@opentelemetry/instrumentation-undici';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { NodeSDK } from '@opentelemetry/sdk-node';

if (process.env.OTEL_EXPORTER_OTLP_ENDPOINT) {
	diag.setLogger({ error() {}, warn() {}, info() {}, debug() {}, verbose() {} }, DiagLogLevel.NONE);
	const sdk = new NodeSDK({
		resource: resourceFromAttributes({
			'service.name': 'frontend',
			'deployment.environment': process.env.AMBIENTE ?? 'local'
		}),
		traceExporter: new OTLPTraceExporter(),
		instrumentations: [new UndiciInstrumentation()]
	});
	sdk.start();
	process.on('SIGTERM', () => {
		sdk.shutdown().finally(() => process.exit(0));
	});
}
