import { describe, expect, it } from 'vitest';
import { PADRAO, ajustes, erroAjustes, validar } from '../src/lib/server/ajustes';

describe('config/ajustes.yaml', () => {
	it('o arquivo do repositório é válido', () => {
		expect(ajustes().sugestoes.length).toBeGreaterThan(0);
		expect(erroAjustes()).toBeNull();
	});
	it('chave desconhecida ou valor fora da faixa é erro', () => {
		expect(() => validar({ intervalo: 1000 })).toThrow('chave desconhecida');
		expect(() => validar({ intervalo_ms: 50 })).toThrow('intervalo_ms');
		expect(() => validar({ sugestoes: 'uma só' })).toThrow('sugestoes');
	});
	it('o que o arquivo não diz fica com o padrão', () => {
		expect(validar({ intervalo_ms: 2000 })).toEqual({ ...PADRAO, intervalo_ms: 2000 });
		expect(validar(null)).toEqual(PADRAO);
	});
});
