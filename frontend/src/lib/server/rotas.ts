/**
 * O que o navegador pode pedir, por meio do servidor do SvelteKit, a cada sistema.
 *
 * A lista é fechada: o proxy não repassa qualquer caminho. Do indexador, só os documentos e os
 * jobs (enviar, listar, acompanhar, remover); do orquestrador, só as conversas e as execuções.
 * Busca direta, cadastro de usuário e as rotas administrativas ficam de fora.
 */
export type Sistema = 'indexador' | 'agentes';

type Regra = { caminho: RegExp; metodos: string[] };

const ID = '[A-Za-z0-9_-]+';

export const REGRAS: Record<Sistema, Regra[]> = {
	indexador: [
		{ caminho: /^api\/documents$/, metodos: ['GET', 'POST'] },
		{ caminho: new RegExp(`^api/documents/${ID}$`), metodos: ['GET', 'DELETE'] },
		{ caminho: new RegExp(`^api/documents/${ID}/jobs$`), metodos: ['GET'] },
		{ caminho: new RegExp(`^api/jobs/${ID}$`), metodos: ['GET'] }
	],
	agentes: [
		{ caminho: /^api\/conversas$/, metodos: ['GET', 'POST'] },
		{ caminho: new RegExp(`^api/conversas/${ID}$`), metodos: ['GET'] },
		{ caminho: new RegExp(`^api/conversas/${ID}/mensagens$`), metodos: ['POST'] },
		{ caminho: new RegExp(`^api/execucoes/${ID}$`), metodos: ['GET'] }
	]
};

export function ehSistema(nome: string): nome is Sistema {
	return nome === 'indexador' || nome === 'agentes';
}

/** `null` = liberado; senão, o status HTTP da recusa (404 caminho fora da lista, 405 método). */
export function recusa(sistema: Sistema, caminho: string, metodo: string): 404 | 405 | null {
	const regra = REGRAS[sistema].find((r) => r.caminho.test(caminho));
	if (!regra) return 404;
	return regra.metodos.includes(metodo.toUpperCase()) ? null : 405;
}
