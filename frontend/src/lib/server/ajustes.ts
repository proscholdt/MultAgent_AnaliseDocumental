/**
 * Os ajustes do frontend, em config/ajustes.yaml (atalhos do chat, intervalo do andamento,
 * margem da sessão). Relidos quando o arquivo muda: não pedem build nem restart. Arquivo
 * inválido mantém a versão anterior, e o motivo vai para o log.
 */
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { parse } from 'yaml';

export interface Ajustes {
	sugestoes: string[];
	intervalo_ms: number;
	minutos_minimos: number;
}

export const PADRAO: Ajustes = {
	// Os atalhos são os do pacote do domínio (textos.opcoes); estes só valem sem domínio conhecido.
	sugestoes: ['Gere o relatório consolidado completo'],
	intervalo_ms: 1500,
	minutos_minimos: 16
};

const CAMINHO = resolve(process.env.AJUSTES_PATH ?? 'config/ajustes.yaml');
let atual: Ajustes = PADRAO;
let lidoEm: number | null = null;
let erro: string | null = null;

/** Confere o conteúdo do arquivo; devolve os ajustes ou lança o motivo. */
export function validar(dados: unknown): Ajustes {
	if (dados === null || dados === undefined) return PADRAO;
	if (typeof dados !== 'object' || Array.isArray(dados)) {
		throw new Error("o arquivo tem de ser uma lista de 'chave: valor'");
	}
	const entrada = dados as Record<string, unknown>;
	const desconhecidas = Object.keys(entrada).filter((chave) => !(chave in PADRAO));
	if (desconhecidas.length) throw new Error(`chave desconhecida: ${desconhecidas.join(', ')}`);
	const saida: Ajustes = { ...PADRAO };
	if (entrada.sugestoes !== undefined) {
		const lista = entrada.sugestoes;
		if (!Array.isArray(lista) || lista.length > 8 || !lista.every((s) => typeof s === 'string' && s.trim())) {
			throw new Error('sugestoes: uma lista de até 8 textos');
		}
		saida.sugestoes = lista.map((s: string) => s.trim());
	}
	const faixas = [
		['intervalo_ms', 500, 10_000],
		['minutos_minimos', 1, 59]
	] as const;
	for (const [chave, minimo, maximo] of faixas) {
		const valor = entrada[chave];
		if (valor === undefined) continue;
		if (typeof valor !== 'number' || !Number.isFinite(valor) || valor < minimo || valor > maximo) {
			throw new Error(`${chave}: um número de ${minimo} a ${maximo}`);
		}
		saida[chave] = valor;
	}
	return saida;
}

/** Os ajustes de agora: relê o arquivo se ele mudou desde a última leitura. */
export function ajustes(): Ajustes {
	let carimbo: number;
	try {
		carimbo = statSync(CAMINHO).mtimeMs;
	} catch {
		return atual; // sem arquivo: ficam os padrões
	}
	if (carimbo === lidoEm) return atual;
	lidoEm = carimbo;
	try {
		atual = validar(parse(readFileSync(CAMINHO, 'utf-8')));
		erro = null;
	} catch (e) {
		erro = `${CAMINHO}: ${e instanceof Error ? e.message : String(e)}`;
		console.warn('ajustes não aplicados:', erro);
	}
	return atual;
}

/** Por que o arquivo atual não foi aplicado, ou null. */
export function erroAjustes(): string | null {
	ajustes();
	return erro;
}
