import { describe, expect, it } from 'vitest';
import { ehSistema, recusa } from '../src/lib/server/rotas';

describe('lista de rotas do proxy', () => {
	it('libera o que a tela usa', () => {
		expect(recusa('indexador', 'api/documents', 'GET')).toBeNull();
		expect(recusa('indexador', 'api/documents', 'POST')).toBeNull();
		expect(recusa('indexador', 'api/documents/5905cd3412e40fea/jobs', 'GET')).toBeNull();
		expect(recusa('indexador', 'api/documents/5905cd3412e40fea', 'DELETE')).toBeNull();
		expect(recusa('agentes', 'api/conversas', 'POST')).toBeNull();
		expect(recusa('agentes', 'api/conversas/conv-123/mensagens', 'POST')).toBeNull();
		expect(recusa('agentes', 'api/execucoes/exec-5d9a50db19e7', 'GET')).toBeNull();
	});

	it('libera os domínios e as coleções, e só para ler e criar', () => {
		expect(recusa('indexador', 'api/dominios', 'GET')).toBeNull();
		expect(recusa('indexador', 'api/colecoes', 'GET')).toBeNull();
		expect(recusa('indexador', 'api/colecoes', 'POST')).toBeNull();
		expect(recusa('indexador', 'api/dominios', 'POST')).toBe(405);
		expect(recusa('indexador', 'api/colecoes', 'DELETE')).toBe(405);
	});

	it('recusa o que está fora da lista', () => {
		expect(recusa('indexador', 'api/search', 'POST')).toBe(404);
		expect(recusa('indexador', 'auth/register', 'POST')).toBe(404);
		expect(recusa('indexador', 'api/documents/x/chunks', 'GET')).toBe(404);
		expect(recusa('agentes', 'api/agentes', 'GET')).toBe(404);
		expect(recusa('agentes', 'api/relatorios', 'POST')).toBe(404);
	});

	it('não deixa escapar do caminho', () => {
		expect(recusa('indexador', 'api/documents/../../auth/me', 'GET')).toBe(404);
		expect(recusa('agentes', 'api/conversas/a/b/mensagens', 'POST')).toBe(404);
		expect(recusa('agentes', 'api/execucoes/exec-1?x=1', 'GET')).toBe(404);
	});

	it('confere o método', () => {
		expect(recusa('indexador', 'api/documents', 'DELETE')).toBe(405);
		expect(recusa('agentes', 'api/conversas/conv-1', 'DELETE')).toBe(405);
	});

	it('só conhece os dois sistemas', () => {
		expect(ehSistema('indexador')).toBe(true);
		expect(ehSistema('agentes')).toBe(true);
		expect(ehSistema('mcp')).toBe(false);
	});
});
