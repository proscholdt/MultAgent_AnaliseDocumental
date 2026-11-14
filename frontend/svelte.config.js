import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		// Servidor Node: além das páginas, ele faz o login (cookie httpOnly) e repassa as chamadas
		// ao indexador e ao orquestrador. O navegador nunca vê o token.
		adapter: adapter(),
		// Spans de handle, load e actions, e src/instrumentation.server.ts (OpenTelemetry).
		experimental: {
			tracing: { server: true },
			instrumentation: { server: true }
		}
	}
};

export default config;
