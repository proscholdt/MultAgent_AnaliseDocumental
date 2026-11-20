<script lang="ts">
	import type { Colecao, Dominio } from '$lib/tipos';

	let {
		colecoes,
		dominios,
		atual,
		aoEscolher,
		aoCriar
	}: {
		colecoes: Colecao[];
		dominios: Dominio[];
		atual: string | null;
		aoEscolher: (id: string) => void;
		aoCriar: (nome: string, dominio: string) => Promise<void>;
	} = $props();

	let criando = $state(false);
	let nome = $state('');
	let dominio = $state('');
	let erro = $state<string | null>(null);

	const nomeDoDominio = (id: string) => dominios.find((d) => d.id === id)?.nome ?? id;

	$effect(() => {
		if (!dominio && dominios.length) dominio = (dominios.find((d) => d.padrao) ?? dominios[0]).id;
	});

	async function criar(evento: SubmitEvent) {
		evento.preventDefault();
		if (!nome.trim() || !dominio) return;
		try {
			await aoCriar(nome.trim(), dominio);
			nome = '';
			criando = false;
			erro = null;
		} catch (e) {
			erro = e instanceof Error ? e.message : 'Não foi possível criar a coleção.';
		}
	}
</script>

<section aria-labelledby="titulo-colecoes">
	<div class="cabecalho">
		<h2 id="titulo-colecoes">Coleção</h2>
		<button class="link" type="button" data-testid="btn-nova-colecao" onclick={() => (criando = !criando)}>
			{criando ? 'Cancelar' : '+ Nova'}
		</button>
	</div>

	{#if colecoes.length}
		<label class="sr-only" for="colecao-atual">Coleção ativa</label>
		<select id="colecao-atual" data-testid="campo-colecao" value={atual ?? ''}
			onchange={(e) => aoEscolher((e.currentTarget as HTMLSelectElement).value)}>
			{#each colecoes as colecao (colecao.id)}
				<option value={colecao.id}>{colecao.nome} · {nomeDoDominio(colecao.dominio)} ({colecao.documentos})</option>
			{/each}
		</select>
	{:else}
		<p class="vazio">Nenhuma coleção ainda: o primeiro envio cria uma.</p>
	{/if}

	{#if criando}
		<form class="nova" onsubmit={criar}>
			<label for="colecao-nome">Nome</label>
			<input id="colecao-nome" data-testid="campo-colecao-nome" bind:value={nome} maxlength="120"
				placeholder="Ex.: Exames de 2025" />
			<label for="colecao-dominio">Domínio</label>
			<select id="colecao-dominio" data-testid="campo-colecao-dominio" bind:value={dominio}>
				{#each dominios as opcao (opcao.id)}
					<option value={opcao.id}>{opcao.nome}</option>
				{/each}
			</select>
			<button class="botao" type="submit" data-testid="btn-criar-colecao" disabled={!nome.trim()}>Criar</button>
			{#if erro}<p class="erro" data-testid="msg-erro-colecao">{erro}</p>{/if}
		</form>
	{/if}
</section>

<style>
	section {
		display: grid;
		gap: 8px;
		padding-bottom: 12px;
	}

	.cabecalho {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	h2 {
		margin: 0;
		font-size: 13px;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--muted);
	}

	.link {
		border: 0;
		background: none;
		color: var(--accent);
		cursor: pointer;
		font-size: 13px;
	}

	select,
	input {
		width: 100%;
		font: inherit;
		font-size: 13px;
		padding: 6px 8px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--surface);
		color: inherit;
	}

	.nova {
		display: grid;
		gap: 6px;
	}

	.nova label {
		font-size: 12px;
		color: var(--muted);
	}

	.vazio {
		margin: 0;
		font-size: 13px;
		color: var(--muted);
	}

	.erro {
		margin: 0;
		font-size: 12px;
		color: var(--danger);
	}

	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
	}
</style>
