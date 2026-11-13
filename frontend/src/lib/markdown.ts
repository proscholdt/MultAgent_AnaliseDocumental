/**
 * Markdown do modelo e do relatório para HTML. O texto vem dos agentes (e, no fundo, dos
 * documentos), então é limpo pelo DOMPurify antes de ir para a tela: nada de script, de
 * atributo de evento ou de link `javascript:`.
 */
import DOMPurify from 'dompurify';
import { marked } from 'marked';

let configurado = false;

function configurar() {
	if (configurado) return;
	configurado = true;
	// Link vindo do texto abre em outra aba, sem dar acesso a esta.
	DOMPurify.addHook('afterSanitizeAttributes', (no) => {
		if (no.tagName === 'A') {
			no.setAttribute('target', '_blank');
			no.setAttribute('rel', 'noopener noreferrer');
		}
	});
}

export function paraHtml(texto: string): string {
	configurar();
	const html = marked.parse(texto ?? '', { gfm: true, breaks: true, async: false }) as string;
	return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}
