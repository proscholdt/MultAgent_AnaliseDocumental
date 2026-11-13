/**
 * Cliente do navegador. Ele só fala com o próprio servidor do SvelteKit (/api/indexador/... e
 * /api/agentes/...), que repassa com o token do cookie. Nenhum token passa por aqui.
 */
import type { Conversa, Documento, Execucao, Job, Turno } from './tipos';

export class ErroApi extends Error {
	constructor(
		readonly status: number,
		mensagem: string
	) {
		super(mensagem);
	}
}

function detalhe(corpo: unknown, status: number): string {
	const valor = (corpo as { detail?: unknown })?.detail;
	if (typeof valor === 'string') return valor;
	if (Array.isArray(valor)) return valor.map((item) => item?.msg ?? String(item)).join('; ');
	return `Erro ${status}.`;
}

async function chamar<T>(sistema: 'indexador' | 'agentes', caminho: string, init: RequestInit = {}): Promise<T> {
	let resposta: Response;
	try {
		resposta = await fetch(`/api/${sistema}/${caminho}`, init);
	} catch {
		throw new ErroApi(0, 'Sem conexão com o servidor da tela.');
	}
	if (resposta.status === 204) return undefined as T;
	const corpo = await resposta.json().catch(() => null);
	if (!resposta.ok) {
		if (resposta.status === 401) {
			// Sessão vencida (ou perto demais de vencer para começar uma execução): volta ao login.
			window.location.href = '/login?expirada';
		}
		throw new ErroApi(resposta.status, detalhe(corpo, resposta.status));
	}
	return corpo as T;
}

function json(corpo: unknown): RequestInit {
	return { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(corpo) };
}

export const api = {
	documentos: () => chamar<Documento[]>('indexador', 'api/documents'),
	jobs: (docId: string) => chamar<Job[]>('indexador', `api/documents/${docId}/jobs`),
	enviarDocumento: (arquivo: File) => {
		const dados = new FormData();
		dados.append('file', arquivo);
		return chamar<{ doc_id: string; job_id: string }>('indexador', 'api/documents', { method: 'POST', body: dados });
	},
	removerDocumento: (docId: string) => chamar<void>('indexador', `api/documents/${docId}`, { method: 'DELETE' }),

	conversas: () => chamar<Conversa[]>('agentes', 'api/conversas'),
	novaConversa: () => chamar<{ conversa_id: string }>('agentes', 'api/conversas', { method: 'POST' }),
	conversa: (id: string) => chamar<{ conversa_id: string; turnos: Turno[] }>('agentes', `api/conversas/${id}`),
	enviarMensagem: (id: string, texto: string) =>
		chamar<Execucao>('agentes', `api/conversas/${id}/mensagens`, json({ texto, aguardar: false })),
	execucao: (id: string) => chamar<Execucao>('agentes', `api/execucoes/${id}`)
};
