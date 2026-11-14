import { ajustes } from '$lib/server/ajustes';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ locals }) => {
	const { sugestoes, intervalo_ms } = ajustes(); // config/ajustes.yaml, relido quando muda
	return { usuario: locals.usuario, sugestoes, intervaloMs: intervalo_ms };
};
