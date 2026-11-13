import { fail, redirect, type Actions } from '@sveltejs/kit';
import { base, cookieSeguro } from '$lib/server/config';
import { abrir } from '$lib/server/sessao';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ locals, url }) => {
	if (locals.token) redirect(303, '/');
	return { expirada: url.searchParams.has('expirada') };
};

async function entrar(usuario: string, senha: string) {
	const resposta = await fetch(`${base('indexador')}/auth/login`, {
		method: 'POST',
		headers: { 'content-type': 'application/x-www-form-urlencoded' },
		body: new URLSearchParams({ username: usuario, password: senha })
	});
	if (!resposta.ok) return null;
	return (await resposta.json()) as { access_token: string; expires_in: number };
}

function campos(dados: FormData) {
	return { usuario: String(dados.get('usuario') ?? '').trim(), senha: String(dados.get('senha') ?? '') };
}

export const actions: Actions = {
	entrar: async ({ request, cookies }) => {
		const { usuario, senha } = campos(await request.formData());
		if (!usuario || !senha) return fail(400, { usuario, erro: 'Informe usuário e senha.' });
		let sessao;
		try {
			sessao = await entrar(usuario, senha);
		} catch {
			return fail(502, { usuario, erro: 'O indexador não respondeu. Ele está no ar?' });
		}
		if (!sessao) return fail(401, { usuario, erro: 'Usuário ou senha inválidos.' });
		abrir(cookies, sessao.access_token, usuario, sessao.expires_in, cookieSeguro());
		redirect(303, '/');
	},

	criar: async ({ request, cookies }) => {
		const { usuario, senha } = campos(await request.formData());
		if (usuario.length < 3) return fail(400, { usuario, criar: true, erro: 'O usuário precisa de pelo menos 3 caracteres.' });
		if (senha.length < 8) return fail(400, { usuario, criar: true, erro: 'A senha precisa de pelo menos 8 caracteres.' });
		let resposta: Response;
		try {
			resposta = await fetch(`${base('indexador')}/auth/register`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ username: usuario, password: senha })
			});
		} catch {
			return fail(502, { usuario, criar: true, erro: 'O indexador não respondeu. Ele está no ar?' });
		}
		if (resposta.status === 409) return fail(409, { usuario, criar: true, erro: 'Esse nome de usuário já existe.' });
		if (resposta.status === 422) {
			return fail(422, { usuario, criar: true, erro: 'Use só letras, números e . _ @ - no nome de usuário.' });
		}
		if (!resposta.ok) return fail(resposta.status, { usuario, criar: true, erro: 'Não foi possível criar a conta.' });
		const sessao = await entrar(usuario, senha);
		if (!sessao) return fail(500, { usuario, erro: 'Conta criada, mas o login falhou. Tente entrar.' });
		abrir(cookies, sessao.access_token, usuario, sessao.expires_in, cookieSeguro());
		redirect(303, '/');
	}
};
