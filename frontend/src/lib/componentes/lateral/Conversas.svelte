<script lang="ts">
	import type { Conversa } from '$lib/tipos';

	let {
		conversas,
		atual,
		aoAbrir,
		aoNova
	}: {
		conversas: Conversa[];
		atual: string | null;
		aoAbrir: (id: string) => void;
		aoNova: () => void;
	} = $props();

	function quando(iso: string): string {
		const data = new Date(iso);
		const hoje = new Date();
		const mesmoDia = data.toDateString() === hoje.toDateString();
		return mesmoDia
			? data.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
			: data.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
	}
</script>

<section class="conversas" aria-labelledby="titulo-conversas">
	<div class="cabeca">
		<h2 id="titulo-conversas" class="rotulo">Conversas</h2>
		<button class="nova" type="button" onclick={aoNova}>+ Nova</button>
	</div>
	{#if !conversas.length}
		<p class="vazio">Nenhuma conversa ainda.</p>
	{/if}
	<ul>
		{#each conversas as conversa (conversa.conversa_id)}
			<li>
				<button
					type="button"
					class:atual={conversa.conversa_id === atual}
					aria-current={conversa.conversa_id === atual ? 'true' : undefined}
					onclick={() => aoAbrir(conversa.conversa_id)}
				>
					<span class="titulo">{conversa.titulo ?? 'Conversa sem mensagens'}</span>
					<span class="info">{quando(conversa.criada_em)} · {conversa.turnos} {conversa.turnos === 1 ? 'turno' : 'turnos'}</span>
				</button>
			</li>
		{/each}
	</ul>
</section>

<style>
	.conversas {
		display: grid;
		gap: 8px;
	}

	.cabeca {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.nova {
		border: 1px solid var(--line);
		background: var(--surface);
		border-radius: 6px;
		padding: 3px 10px;
		font-size: 13px;
		font-weight: 600;
		color: var(--accent);
	}

	.nova:hover {
		background: var(--accent-soft);
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 2px;
	}

	li button {
		width: 100%;
		text-align: left;
		border: 0;
		background: transparent;
		border-radius: 8px;
		padding: 8px 10px;
		display: grid;
		gap: 2px;
		min-width: 0;
	}

	li button:hover {
		background: var(--surface-2);
	}

	li button.atual {
		background: var(--accent-soft);
	}

	.titulo {
		font-size: 14px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.atual .titulo {
		color: var(--accent);
		font-weight: 600;
	}

	.info {
		font-size: 12px;
		color: var(--muted);
	}

	.vazio {
		margin: 0;
		font-size: 13px;
		color: var(--muted);
	}
</style>
