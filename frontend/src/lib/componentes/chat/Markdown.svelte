<script lang="ts">
	import { paraHtml } from '$lib/markdown';

	let { texto }: { texto: string } = $props();

	const html = $derived(paraHtml(texto));
</script>

<!-- O HTML já passou pelo DOMPurify em paraHtml. -->
<div class="md">{@html html}</div>

<style>
	.md {
		overflow-wrap: anywhere;
		min-width: 0;
	}

	.md :global(> :first-child) {
		margin-top: 0;
	}

	.md :global(> :last-child) {
		margin-bottom: 0;
	}

	.md :global(p),
	.md :global(ul),
	.md :global(ol) {
		margin: 0 0 10px;
	}

	.md :global(h1) {
		font-size: 20px;
		margin: 18px 0 10px;
	}

	.md :global(h2) {
		font-size: 17px;
		margin: 18px 0 8px;
		padding-bottom: 4px;
		border-bottom: 1px solid var(--line);
	}

	.md :global(h3) {
		font-size: 15px;
		margin: 14px 0 6px;
	}

	.md :global(table) {
		display: block;
		overflow-x: auto;
		border-collapse: collapse;
		margin: 8px 0 14px;
		font-size: 13px;
		font-variant-numeric: tabular-nums;
	}

	.md :global(th),
	.md :global(td) {
		border: 1px solid var(--line);
		padding: 5px 9px;
		text-align: left;
		vertical-align: top;
	}

	.md :global(th) {
		background: var(--surface-2);
		font-weight: 600;
	}

	.md :global(code) {
		font-family: var(--mono);
		font-size: 0.88em;
		background: var(--surface-2);
		padding: 1px 5px;
		border-radius: 4px;
	}

	.md :global(blockquote) {
		margin: 0 0 10px;
		padding: 4px 12px;
		border-left: 3px solid var(--line);
		color: var(--muted);
	}

	.md :global(a) {
		color: var(--accent);
	}
</style>
