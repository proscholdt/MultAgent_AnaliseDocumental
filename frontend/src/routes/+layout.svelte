<script lang="ts">
	import '../app.css';

	let { children } = $props();
</script>

<svelte:head>
	<title>Análise de Demonstrações</title>
</svelte:head>

{@render children()}
