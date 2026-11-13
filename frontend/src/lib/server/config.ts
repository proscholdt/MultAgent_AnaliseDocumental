import { env } from '$env/dynamic/private';
import type { Sistema } from './rotas';

/** Endereço de cada sistema, visto do servidor do SvelteKit (no Docker, pelos nomes da rede). */
export function base(sistema: Sistema): string {
	const url = sistema === 'indexador' ? env.INDEXER_URL || 'http://localhost:8000' : env.AGENTS_URL || 'http://localhost:8200';
	return url.replace(/\/+$/, '');
}

/** Cookie só por HTTPS? Em produção, sim; em http://localhost, o navegador não o guardaria. */
export function cookieSeguro(): boolean {
	return (env.COOKIE_SECURE ?? 'false').toLowerCase() === 'true';
}
