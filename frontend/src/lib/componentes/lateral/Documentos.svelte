<script lang="ts">
	import { onDestroy } from 'svelte';
	import { api, ErroApi } from '$lib/api';
	import type { Documento, Job } from '$lib/tipos';

	let {
		aoMudar,
		colecaoId = null,
		envio = 'Envie os documentos em PDF.'
	}: { aoMudar: (documentos: Documento[]) => void; colecaoId?: string | null; envio?: string } = $props();

	let documentos = $state<Documento[]>([]);
	let jobs = $state<Record<string, Job>>({});
	let envios = $state<{ id: number; nome: string; erro: string | null }[]>([]);
	let confirmando = $state<string | null>(null);
	let arrastando = $state(false);
	let erro = $state<string | null>(null);
	let carregado = $state(false);

	let timer: ReturnType<typeof setTimeout> | undefined;
	let proximoEnvio = 0;
	let entrada: HTMLInputElement;

	const ESTADO: Record<Documento['status'], string> = {
		queued: 'Na fila',
		processing: 'Processando',
		indexed: 'Indexado',
		failed: 'Falhou'
	};

	const emProcesso = (d: Documento) => d.status === 'queued' || d.status === 'processing';

	async function atualizar() {
		clearTimeout(timer);
		try {
			documentos = await api.documentos(colecaoId);
			erro = null;
			const novos: Record<string, Job> = {};
			await Promise.all(
				documentos.filter(emProcesso).map(async (d) => {
					const lista = await api.jobs(d.doc_id).catch(() => []);
					if (lista[0]) novos[d.doc_id] = lista[0];
				})
			);
			jobs = novos;
			aoMudar(documentos);
		} catch (e) {
			erro = e instanceof ErroApi ? e.message : 'Não foi possível listar os documentos.';
		} finally {
			carregado = true;
			// Enquanto houver documento em processamento, a lista se atualiza sozinha.
			if (documentos.some(emProcesso)) timer = setTimeout(atualizar, 3000);
		}
	}

	async function enviar(arquivos: FileList | File[] | null) {
		if (!arquivos) return;
		for (const arquivo of Array.from(arquivos)) {
			const id = ++proximoEnvio;
			if (!arquivo.name.toLowerCase().endsWith('.pdf')) {
				envios = [...envios, { id, nome: arquivo.name, erro: 'Só PDF.' }];
				continue;
			}
			envios = [...envios, { id, nome: arquivo.name, erro: null }];
			try {
				await api.enviarDocumento(arquivo, colecaoId);
				envios = envios.filter((e) => e.id !== id);
			} catch (e) {
				const mensagem = e instanceof ErroApi ? e.message : 'Falha no envio.';
				envios = envios.map((item) => (item.id === id ? { ...item, erro: mensagem } : item));
			}
			await atualizar();
		}
		if (entrada) entrada.value = '';
	}

	async function remover(docId: string) {
		confirmando = null;
		try {
			await api.removerDocumento(docId);
		} catch (e) {
			erro = e instanceof ErroApi ? e.message : 'Não foi possível remover.';
		}
		await atualizar();
	}

	function progresso(d: Documento): string {
		const job = jobs[d.doc_id];
		if (d.status === 'queued' || !job) return 'Aguardando a vez…';
		if (job.status === 'parsing') {
			return job.progress_total ? `Lendo páginas: ${job.progress_current} de ${job.progress_total}` : 'Lendo o PDF…';
		}
		if (job.status === 'indexing') return 'Indexando os trechos…';
		return 'Processando…';
	}

	function porcentagem(d: Documento): number | null {
		const job = jobs[d.doc_id];
		if (!job || job.status !== 'parsing' || !job.progress_total) return null;
		return Math.round((100 * job.progress_current) / job.progress_total);
	}

	// Na troca de coleção, a lista é a da nova coleção.
	$effect(() => {
		void colecaoId;
		atualizar();
	});
	onDestroy(() => clearTimeout(timer));
</script>

<section class="documentos" aria-labelledby="titulo-documentos">
	<h2 id="titulo-documentos" class="rotulo">Documentos</h2>

	<label
		class="soltar"
		class:ativo={arrastando}
		ondragover={(e) => {
			e.preventDefault();
			arrastando = true;
		}}
		ondragleave={() => (arrastando = false)}
		ondrop={(e) => {
			e.preventDefault();
			arrastando = false;
			enviar(e.dataTransfer?.files ?? null);
		}}
	>
		<input
			bind:this={entrada}
			class="sr-only"
			type="file"
			accept="application/pdf,.pdf"
			multiple
			onchange={(e) => enviar(e.currentTarget.files)}
		/>
		<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
			<path d="M12 16V4m0 0-4 4m4-4 4 4M5 20h14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
		</svg>
		<span><strong>Enviar PDFs</strong> — clique ou arraste aqui</span>
	</label>

	{#each envios as envio (envio.id)}
		<div class="item" class:falhou={envio.erro}>
			<div class="nome">{envio.nome}</div>
			<div class="detalhe">{envio.erro ?? 'Enviando…'}</div>
			{#if envio.erro}
				<button class="mini" type="button" onclick={() => (envios = envios.filter((e) => e.id !== envio.id))}>Fechar</button>
			{/if}
		</div>
	{/each}

	{#if erro}
		<p class="erro">{erro}</p>
	{/if}

	{#if carregado && !documentos.length && !envios.length}
		<p class="vazio" data-testid="documentos-vazio">Nenhum documento ainda. {envio}</p>
	{/if}

	<ul>
		{#each documentos as doc (doc.doc_id)}
			<li class="item" class:falhou={doc.status === 'failed'}>
				<div class="topo">
					<div class="nome" title={doc.file_name}>{doc.company || doc.file_name}</div>
					<span class="selo {doc.status}">{ESTADO[doc.status]}</span>
					{#if !emProcesso(doc) && confirmando !== doc.doc_id}
						<button
							class="remover"
							type="button"
							title="Remover"
							aria-label={`Remover ${doc.file_name}`}
							onclick={() => (confirmando = doc.doc_id)}>×</button
						>
					{/if}
				</div>
				{#if doc.aviso_dominio}
					<p class="aviso" data-testid="aviso-dominio">{doc.aviso_dominio}</p>
				{/if}
				<div class="detalhe">
					{#if emProcesso(doc)}
						{progresso(doc)}
					{:else if doc.status === 'indexed'}
						{doc.file_name} · {doc.page_count ?? '?'} p. · {doc.chunk_count} trechos{doc.period ? ` · ${doc.period}` : ''}
					{:else}
						{doc.file_name}
					{/if}
				</div>
				{#if porcentagem(doc) !== null}
					<div class="barra" aria-hidden="true"><span style="width: {porcentagem(doc)}%"></span></div>
				{/if}
				{#if confirmando === doc.doc_id}
					<div class="confirma">
						<span>Remover este documento e o índice dele?</span>
						<button class="mini perigo" type="button" onclick={() => remover(doc.doc_id)}>Remover</button>
						<button class="mini" type="button" onclick={() => (confirmando = null)}>Cancelar</button>
					</div>
				{/if}
			</li>
		{/each}
	</ul>
</section>

<style>
	.documentos {
		display: grid;
		gap: 10px;
	}

	.soltar {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 14px;
		border: 1.5px dashed var(--line);
		border-radius: var(--radius);
		color: var(--muted);
		font-size: 14px;
		cursor: pointer;
		transition:
			border-color 0.15s,
			background 0.15s;
	}

	.soltar strong {
		color: var(--ink);
	}

	.soltar:hover,
	.soltar.ativo {
		border-color: var(--accent);
		background: var(--accent-soft);
	}

	.soltar:focus-within {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 8px;
	}

	.item {
		background: var(--surface);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 10px 12px;
		display: grid;
		gap: 4px;
		min-width: 0;
	}

	.item.falhou {
		border-color: var(--danger);
	}

	.topo {
		display: flex;
		align-items: center;
		gap: 8px;
		min-width: 0;
	}

	.nome {
		font-weight: 600;
		font-size: 14px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		min-width: 0;
		flex: 1;
	}

	.detalhe {
		font-size: 12px;
		color: var(--muted);
		overflow-wrap: anywhere;
	}

	.selo {
		flex: none;
		font-size: 11px;
		font-weight: 700;
		padding: 2px 8px;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--muted);
	}

	.selo.indexed {
		background: var(--ok-soft);
		color: var(--ok);
	}

	.selo.processing,
	.selo.queued {
		background: var(--warn-soft);
		color: var(--warn);
	}

	.selo.failed {
		background: var(--danger-soft);
		color: var(--danger);
	}

	.barra {
		height: 4px;
		border-radius: 2px;
		background: var(--surface-2);
		overflow: hidden;
	}

	.barra span {
		display: block;
		height: 100%;
		background: var(--accent);
		transition: width 0.4s;
	}

	.mini {
		justify-self: start;
		border: 1px solid var(--line);
		background: transparent;
		border-radius: 6px;
		padding: 2px 8px;
		font-size: 12px;
		color: var(--muted);
	}

	.mini:hover {
		color: var(--ink);
		background: var(--surface-2);
	}

	.mini.perigo {
		background: var(--danger);
		border-color: var(--danger);
		color: #fff;
	}

	li.item {
		position: relative;
	}

	li.item .detalhe {
		padding-right: 22px;
	}

	.remover {
		position: absolute;
		right: 6px;
		bottom: 6px;
		width: 22px;
		height: 22px;
		display: grid;
		place-items: center;
		border: 0;
		border-radius: 6px;
		background: transparent;
		color: var(--muted);
		font-size: 16px;
		line-height: 1;
		opacity: 0;
		transition: opacity 0.15s;
	}

	.remover:hover {
		background: var(--danger-soft);
		color: var(--danger);
	}

	.item:hover .remover,
	.remover:focus-visible {
		opacity: 1;
	}

	@media (hover: none) {
		.remover {
			opacity: 1;
		}
	}

	.confirma {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		font-size: 12px;
		color: var(--danger);
	}

	.erro,
	.vazio {
		margin: 0;
		font-size: 13px;
		color: var(--muted);
	}

	.erro {
		color: var(--danger);
	}

	.aviso {
		margin: 4px 0 0;
		font-size: 12px;
		color: var(--warn);
	}
</style>
