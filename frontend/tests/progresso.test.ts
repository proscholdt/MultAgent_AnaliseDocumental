import { describe, expect, it } from 'vitest';
import { andamento, ativos } from '../src/lib/progresso';
import type { Passo } from '../src/lib/tipos';

const NOMES = { 'doc-light': 'LIGHT S.A.', 'doc-millet': 'Millet' };

function passo(tipo: string, campos: Partial<Passo> = {}): Passo {
	return { em: '2026-11-13T20:00:00+00:00', tipo, ...campos };
}

describe('andamento', () => {
	it('sem passos ainda, está carregando', () => {
		expect(andamento([]).frase).toBe('Carregando…');
	});

	it('no começo, está entendendo o pedido', () => {
		expect(andamento([passo('inicio'), passo('especialistas')]).frase).toBe('Entendendo o pedido…');
	});

	it('com o orquestrador decidindo, está pensando', () => {
		const passos = [passo('inicio'), passo('tool', { agente: 'orquestrador' }), passo('raciocinio', { agente: 'orquestrador' })];
		expect(andamento(passos).frase).toBe('Pensando…');
	});

	it('diz quem está trabalhando agora, com o nome do documento', () => {
		const passos = [passo('inicio'), passo('delegando', { agente: 'extrator', doc_id: 'doc-light' })];
		expect(andamento(passos, NOMES).frase).toBe('Lendo os números de LIGHT S.A.…');
		const pesquisa = [passo('inicio'), passo('delegando', { agente: 'pesquisador', doc_id: null })];
		expect(andamento(pesquisa, NOMES).frase).toBe('Buscando informações nos documentos…');
	});

	it('quem terminou sai da lista, e quem está em paralelo aparece junto', () => {
		const passos = [
			passo('delegando', { agente: 'extrator', doc_id: 'doc-light' }),
			passo('delegando', { agente: 'extrator', doc_id: 'doc-millet' }),
			passo('delegando', { agente: 'analista', doc_id: 'doc-light' }),
			passo('especialista', { agente: 'extrator', doc_id: 'doc-light' })
		];
		expect(ativos(passos, NOMES)).toEqual([
			{ agente: 'extrator', doc: 'Millet' },
			{ agente: 'analista', doc: 'LIGHT S.A.' }
		]);
		expect(andamento(passos, NOMES).frase).toBe('2 especialistas trabalhando ao mesmo tempo…');
	});

	it('todos terminaram: volta ao orquestrador, e depois à conferência', () => {
		const passos = [
			passo('delegando', { agente: 'pesquisador' }),
			passo('especialista', { agente: 'pesquisador' }),
			passo('orquestrador')
		];
		expect(andamento(passos).frase).toBe('Pensando…');
		expect(andamento([...passos, passo('conferencia')]).frase).toBe('Conferindo se está tudo pronto…');
	});

	it('mostra o que o orquestrador entendeu do pedido', () => {
		const passos = [passo('inicio'), passo('orquestrador'), passo('intencao', { intencao: 'relatorio' })];
		expect(andamento(passos).intencao).toBe('relatorio');
		expect(andamento([passo('inicio')]).intencao).toBeNull();
	});

	it('parado por muito tempo sem especialista trabalhando, o aviso alterna', () => {
		const passos = [passo('inicio'), passo('orquestrador')];
		const frases = new Set([8, 12, 16].map((s) => andamento(passos, {}, s).frase));
		expect(frases).toEqual(new Set(['Pensando…', 'Buscando informações…', 'Carregando…']));
		// Com especialista trabalhando, o aviso continua dizendo quem é.
		const ocupado = [passo('delegando', { agente: 'validador', doc_id: 'doc-millet' })];
		expect(andamento(ocupado, NOMES, 30).frase).toBe('Conferindo as fontes de Millet…');
	});
});
