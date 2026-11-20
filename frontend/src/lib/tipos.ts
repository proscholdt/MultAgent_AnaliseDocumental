/** O que as APIs do indexador e do orquestrador devolvem, só nos campos que a tela usa. */

export type EstadoDocumento = 'queued' | 'processing' | 'indexed' | 'failed';

export interface Documento {
	doc_id: string;
	file_name: string;
	page_count: number | null;
	/** A entidade e o recorte do domínio (a empresa e o período; o paciente e a data da coleta). */
	company: string | null;
	period: string | null;
	status: EstadoDocumento;
	chunk_count: number;
	collection_id?: string | null;
	dominio?: string;
	/** Quando o conteúdo parece de outro domínio que não o da coleção. */
	aviso_dominio?: string | null;
}

/** Um pacote de domínio (dominios/<id>/): o que a tela mostra e como rotula. */
export interface Dominio {
	id: string;
	nome: string;
	descricao: string;
	padrao: boolean;
	entidade: { nome: string; rotulo: string; plural: string };
	recorte: { nome: string; rotulo: string; tipo: string };
	textos: { titulo: string; envio: string; fora_do_escopo: string; opcoes: { titulo: string; exemplo: string }[] };
}

/** Uma coleção de documentos de um domínio. */
export interface Colecao {
	id: string;
	nome: string;
	dominio: string;
	documentos: number;
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
	// relatorio_empresa: o nome de antes, em conversas antigas.
	tipo: 'pergunta' | 'relatorio' | 'relatorio_documento' | 'relatorio_empresa' | 'fora_do_escopo' | 'livre' | 'auto';
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
