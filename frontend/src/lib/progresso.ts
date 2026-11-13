/**
 * O aviso de andamento ("Pensando…", "Lendo os números da Light…"), lido dos passos do trace
 * da execução. Não é uma animação de enfeite: diz o que o sistema está fazendo agora.
 *
 * - Um especialista entra no trace como `delegando` quando começa e como `especialista` quando
 *   termina. Os que começaram e não terminaram são os que estão trabalhando agora.
 * - Sem especialista trabalhando, o último passo diz a fase: entendendo o pedido, o orquestrador
 *   decidindo, a conferência final.
 * - Se nada muda por alguns segundos e ninguém está trabalhando, o aviso alterna, para a tela
 *   não parecer travada.
 */
import type { Passo } from './tipos';

export interface Ativo {
	agente: string;
	doc: string | null;
}

export interface Andamento {
	frase: string;
	ativos: Ativo[];
	/** O que o orquestrador entendeu do pedido, quando já decidiu. */
	intencao: 'pergunta' | 'relatorio' | 'outro' | null;
}

const SEGUNDOS_PARADO = 8;
const ALTERNANCIA = ['Pensando…', 'Buscando informações…', 'Carregando…'];

export function fraseDoEspecialista(agente: string, doc: string | null): string {
	switch (agente) {
		case 'pesquisador':
			return 'Buscando informações nos documentos…';
		case 'extrator':
			return doc ? `Lendo os números de ${doc}…` : 'Lendo os números…';
		case 'validador':
			return doc ? `Conferindo as fontes de ${doc}…` : 'Conferindo as fontes…';
		case 'analista':
			return doc ? `Analisando o texto de ${doc}…` : 'Analisando o texto…';
		case 'redator':
			return doc ? `Escrevendo o resumo de ${doc}…` : 'Escrevendo…';
		default:
			return doc ? `${agente} trabalhando em ${doc}…` : `${agente} trabalhando…`;
	}
}

/** Os especialistas que começaram e ainda não terminaram. */
export function ativos(passos: Passo[], nomes: Record<string, string> = {}): Ativo[] {
	const abertos: { agente: string; doc_id: string | null }[] = [];
	for (const passo of passos) {
		if (!passo.agente) continue;
		const doc_id = passo.doc_id ?? null;
		if (passo.tipo === 'delegando') abertos.push({ agente: passo.agente, doc_id });
		if (passo.tipo === 'especialista') {
			const i = abertos.findIndex((a) => a.agente === passo.agente && a.doc_id === doc_id);
			if (i >= 0) abertos.splice(i, 1);
		}
	}
	return abertos.map((a) => ({ agente: a.agente, doc: a.doc_id ? (nomes[a.doc_id] ?? 'um documento') : null }));
}

function fase(ultimo: Passo | undefined): string {
	switch (ultimo?.tipo) {
		case undefined:
			return 'Carregando…';
		case 'inicio':
		case 'especialistas':
		case 'conversa':
			return 'Entendendo o pedido…';
		case 'conferencia':
			return 'Conferindo se está tudo pronto…';
		case 'fim':
			return 'Finalizando…';
		default:
			return 'Pensando…';
	}
}

/**
 * @param passos os passos da execução, na ordem
 * @param nomes doc_id → nome para mostrar (a empresa ou o arquivo)
 * @param segundosSemMudanca há quanto tempo a lista de passos não cresce
 */
export function andamento(passos: Passo[], nomes: Record<string, string> = {}, segundosSemMudanca = 0): Andamento {
	const trabalhando = ativos(passos, nomes);
	const decisao = passos.find((p) => p.tipo === 'intencao');
	const intencao = (decisao?.intencao as Andamento['intencao']) ?? null;

	let frase: string;
	if (trabalhando.length === 1) {
		frase = fraseDoEspecialista(trabalhando[0].agente, trabalhando[0].doc);
	} else if (trabalhando.length > 1) {
		frase = `${trabalhando.length} especialistas trabalhando ao mesmo tempo…`;
	} else if (segundosSemMudanca >= SEGUNDOS_PARADO) {
		frase = ALTERNANCIA[Math.floor(segundosSemMudanca / 4) % ALTERNANCIA.length];
	} else {
		frase = fase(passos.at(-1));
	}
	return { frase, ativos: trabalhando, intencao };
}
