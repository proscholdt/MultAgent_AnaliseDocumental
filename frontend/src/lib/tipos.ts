/** O que as APIs do indexador e do orquestrador devolvem, só nos campos que a tela usa. */

export type EstadoDocumento = 'queued' | 'processing' | 'indexed' | 'failed';

export interface Documento {
	doc_id: string;
	file_name: string;
	page_count: number | null;
	company: string | null;
	period: string | null;
	status: EstadoDocumento;
	chunk_count: number;
}

export interface Job {
	job_id: string;
	status: 'queued' | 'parsing' | 'indexing' | 'done' | 'failed';
	stage: string | null;
	progress_current: number;
	progress_total: number;
	error: string | null;
}

export interface Conversa {
	conversa_id: string;
	criada_em: string;
	turnos: number;
	/** O primeiro pedido da conversa. */
	titulo: string | null;
}

export type EstadoExecucao = 'na_fila' | 'em_andamento' | 'completa' | 'incompleta' | 'falhou' | 'interrompida';

export const EM_ANDAMENTO: EstadoExecucao[] = ['na_fila', 'em_andamento'];

export interface Resposta {
	resposta: string;
	fontes: string[];
}

export interface Opcao {
	titulo: string;
	exemplo: string;
}

/** Um turno da conversa: o pedido e o que voltou. */
export interface Turno {
	execucao_id: string;
	pedido: string;
	tipo: 'pergunta' | 'relatorio' | 'relatorio_empresa' | 'fora_do_escopo' | 'livre' | 'auto';
	estado: EstadoExecucao;
	mensagem: string;
	erro: string | null;
	respostas: Resposta[];
	relatorio: string | null;
	/** Fora do escopo: o que o sistema faz, com um exemplo clicável de cada. */
	opcoes: Opcao[] | null;
	criada_em: string;
	terminada_em: string | null;
}

/** Um passo do trace de uma execução (`GET /api/execucoes/{id}`). */
export interface Passo {
	em: string;
	tipo: string;
	agente?: string;
	doc_id?: string | null;
	tarefa?: string;
	intencao?: string;
	[campo: string]: unknown;
}

export interface Execucao {
	execucao_id: string;
	tipo: Turno['tipo'];
	estado: EstadoExecucao;
	mensagem: string;
	erro: string | null;
	pedido: string;
	conversa_id: string;
	criada_em: string;
	iniciada_em: string | null;
	terminada_em: string | null;
	resultado: { respostas?: Resposta[]; relatorio?: string | null; opcoes?: Opcao[] } | null;
	passos?: Passo[];
}
