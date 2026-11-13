<script lang="ts">
	import { onDestroy, tick, untrack } from 'svelte';
	import { api, ErroApi } from '$lib/api';
	import { andamento as lerAndamento, type Andamento } from '$lib/progresso';
	import { EM_ANDAMENTO, type Execucao, type Turno as TurnoT } from '$lib/tipos';
	import Turno from './Turno.svelte';

	let {
		conversaId,
		nomes,
		indexados,
		processando,
		aoCriarConversa,
		aoMudarConversas
	}: {
		conversaId: string | null;
		nomes: Record<string, string>;
		indexados: number;
		processando: number;
		aoCriarConversa: (id: string) => void;
		aoMudarConversas: () => void;
	} = $props();

	const SUGESTOES = [
		'Gere o relatório consolidado completo',
		'Qual empresa teve a maior margem EBITDA?',
		'Compare o lucro líquido das empresas',
		'Quais riscos as empresas destacam?'
	];
	const INTERVALO_MS = 1500;

	let turnos = $state<TurnoT[]>([]);
	let texto = $state('');
	let erro = $state<string | null>(null);
	let enviando = $state(false);
	let carregando = $state(false);
	/** A execução em andamento nesta conversa: enquanto existir, o chat fica bloqueado. */
	let emCurso = $state<{ execucaoId: string; inicio: number } | null>(null);
	let andamentoAtual = $state<Andamento | null>(null);
	let segundos = $state(0);

	const bloqueado = $derived(enviando || emCurso !== null);

	let lista: HTMLElement | undefined = $state();
	let caixa: HTMLTextAreaElement | undefined = $state();
	let geracao = 0; // muda a cada conversa aberta ou acompanhamento novo: cancela o anterior
	let criadaAqui: string | null = null;
	let relogio: ReturnType<typeof setInterval> | undefined;

	const esperar = (ms: number) => new Promise((r) => setTimeout(r, ms));

	function turnoDe(execucao: Execucao): TurnoT {
		const resultado = execucao.resultado ?? {};
		return {
			execucao_id: execucao.execucao_id,
			pedido: execucao.pedido,
			tipo: execucao.tipo,
			estado: execucao.estado,
			mensagem: execucao.mensagem,
			erro: execucao.erro,
			respostas: (resultado.respostas ?? []).map((r) => ({ resposta: r.resposta, fontes: r.fontes ?? [] })),
			relatorio: resultado.relatorio ?? null,
			criada_em: execucao.criada_em,
			terminada_em: execucao.terminada_em
		};
	}

	async function rolarParaOFim() {
		await tick();
		lista?.scrollTo({ top: lista.scrollHeight, behavior: 'smooth' });
	}

	function pararRelogio() {
		clearInterval(relogio);
		relogio = undefined;
	}

	async function abrir(id: string | null) {
		const minha = ++geracao;
		emCurso = null;
		andamentoAtual = null;
		pararRelogio();
		erro = null;
		if (!id) {
			turnos = [];
			return;
		}
		carregando = true;
		try {
			const conversa = await api.conversa(id);
			if (minha !== geracao) return;
			turnos = conversa.turnos;
			const ultimo = turnos.at(-1);
			// Recarregou a página (ou voltou à conversa) no meio de uma resposta: continua bloqueado.
			if (ultimo && EM_ANDAMENTO.includes(ultimo.estado)) acompanhar(ultimo.execucao_id, Date.parse(ultimo.criada_em));
			rolarParaOFim();
		} catch (e) {
			if (minha === geracao) erro = e instanceof ErroApi ? e.message : 'Não foi possível abrir a conversa.';
		} finally {
			if (minha === geracao) carregando = false;
		}
	}

	async function acompanhar(execucaoId: string, inicio: number) {
		const minha = ++geracao;
		carregando = false;
		emCurso = { execucaoId, inicio };
		andamentoAtual = lerAndamento([], nomes);
		segundos = Math.max(0, Math.floor((Date.now() - inicio) / 1000));
		pararRelogio();
		relogio = setInterval(() => (segundos = Math.max(0, Math.floor((Date.now() - inicio) / 1000))), 1000);

		let tamanho = -1;
		let mudou = Date.now();
		let falhas = 0;
		while (minha === geracao) {
			let execucao: Execucao;
			try {
				execucao = await api.execucao(execucaoId);
				falhas = 0;
			} catch (e) {
				if (minha !== geracao) return;
				if (e instanceof ErroApi && e.status === 404) {
					erro = 'A execução não foi encontrada.';
					break;
				}
				if (++falhas >= 5) {
					erro = e instanceof ErroApi ? e.message : 'Perdi o contato com o servidor. Recarregue a página.';
					break;
				}
				await esperar(INTERVALO_MS * 2);
				continue;
			}
			if (minha !== geracao) return;

			const passos = execucao.passos ?? [];
			if (passos.length !== tamanho) {
				tamanho = passos.length;
				mudou = Date.now();
			}
			andamentoAtual = lerAndamento(passos, nomes, (Date.now() - mudou) / 1000);
			const turno = turnoDe(execucao);
			turnos = turnos.map((t) => (t.execucao_id === execucaoId ? turno : t));

			if (!EM_ANDAMENTO.includes(execucao.estado)) {
				aoMudarConversas();
				break;
			}
			await esperar(INTERVALO_MS);
		}
		if (minha === geracao) {
			emCurso = null;
			andamentoAtual = null;
			pararRelogio();
			await rolarParaOFim();
			caixa?.focus();
		}
	}

	async function enviar(mensagem: string) {
		const pedido = mensagem.trim();
		if (bloqueado || pedido.length < 2) return;
		enviando = true;
		erro = null;
		try {
			let id = conversaId;
			if (!id) {
				id = (await api.novaConversa()).conversa_id;
				criadaAqui = id;
				aoCriarConversa(id);
			}
			const execucao = await api.enviarMensagem(id, pedido);
			texto = '';
			turnos = [...turnos, turnoDe(execucao)];
			aoMudarConversas(); // a lista de conversas já mostra o pedido como título
			rolarParaOFim();
			acompanhar(execucao.execucao_id, Date.parse(execucao.criada_em));
		} catch (e) {
			if (e instanceof ErroApi && e.status === 409) {
				erro = e.message;
				abrir(conversaId); // retoma o acompanhamento do pedido que já está rodando
			} else {
				erro = e instanceof ErroApi ? e.message : 'Não foi possível enviar.';
			}
		} finally {
			enviando = false;
		}
	}

	function teclas(evento: KeyboardEvent) {
		if (evento.key === 'Enter' && !evento.shiftKey && !evento.isComposing) {
			evento.preventDefault();
			enviar(texto);
		}
	}

	// Trocar de conversa: carrega os turnos dela. A conversa criada aqui mesmo já está na tela.
	$effect(() => {
		const id = conversaId;
		untrack(() => {
			if (id && id === criadaAqui) return;
			criadaAqui = null;
			abrir(id);
		});
	});

	onDestroy(() => {
		geracao++;
		pararRelogio();
	});
</script>

<div class="chat">
	<div class="turnos" bind:this={lista}>
		{#if carregando}
			<p class="centro">Carregando a conversa…</p>
		{:else if !turnos.length}
			<div class="boas-vindas">
				<h1>O que você quer saber dos documentos?</h1>
				<p>
					Pergunte, compare ou peça o relatório consolidado. As respostas vêm dos documentos indexados, com a página de
					origem, e o que não estiver neles aparece como não encontrado.
				</p>
			</div>
		{/if}
		{#each turnos as turno (turno.execucao_id)}
			<Turno
				{turno}
				andamento={emCurso?.execucaoId === turno.execucao_id ? andamentoAtual : null}
				segundos={emCurso?.execucaoId === turno.execucao_id ? segundos : 0}
			/>
		{/each}
	</div>

	<div class="rodape">
		{#if indexados === 0}
			<p class="aviso">
				{processando
					? 'Os documentos ainda estão sendo indexados. Espere terminar para perguntar.'
					: 'Nenhum documento indexado. Envie os PDFs na lateral antes de perguntar.'}
			</p>
		{:else if processando}
			<p class="aviso leve">{processando} documento(s) ainda em processamento: as respostas usam só os já indexados.</p>
		{/if}
		{#if erro}
			<p class="aviso erro" role="alert">{erro}</p>
		{/if}

		<div class="sugestoes">
			{#each SUGESTOES as sugestao (sugestao)}
				<button type="button" disabled={bloqueado} onclick={() => enviar(sugestao)}>{sugestao}</button>
			{/each}
		</div>

		<form
			class="entrada"
			class:bloqueada={bloqueado}
			onsubmit={(e) => {
				e.preventDefault();
				enviar(texto);
			}}
		>
			<label class="sr-only" for="mensagem">Mensagem</label>
			<textarea
				id="mensagem"
				bind:this={caixa}
				bind:value={texto}
				onkeydown={teclas}
				disabled={bloqueado}
				rows="1"
				maxlength="2000"
				placeholder={bloqueado ? 'Aguarde a resposta…' : 'Pergunte algo ou peça o relatório (Enter envia, Shift+Enter quebra linha)'}
			></textarea>
			<button class="botao" type="submit" disabled={bloqueado || texto.trim().length < 2} aria-label="Enviar">
				{#if bloqueado}
					<span class="giro" aria-hidden="true"></span>
				{:else}
					<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
						<path d="M5 12h13m0 0-5-5m5 5-5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
					</svg>
				{/if}
			</button>
		</form>
	</div>
</div>

<style>
	.chat {
		display: grid;
		grid-template-rows: 1fr auto;
		height: 100%;
		min-height: 0;
	}

	.turnos {
		overflow-y: auto;
		padding: 24px max(16px, env(safe-area-inset-right, 0px)) 24px max(16px, env(safe-area-inset-left, 0px));
		display: flex;
		flex-direction: column;
		gap: 22px;
		min-height: 0;
	}

	.turnos > :global(*) {
		width: 100%;
		max-width: 860px;
		margin-inline: auto;
	}

	.boas-vindas {
		margin-top: 8vh;
		display: grid;
		gap: 10px;
		text-align: center;
		justify-items: center;
	}

	.boas-vindas h1 {
		font-size: 24px;
	}

	.boas-vindas p {
		margin: 0;
		max-width: 56ch;
		color: var(--muted);
	}

	.centro {
		text-align: center;
		color: var(--muted);
	}

	.rodape {
		border-top: 1px solid var(--line);
		background: var(--bg);
		padding: 12px max(16px, env(safe-area-inset-right, 0px)) max(14px, env(safe-area-inset-bottom, 0px))
			max(16px, env(safe-area-inset-left, 0px));
		display: grid;
		gap: 10px;
	}

	.rodape > :global(*) {
		width: 100%;
		max-width: 860px;
		margin-inline: auto;
	}

	.aviso {
		margin: 0;
		padding: 8px 12px;
		border-radius: 8px;
		background: var(--warn-soft);
		color: var(--warn);
		font-size: 13px;
	}

	.aviso.leve {
		background: var(--surface-2);
		color: var(--muted);
	}

	.aviso.erro {
		background: var(--danger-soft);
		color: var(--danger);
	}

	.sugestoes {
		display: flex;
		gap: 6px;
		overflow-x: auto;
		scrollbar-width: none;
	}

	.sugestoes button {
		flex: none;
		border: 1px solid var(--line);
		background: var(--surface);
		border-radius: 999px;
		padding: 4px 12px;
		font-size: 13px;
		color: var(--muted);
	}

	.sugestoes button:hover:not(:disabled) {
		color: var(--accent);
		border-color: var(--accent);
	}

	.sugestoes button:disabled {
		opacity: 0.5;
	}

	.entrada {
		display: flex;
		gap: 8px;
		align-items: flex-end;
		background: var(--surface);
		border: 1px solid var(--line);
		border-radius: 14px;
		padding: 8px 8px 8px 14px;
		box-shadow: var(--shadow);
	}

	.entrada:focus-within {
		border-color: var(--accent);
	}

	.entrada.bloqueada {
		background: var(--surface-2);
	}

	textarea {
		flex: 1;
		min-width: 0;
		border: 0;
		background: transparent;
		resize: none;
		outline: none;
		padding: 6px 0;
		field-sizing: content;
		max-height: 200px;
		min-height: 24px;
	}

	textarea:disabled {
		color: var(--muted);
	}

	.entrada .botao {
		width: 40px;
		height: 40px;
		padding: 0;
		border-radius: 10px;
		flex: none;
	}

	.giro {
		width: 16px;
		height: 16px;
		border-radius: 50%;
		border: 2px solid currentColor;
		border-right-color: transparent;
		animation: giro 0.8s linear infinite;
	}

	@keyframes giro {
		to {
			transform: rotate(360deg);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.giro {
			animation: none;
		}
	}
</style>
