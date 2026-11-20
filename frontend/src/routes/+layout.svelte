<script lang="ts">
	import '../app.css';

	let { children } = $props();
</script>

<svelte:head>
	<title>Análise de documentos</title>
</svelte:head>

{@render children()}
