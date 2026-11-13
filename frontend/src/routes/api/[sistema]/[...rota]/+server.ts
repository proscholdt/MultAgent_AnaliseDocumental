/**
 * O proxy: o navegador chama /api/indexador/... e /api/agentes/..., e o servidor do SvelteKit
 * repassa ao sistema certo, com o token do cookie no cabeçalho. Só passam os caminhos da lista
 * (`$lib/server/rotas`).
 */
import { json, type RequestHandler } from '@sveltejs/kit';
import { base } from '$lib/server/config';
import { ehSistema, recusa } from '$lib/server/rotas';
import { fechar } from '$lib/server/sessao';

const REPASSADOS = ['content-type', 'accept'];

export const fallback: RequestHandler = async ({ params, request, url, locals, cookies, fetch }) => {
	const sistema = params.sistema ?? '';
	const rota = params.rota ?? '';
	if (!ehSistema(sistema)) return json({ detail: 'Sistema desconhecido.' }, { status: 404 });
	const motivo = recusa(sistema, rota, request.method);
	if (motivo === 404) return json({ detail: 'Rota não disponível pela tela.' }, { status: 404 });
	if (motivo === 405) return json({ detail: 'Método não permitido.' }, { status: 405 });

	const headers = new Headers({ authorization: `Bearer ${locals.token}` });
	for (const nome of REPASSADOS) {
		const valor = request.headers.get(nome);
		if (valor) headers.set(nome, valor);
	}
	const temCorpo = !['GET', 'HEAD', 'DELETE'].includes(request.method);

	let resposta: Response;
	try {
		resposta = await fetch(`${base(sistema)}/${rota}${url.search}`, {
			method: request.method,
			headers,
			body: temCorpo ? await request.arrayBuffer() : undefined
		});
	} catch (erro) {
		const nome = sistema === 'indexador' ? 'O indexador' : 'O orquestrador';
		return json({ detail: `${nome} não respondeu. Ele está no ar?` }, { status: 502 });
	}

	if (resposta.status === 401) {
		// Token recusado lá em cima (vencido, ou perto demais de vencer para uma execução).
		fechar(cookies);
		const corpo = await resposta.json().catch(() => ({}));
		return json({ detail: corpo.detail ?? 'Sessão expirada. Entre de novo.', sessao_encerrada: true }, { status: 401 });
	}
	const saida = new Headers();
	const tipo = resposta.headers.get('content-type');
	if (tipo) saida.set('content-type', tipo);
	return new Response(resposta.status === 204 ? null : resposta.body, { status: resposta.status, headers: saida });
};
