import { json, redirect, type Handle } from '@sveltejs/kit';
import { COOKIE_TOKEN, COOKIE_USUARIO, fechar, tokenValido } from '$lib/server/sessao';

const PUBLICAS = ['/login'];

/**
 * Toda requisição passa aqui: lê a sessão do cookie e barra quem não entrou.
 * Token vencido ou perto de vencer encerra a sessão (ver `sessao.ts`).
 */
export const handle: Handle = async ({ event, resolve }) => {
	let token = event.cookies.get(COOKIE_TOKEN) ?? null;
	if (token && !tokenValido(token)) {
		fechar(event.cookies);
		token = null;
	}
	event.locals.token = token;
	event.locals.usuario = token ? (event.cookies.get(COOKIE_USUARIO) ?? null) : null;

	const caminho = event.url.pathname;
	if (!token && !PUBLICAS.some((p) => caminho.startsWith(p))) {
		if (caminho.startsWith('/api/')) {
			return json({ detail: 'Sessão expirada. Entre de novo.' }, { status: 401 });
		}
		redirect(303, '/login');
	}
	return resolve(event);
};
