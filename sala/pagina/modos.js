// Os modos da sala: o mesmo escritório com outra cara. Cada modo é um estilo e, fora o
// escritório, um script que redesenha a cena (carregado depois dos outros, antes do início).
// O modo vem da URL (?modo=) ou da última escolha (o padrão é a caverna); trocar recarrega a página.
const TEMAS = {
  escritorio: {
    nome: 'Escritório', css: 'sala.css', js: null,
    pagina: 'Sala dos Agentes', titulo: '▮ SALA DOS AGENTES', painel: 'PAINEL DE CONTROLE', login: 'ENTRAR', entrar: 'Entrar na sala',
  },
  psicodelico: {
    nome: 'Psicodélico', css: 'psicodelico.css', js: 'psicodelico.js',
    pagina: 'Sala da Força', titulo: 'SALA DA FORÇA', subtitulo: 'o orquestrador, mestre é', painel: 'PAINEL DA FORÇA',
    login: 'ENTRAR NA SALA,<br>VOCÊ DEVE', entrar: 'Entrar',
  },
  caverna: {
    nome: 'Caverna do Dragão', css: 'caverna.css', js: 'caverna.js',
    pagina: 'Caverna do Dragão', titulo: 'Caverna do Dragão', subtitulo: 'a sala dos agentes, guardada pela Tiamat', painel: 'O MAPA DA JORNADA',
    login: 'Entrem no portal, jovens', entrar: 'Atravessar o portal',
  },
};

const MODO = (() => {
  let salvo = null;
  try { salvo = localStorage.getItem('sala.modo'); } catch { /* sem armazenamento: fica o padrão */ }
  const pedido = new URLSearchParams(location.search).get('modo') || salvo;
  return TEMAS[pedido] ? pedido : 'caverna';
})();
document.documentElement.dataset.modo = MODO;
(() => {
  const estilo = document.createElement('link');
  estilo.rel = 'stylesheet'; estilo.href = TEMAS[MODO].css;
  document.head.appendChild(estilo);
})();

document.addEventListener('DOMContentLoaded', () => {
  const t = TEMAS[MODO];
  document.title = t.pagina;
  document.getElementById('titulo').textContent = t.titulo;
  document.getElementById('subtitulo').textContent = t.subtitulo || '';
  document.getElementById('painel-titulo').textContent = t.painel;
  document.getElementById('login-titulo').innerHTML = t.login;
  document.getElementById('login-entrar').textContent = t.entrar;
  const sel = document.getElementById('modo');
  sel.innerHTML = Object.entries(TEMAS).map(([id, x]) => `<option value="${id}">${x.nome}</option>`).join('');
  sel.value = MODO;
  sel.onchange = () => {
    try { localStorage.setItem('sala.modo', sel.value); } catch { /* vale só pela URL */ }
    const url = new URL(location.href); url.searchParams.set('modo', sel.value); location.href = url.toString();
  };
  // os scripts da sala, em ordem (async=false mantém a ordem de execução)
  ['cena.js', 'conversa.js', 'eventos.js', 'dados.js', 'logs.js', t.js, 'inicio.js'].filter(Boolean).forEach((src) => {
    const s = document.createElement('script'); s.src = src; s.async = false; document.body.appendChild(s);
  });
});
