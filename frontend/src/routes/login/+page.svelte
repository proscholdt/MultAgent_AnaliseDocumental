<script lang="ts">
	import { enhance } from '$app/forms';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let criando = $state(false);
	let enviando = $state(false);

	$effect(() => {
		if (form && 'criar' in form && form.criar) criando = true;
	});
</script>

<svelte:head>
	<title>Entrar · Análise de Demonstrações</title>
</svelte:head>

<main class="pagina">
	<section class="cartao">
		<header>
			<img src="/favicon.svg" alt="" width="40" height="40" />
			<div>
				<h1>Análise de demonstrações</h1>
				<p>Envie os PDFs e converse com os agentes: perguntas com fonte, relatório consolidado e mais.</p>
			</div>
		</header>

		{#if data.expirada}
			<p class="aviso">Sua sessão venceu. Entre de novo.</p>
		{/if}

		<form
			method="POST"
			action={criando ? '?/criar' : '?/entrar'}
			use:enhance={() => {
				enviando = true;
				return async ({ update }) => {
					await update();
					enviando = false;
				};
			}}
		>
			<label>
				<span class="rotulo">Usuário</span>
				<input name="usuario" autocomplete="username" required value={form?.usuario ?? ''} />
			</label>
			<label>
				<span class="rotulo">Senha</span>
				<input
					name="senha"
					type="password"
					autocomplete={criando ? 'new-password' : 'current-password'}
					required
					minlength={criando ? 8 : undefined}
				/>
			</label>

			{#if form?.erro}
				<p class="erro" role="alert">{form.erro}</p>
			{/if}

			<button class="botao" type="submit" disabled={enviando}>
				{enviando ? 'Aguarde…' : criando ? 'Criar conta e entrar' : 'Entrar'}
			</button>
		</form>

		<p class="troca">
			{#if criando}
				Já tem conta? <button type="button" class="link" onclick={() => (criando = false)}>Entrar</button>
			{:else}
				Primeira vez? <button type="button" class="link" onclick={() => (criando = true)}>Criar conta</button>
			{/if}
		</p>
		<p class="nota">Cada usuário vê só os próprios documentos e conversas.</p>
	</section>
</main>

<style>
	.pagina {
		min-height: 100vh;
		display: grid;
		place-items: center;
		padding: 24px max(16px, env(safe-area-inset-right, 0px)) 24px max(16px, env(safe-area-inset-left, 0px));
	}

	.cartao {
		width: min(420px, 100%);
		background: var(--surface);
		border: 1px solid var(--line);
		border-radius: 14px;
		box-shadow: var(--shadow);
		padding: 28px;
		display: grid;
		gap: 18px;
	}

	header {
		display: flex;
		gap: 14px;
		align-items: flex-start;
	}

	h1 {
		font-size: 20px;
	}

	header p {
		margin: 4px 0 0;
		color: var(--muted);
		font-size: 14px;
	}

	form {
		display: grid;
		gap: 14px;
	}

	label {
		display: grid;
		gap: 6px;
	}

	input {
		border: 1px solid var(--line);
		background: var(--bg);
		border-radius: 8px;
		padding: 10px 12px;
	}

	.erro,
	.aviso {
		margin: 0;
		padding: 8px 12px;
		border-radius: 8px;
		font-size: 14px;
	}

	.erro {
		background: var(--danger-soft);
		color: var(--danger);
	}

	.aviso {
		background: var(--warn-soft);
		color: var(--warn);
	}

	.troca,
	.nota {
		margin: 0;
		font-size: 14px;
		color: var(--muted);
	}

	.nota {
		font-size: 12px;
	}

	.link {
		background: none;
		border: 0;
		padding: 0;
		color: var(--accent);
		font-weight: 600;
		text-decoration: underline;
	}
</style>
