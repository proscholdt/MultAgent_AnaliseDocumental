import { describe, expect, it } from 'vitest';
import { minutosRestantes, tokenValido, vencimento } from '../src/lib/server/sessao';

function jwt(payload: object): string {
	const parte = (obj: object) => btoa(JSON.stringify(obj)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
	return `${parte({ alg: 'HS256', typ: 'JWT' })}.${parte(payload)}.assinatura`;
}

const AGORA = Date.parse('2026-11-13T20:00:00Z');
const segundos = (minutos: number) => AGORA / 1000 + minutos * 60;

describe('sessão', () => {
	it('lê o vencimento do token', () => {
		expect(vencimento(jwt({ sub: 'u1', exp: 1_800_000_000 }))).toBe(1_800_000_000);
		expect(vencimento('nao-e-jwt')).toBeNull();
		expect(vencimento(jwt({ sub: 'u1' }))).toBeNull();
	});

	it('conta os minutos que faltam', () => {
		expect(minutosRestantes(jwt({ exp: segundos(30) }), AGORA)).toBeCloseTo(30);
	});

	it('encerra a sessão antes de o orquestrador recusar (menos de 16 min)', () => {
		expect(tokenValido(jwt({ exp: segundos(59) }), AGORA)).toBe(true);
		expect(tokenValido(jwt({ exp: segundos(16) }), AGORA)).toBe(true);
		expect(tokenValido(jwt({ exp: segundos(15) }), AGORA)).toBe(false);
		expect(tokenValido(jwt({ exp: segundos(-1) }), AGORA)).toBe(false);
		expect(tokenValido('lixo', AGORA)).toBe(false);
	});
});
