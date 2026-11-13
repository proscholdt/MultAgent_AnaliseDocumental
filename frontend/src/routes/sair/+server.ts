import { redirect, type RequestHandler } from '@sveltejs/kit';
import { fechar } from '$lib/server/sessao';

export const POST: RequestHandler = ({ cookies }) => {
	fechar(cookies);
	redirect(303, '/login');
};
