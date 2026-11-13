// Tipos globais do SvelteKit: veja https://svelte.dev/docs/kit/types#app.d.ts
declare global {
	namespace App {
		interface Locals {
			/** O JWT do indexador, lido do cookie httpOnly. Só existe no servidor. */
			token: string | null;
			usuario: string | null;
		}
		interface PageData {
			usuario?: string | null;
		}
	}
}

export {};
