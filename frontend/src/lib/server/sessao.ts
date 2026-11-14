/**
 * A sessão do usuário: o JWT do indexador num cookie httpOnly.
 *
 * O JavaScript da página nunca vê o token. O servidor do SvelteKit o lê do cookie e o põe no
 * cabeçalho das chamadas ao indexador e ao orquestrador.
 *
 * O token vale 60 minutos (`JWT_EXPIRES_MINUTES` do indexador), e o orquestrador recusa começar
 * uma execução com menos de 15 minutos restantes, porque as buscas falhariam no meio. Por isso a
 * sessão termina antes: com menos de `minutos_minimos` (config/ajustes.yaml), o usuário volta ao login.
 */
import type { Cookies } from '@sveltejs/kit';
import { ajustes } from './ajustes';

export const COOKIE_TOKEN = 'sessao';
export const COOKIE_USUARIO = 'usuario';

/** O `exp` do JWT, em segundos, lido sem conferir a assinatura (quem confere é o indexador). */
export function vencimento(token: string): number | null {
	const partes = token.split('.');
	if (partes.length !== 3) return null;
	try {
		const base64 = partes[1].replace(/-/g, '+').replace(/_/g, '/');
		const payload = JSON.parse(atob(base64));
		return typeof payload.exp === 'number' ? payload.exp : null;
	} catch {
		return null;
	}
}

/** Minutos até o token vencer; `null` quando não dá para saber. */
export function minutosRestantes(token: string, agora: number = Date.now()): number | null {
	const exp = vencimento(token);
	return exp === null ? null : (exp * 1000 - agora) / 60_000;
}

/** O token ainda serve para começar uma execução? */
export function tokenValido(token: string, agora: number = Date.now(), minimos = ajustes().minutos_minimos): boolean {
	const minutos = minutosRestantes(token, agora);
	return minutos !== null && minutos >= minimos;
}

function opcoes(segura: boolean, segundos: number) {
	return { path: '/', httpOnly: true, sameSite: 'strict' as const, secure: segura, maxAge: segundos };
}

export function abrir(cookies: Cookies, token: string, usuario: string, validadeSegundos: number, segura: boolean) {
	cookies.set(COOKIE_TOKEN, token, opcoes(segura, validadeSegundos));
	cookies.set(COOKIE_USUARIO, usuario, opcoes(segura, validadeSegundos));
}

export function fechar(cookies: Cookies) {
	cookies.delete(COOKIE_TOKEN, { path: '/' });
	cookies.delete(COOKIE_USUARIO, { path: '/' });
}
