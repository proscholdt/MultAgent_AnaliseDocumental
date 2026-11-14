<script lang="ts">
	import { fraseDoEspecialista, type Andamento } from '$lib/progresso';

	let { andamento, segundos }: { andamento: Andamento; segundos: number } = $props();

	const ROTULO = {
		pergunta: 'pergunta',
		relatorio: 'relatório consolidado',
		relatorio_empresa: 'relatório de uma empresa',
		fora_do_escopo: 'fora do escopo'
	} as const;

	function tempo(s: number): string {
		return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
	}
</script>

<div class="andamento" role="status" aria-live="polite">
	<div class="linha">
		<span class="pontos" aria-hidden="true"><i></i><i></i><i></i></span>
		<strong>{andamento.frase}</strong>
		<span class="tempo">{tempo(segundos)}</span>
	</div>
	{#if andamento.intencao}
		<p class="entendido">Entendido como {ROTULO[andamento.intencao]}.</p>
	{/if}
	{#if andamento.ativos.length > 1}
		<ul>
			{#each andamento.ativos as ativo, i (i)}
				<li>{fraseDoEspecialista(ativo.agente, ativo.doc)}</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.andamento {
		display: grid;
		gap: 4px;
		color: var(--muted);
	}

	.linha {
		display: flex;
		align-items: center;
		gap: 10px;
	}

	strong {
		color: var(--ink);
		font-weight: 600;
	}

	.tempo {
		margin-left: auto;
		font-size: 12px;
		font-variant-numeric: tabular-nums;
	}

	.entendido {
		margin: 0;
		font-size: 13px;
	}

	ul {
		margin: 4px 0 0;
		padding-left: 30px;
		font-size: 13px;
	}

	.pontos {
		display: inline-flex;
		gap: 4px;
	}

	.pontos i {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--accent);
		animation: pulso 1.2s infinite ease-in-out;
	}

	.pontos i:nth-child(2) {
		animation-delay: 0.15s;
	}

	.pontos i:nth-child(3) {
		animation-delay: 0.3s;
	}

	@keyframes pulso {
		0%,
		80%,
		100% {
			opacity: 0.25;
			transform: scale(0.8);
		}
		40% {
			opacity: 1;
			transform: scale(1);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.pontos i {
			animation: none;
			opacity: 0.7;
		}
	}
</style>
