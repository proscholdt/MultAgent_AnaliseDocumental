<script lang="ts">
	import type { Andamento as AndamentoT } from '$lib/progresso';
	import { EM_ANDAMENTO, type Turno } from '$lib/tipos';
	import Andamento from './Andamento.svelte';
	import Markdown from './Markdown.svelte';

	let {
		turno,
		andamento = null,
		segundos = 0
	}: { turno: Turno; andamento?: AndamentoT | null; segundos?: number } = $props();

	const rodando = $derived(EM_ANDAMENTO.includes(turno.estado));
	const TIPO: Record<Turno['tipo'], string> = { pergunta: 'Pergunta', relatorio: 'Relatório', livre: 'Pedido', auto: '' };

	let relatorioAberto = $state(true);
	let copiado = $state(false);

	async function copiar(texto: string) {
		try {
			await navigator.clipboard.writeText(texto);
			copiado = true;
			setTimeout(() => (copiado = false), 2000);
		} catch {
			copiado = false;
		}
	}

	function duracao(t: Turno): string | null {
		if (!t.terminada_em) return null;
		const s = Math.round((Date.parse(t.terminada_em) - Date.parse(t.criada_em)) / 1000);
		return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
	}
</script>

<article class="turno">
	<div class="pedido">{turno.pedido}</div>

	<div class="resposta" class:falha={turno.estado === 'falhou'} class:incompleta={turno.estado === 'incompleta'}>
		{#if rodando}
			{#if andamento}
				<Andamento {andamento} {segundos} />
			{:else}
				<Andamento andamento={{ frase: 'Carregando…', ativos: [], intencao: null }} {segundos} />
			{/if}
		{:else}
			{#if turno.estado === 'falhou' || turno.estado === 'interrompida'}
				<p class="alerta">
					<strong>{turno.estado === 'falhou' ? 'Não foi possível concluir.' : 'A execução foi interrompida.'}</strong>
					{turno.erro ?? ''}
				</p>
			{:else if turno.estado === 'incompleta'}
				<p class="alerta">
					<strong>Ficou incompleto.</strong>
					{turno.erro ?? 'O sistema terminou com pendências.'}
				</p>
			{/if}

			{#each turno.respostas as resposta, i (i)}
				<div class="bloco">
					<Markdown texto={resposta.resposta} />
					{#if resposta.fontes.length}
						<div class="fontes">
							<span class="rotulo">Fontes</span>
							{#each resposta.fontes as fonte (fonte)}
								<span class="fonte">{fonte}</span>
							{/each}
						</div>
					{/if}
				</div>
			{/each}

			{#if turno.relatorio}
				<div class="relatorio">
					<div class="barra">
						<span class="rotulo">Relatório consolidado</span>
						<div class="acoes">
							<button class="botao secundario" type="button" onclick={() => copiar(turno.relatorio ?? '')}>
								{copiado ? 'Copiado' : 'Copiar Markdown'}
							</button>
							<button class="botao secundario" type="button" onclick={() => (relatorioAberto = !relatorioAberto)}>
								{relatorioAberto ? 'Recolher' : 'Abrir'}
							</button>
						</div>
					</div>
					{#if relatorioAberto}
						<div class="conteudo"><Markdown texto={turno.relatorio} /></div>
					{/if}
				</div>
			{/if}

			{#if !turno.respostas.length && !turno.relatorio && turno.mensagem}
				<Markdown texto={turno.mensagem} />
			{/if}

			<div class="meta">
				{#if TIPO[turno.tipo]}<span>{TIPO[turno.tipo]}</span>{/if}
				{#if duracao(turno)}<span>{duracao(turno)}</span>{/if}
			</div>
		{/if}
	</div>
</article>

<style>
	.turno {
		display: grid;
		gap: 10px;
	}

	.pedido {
		justify-self: end;
		max-width: min(85%, 640px);
		background: var(--accent);
		color: var(--accent-ink);
		padding: 9px 14px;
		border-radius: 14px 14px 4px 14px;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}

	.resposta {
		justify-self: start;
		width: 100%;
		min-width: 0;
		background: var(--surface);
		border: 1px solid var(--line);
		border-radius: 14px 14px 14px 4px;
		padding: 14px 16px;
		display: grid;
		gap: 12px;
		box-shadow: var(--shadow);
	}

	.resposta.falha {
		border-color: var(--danger);
	}

	.resposta.incompleta {
		border-color: var(--warn);
	}

	.alerta {
		margin: 0;
		padding: 8px 12px;
		border-radius: 8px;
		background: var(--warn-soft);
		color: var(--warn);
		font-size: 14px;
	}

	.falha .alerta {
		background: var(--danger-soft);
		color: var(--danger);
	}

	.bloco {
		display: grid;
		gap: 8px;
	}

	.fontes {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
	}

	.fonte {
		font-size: 12px;
		padding: 2px 8px;
		border-radius: 999px;
		background: var(--accent-soft);
		color: var(--accent);
		font-weight: 600;
	}

	.relatorio {
		border: 1px solid var(--line);
		border-radius: 10px;
		overflow: hidden;
		min-width: 0;
	}

	.barra {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 8px 12px;
		background: var(--surface-2);
		border-bottom: 1px solid var(--line);
	}

	.acoes {
		display: flex;
		gap: 6px;
	}

	.acoes .botao {
		padding: 4px 10px;
		font-size: 13px;
	}

	.conteudo {
		padding: 12px 16px;
		max-height: 70vh;
		overflow: auto;
	}

	.meta {
		display: flex;
		gap: 12px;
		font-size: 12px;
		color: var(--muted);
	}

	.meta:empty {
		display: none;
	}
</style>
