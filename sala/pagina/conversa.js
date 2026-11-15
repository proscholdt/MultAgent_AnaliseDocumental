// A janela de conversa (falas, pensamentos e portões, com filtro) e o painel de controle.
// Scripts clássicos, carregados em ordem pelo index.html: o que um declara, os seguintes enxergam.

// ============================================================================================
// Conversa e painel
// ============================================================================================
// A conversa inteira da execução: falas, pensamentos e recusas dos portões, com o texto completo.
const falas = [];
const MAX_FALAS = 3000;
const caixaFalas = document.querySelector('#conversa .linhas');
function hora() { return new Date().toTimeString().slice(0, 8); }
function passaFiltro(f) {
  const texto = document.getElementById('conversa-filtro').value.trim().toLowerCase();
  const tipo = document.getElementById('conversa-tipo').value;
  return (!tipo || f.tipo === tipo) && (!texto || `${f.de} ${f.para} ${f.texto}`.toLowerCase().includes(texto));
}
function linhaFala(f) {
  const div = document.createElement('div');
  div.className = `l ${f.tipo}`;
  const seta = f.tipo === 'pensa' ? ' (pensando)' : f.para ? ' → ' + f.para : '';
  div.innerHTML = `<span class="h">${f.h}</span><b>${esc(f.de)}${esc(seta)}:</b> ${esc(f.texto)}`;
  div.hidden = !passaFiltro(f);
  f.el = div;
  return div;
}
function contarFalas() {
  const visiveis = falas.filter((f) => !f.el.hidden).length;
  document.getElementById('conversa-n').textContent = visiveis === falas.length ? `(${falas.length})` : `(${visiveis}/${falas.length})`;
}
function registrarFala(p, para, texto, tipo = 'fala') {
  const noFim = caixaFalas.scrollHeight - caixaFalas.scrollTop - caixaFalas.clientHeight < 30;
  const f = { de: p.v.nome, para: para ? VISUAL[para].nome : '', texto: String(texto ?? ''), tipo, h: hora() };
  falas.push(f);
  caixaFalas.appendChild(linhaFala(f));
  if (falas.length > MAX_FALAS) falas.shift().el.remove();
  if (noFim) caixaFalas.scrollTop = caixaFalas.scrollHeight; // quem rolou para cima não é puxado de volta
  contarFalas();
}
function aplicarFiltroFalas() {
  falas.forEach((f) => (f.el.hidden = !passaFiltro(f)));
  contarFalas();
  caixaFalas.scrollTop = caixaFalas.scrollHeight;
}
document.getElementById('conversa-filtro').oninput = aplicarFiltroFalas;
document.getElementById('conversa-tipo').onchange = aplicarFiltroFalas;
const janelaConversa = document.getElementById('conversa');
document.getElementById('conversa-min').onclick = () => {
  janelaConversa.classList.toggle('min');
  if (janelaConversa.classList.contains('min')) janelaConversa.classList.remove('max');
  atualizarBotoesConversa();
};
document.getElementById('conversa-max').onclick = () => {
  janelaConversa.classList.toggle('max');
  janelaConversa.classList.remove('min');
  atualizarBotoesConversa();
  caixaFalas.scrollTop = caixaFalas.scrollHeight;
};
function atualizarBotoesConversa() {
  const max = janelaConversa.classList.contains('max'), min = janelaConversa.classList.contains('min');
  const bMax = document.getElementById('conversa-max'), bMin = document.getElementById('conversa-min');
  bMax.textContent = max ? '❐' : '□'; bMax.title = max ? 'Restaurar' : 'Maximizar';
  bMin.textContent = min ? '▴' : '_'; bMin.title = min ? 'Abrir' : 'Minimizar';
}
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

let execInfo = null, chamadas = 0, barradas = 0;
function painel() {
  const ativos = ESPECIALISTAS.filter((n) => pessoas[n].status === 'TRABALHANDO').length;
  const ex = execInfo || {};
  const inicio = ex.iniciada_em ? Date.parse(ex.iniciada_em) : null;
  const fim = ex.terminada_em ? Date.parse(ex.terminada_em) : Date.now();
  const seg = inicio ? Math.max(0, Math.round((fim - inicio) / 1000)) : 0;
  const custo = ex.uso?.total?.custo_usd;
  const linhas = [
    ['AGENTES ATIVOS', ativos], ['EXECUÇÃO', (ex.tipo || '-').toUpperCase()], ['ESTADO', (ex.estado || '-').toUpperCase()],
    ['TEMPO', seg ? `${Math.floor(seg / 60)}:${String(seg % 60).padStart(2, '0')}` : '-'],
    ['CHAMADAS DE TOOL', chamadas], ['BARRADAS NO PORTÃO', barradas], ['CUSTO', custo != null ? 'US$ ' + custo.toFixed(3) : '-'],
  ];
  document.querySelector('#painel .linhas').innerHTML = linhas.map(([k, v]) => `<div><span>${k}</span><span class="v">${esc(v)}</span></div>`).join('');
}
setInterval(painel, 500);

