<script lang="ts">
	import { onMount } from 'svelte';
	import { replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/api';
	import Chat from '$lib/components/Chat.svelte';
	import Conversas from '$lib/components/Conversas.svelte';
	import Documentos from '$lib/components/Documentos.svelte';
	import type { Conversa, Documento } from '$lib/tipos';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let documentos = $state<Documento[]>([]);
	let conversas = $state<Conversa[]>([]);
	// A conversa aberta fica no endereço (?c=...): recarregar a página volta a ela.
	let conversaId = $state<string | null>(page.url.searchParams.get('c'));
	let lateralAberta = $state(false);

	const nomes = $derived(Object.fromEntries(documentos.map((d) => [d.doc_id, d.company || d.file_name])));
	const indexados = $derived(documentos.filter((d) => d.status === 'indexed').length);
	const processando = $derived(documentos.filter((d) => d.status === 'queued' || d.status === 'processing').length);

	async function carregarConversas() {
		try {
			conversas = await api.conversas();
		} catch {
			// a lista é secundária: o chat continua funcionando
		}
	}

	function abrirConversa(id: string | null) {
		conversaId = id;
		lateralAberta = false;
		const url = new URL(page.url);
		if (id) url.searchParams.set('c', id);
		else url.searchParams.delete('c');
		replaceState(url, {});
	}

	onMount(carregarConversas);
</script>

<svelte:head>
	<title>Análise de Demonstrações</title>
</svelte:head>

<div class="app">
	<aside class:aberta={lateralAberta} aria-label="Documentos e conversas">
		<header class="marca">
			<img src="/favicon.svg" alt="" width="28" height="28" />
			<div>
				<strong>Demonstrações</strong>
				<span>Indexação e agentes</span>
			</div>
		</header>

		<div class="rolagem">
			<Documentos aoMudar={(lista) => (documentos = lista)} />
			<Conversas {conversas} atual={conversaId} aoAbrir={abrirConversa} aoNova={() => abrirConversa(null)} />
		</div>

		<footer class="usuario">
			<span title="Usuário">{data.usuario ?? ''}</span>
			<form method="POST" action="/sair">
				<button class="botao secundario" type="submit">Sair</button>
			</form>
		</footer>
	</aside>

	{#if lateralAberta}
		<button class="veu" type="button" aria-label="Fechar a lateral" onclick={() => (lateralAberta = false)}></button>
	{/if}

	<main>
		<header class="topo-movel">
			<button class="botao secundario" type="button" onclick={() => (lateralAberta = true)}>☰ Documentos e conversas</button>
		</header>
		<Chat
			{conversaId}
			sugestoes={data.sugestoes}
			intervaloMs={data.intervaloMs}
			{nomes}
			{indexados}
			{processando}
			aoCriarConversa={(id) => {
				abrirConversa(id);
				carregarConversas();
			}}
			aoMudarConversas={carregarConversas}
		/>
	</main>
</div>

<style>
	.app {
		display: grid;
		grid-template-columns: 320px 1fr;
		height: 100vh;
		height: 100dvh;
	}

	aside {
		display: grid;
		grid-template-rows: auto 1fr auto;
		background: var(--surface-2);
		border-right: 1px solid var(--line);
		min-height: 0;
	}

	.marca {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 16px 18px;
		border-bottom: 1px solid var(--line);
	}

	.marca div {
		display: grid;
		line-height: 1.2;
	}

	.marca span {
		font-size: 12px;
		color: var(--muted);
	}

	.rolagem {
		overflow-y: auto;
		padding: 16px 18px;
		display: grid;
		align-content: start;
		gap: 24px;
		min-height: 0;
	}

	.usuario {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 12px 18px max(12px, env(safe-area-inset-bottom, 0px));
		border-top: 1px solid var(--line);
		font-size: 14px;
	}

	.usuario span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--muted);
	}

	.usuario .botao {
		padding: 4px 12px;
		font-size: 13px;
	}

	main {
		display: grid;
		grid-template-rows: 1fr; /* no celular, ganha a linha do cabeçalho (abaixo) */
		min-width: 0;
		min-height: 0;
	}

	.topo-movel {
		display: none;
	}

	.veu {
		display: none;
	}

	@media (max-width: 900px) {
		.app {
			grid-template-columns: 1fr;
		}

		aside {
			position: fixed;
			inset: 0 auto 0 0;
			width: min(340px, 88vw);
			z-index: 20;
			transform: translateX(-100%);
			transition: transform 0.2s;
			padding-top: env(safe-area-inset-top, 0px);
		}

		aside.aberta {
			transform: none;
			box-shadow: var(--shadow);
		}

		.veu {
			display: block;
			position: fixed;
			inset: 0;
			z-index: 10;
			border: 0;
			background: rgb(0 0 0 / 35%);
		}

		main {
			grid-template-rows: auto 1fr;
		}

		.topo-movel {
			display: block;
			padding: max(10px, env(safe-area-inset-top, 0px)) max(16px, env(safe-area-inset-right, 0px)) 10px
				max(16px, env(safe-area-inset-left, 0px));
			border-bottom: 1px solid var(--line);
		}

		.topo-movel .botao {
			padding: 6px 12px;
			font-size: 14px;
		}
	}
</style>
