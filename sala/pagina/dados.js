// Os dados: a API da sala, os eventos da aba Logs, abrir, repetir e acompanhar uma execução.
// Scripts clássicos, carregados em ordem pelo index.html: o que um declara, os seguintes enxergam.

// ============================================================================================
// Dados
// ============================================================================================
let execucaoId = null, vistos = 0, auditVistos = new Set(), seguir = true, emCurso = false, todos = [];

async function api(caminho, init) {
  const r = await fetch(caminho, init);
  if (r.status === 401) { document.getElementById('login').hidden = false; throw new Error('401'); }
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function chaveAudit(l) { return `${l.quando}|${l.agente}|${l.tool}|${l.resultado}`; }

function resetarCena() {
  Object.values(pessoas).forEach((p) => {
    p.limpar(); const s = LUGARES[p.id].sentado; p.x = s.x; p.y = s.y; p.sentado = true; p.lugar = p.id; p.status = 'LIVRE'; p.dir = 'baixo';
  });
  pacotes.length = 0; falas.length = 0; chamadas = 0; barradas = 0; pensamentos = {};
  caixaFalas.innerHTML = ''; contarFalas();
  document.getElementById('eventos').innerHTML = '';
}

function linhaLog(t, quem, tipo, detalhe, cls = '') {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td class="t">${esc((t || '').slice(11, 19))}</td><td class="q">${esc(quem)}</td><td><span class="tg ${cls}">${esc(tipo)}</span></td><td>${esc(detalhe)}</td>`;
  const tb = document.getElementById('eventos'); tb.prepend(tr);
  while (tb.children.length > 1500) tb.lastChild.remove();
  aplicarFiltro(tr);
}
function logPasso(p) {
  if (p.tipo === 'tool') return; // as chamadas vêm da auditoria do MCP, na hora
  const d = { delegando: `${nomeDoc(p.doc_id)} ${p.tarefa || ''}`, especialista: `${p.estado} ${JSON.stringify(p.gravacoes || {})} ${p.segundos ?? ''}s`,
    raciocinio: p.texto, orquestrador: p.decisao ? `onda ${p.onda}: ${(p.decisao.delegacoes || []).map((x) => x.agente).join(', ') || (p.decisao.terminado ? 'terminado' : '-')}` : (p.erro || ''),
    inicio: p.pedido, intencao: `${p.intencao} → ${p.tipo_execucao}`, conferencia: `completo=${p.completo} pendencias=${p.pendencias}`, fim: `${p.estado} ${p.erro || ''}` }[p.tipo] ?? JSON.stringify(p);
  linhaLog(p.em, p.agente || (['inicio', 'fim', 'conferencia', 'intencao', 'orquestrador', 'conversa'].includes(p.tipo) ? 'orquestrador' : 'sistema'),
    p.tipo, d, p.tipo === 'raciocinio' ? 'pensa' : p.tipo === 'fim' ? 'ok' : '');
}
function logAudit(l) {
  linhaLog(l.quando, l.agente, 'tool ' + l.tool, `${l.resultado}${l.detalhe ? ' · ' + l.detalhe : ''} (${l.duracao_ms} ms)`, l.resultado === 'ok' ? 'ok' : 'ruim');
}
function aplicarFiltro(tr) {
  const f = document.getElementById('filtro').value.toLowerCase();
  const linhas = tr ? [tr] : document.querySelectorAll('#eventos tr');
  linhas.forEach((x) => (x.hidden = f && !x.textContent.toLowerCase().includes(f)));
}
document.getElementById('filtro').oninput = () => aplicarFiltro();

async function carregarDocs() {
  try { nomesDocs = await api('/api/documentos'); } catch { /* fica o id curto */ }
}

// A auditoria do MCP vem sem fuso (é UTC); o trace vem com "+00:00".
function instante(s) { return Date.parse(/[zZ]$|[+-]\d\d:\d\d$/.test(s) ? s : s + 'Z'); }

// Repetição em andamento: os eventos agendados, para cancelar se outra execução for aberta.
let repeticao = { timers: [] };
function cancelarRepeticao() { repeticao.timers.forEach(clearTimeout); repeticao = { timers: [] }; }

// Abre uma execução. animar=false: só mostra o estado atual (sem refazer o que já passou).
// animar='repetir': refaz do começo no ritmo em que aconteceu, dividido pela velocidade (1× = tempo real).
async function abrir(id, animar) {
  cancelarRepeticao();
  execucaoId = id; vistos = 0; auditVistos = new Set();
  resetarCena();
  const dados = await api(`/api/estado?execucao=${id}&desde=0`);
  execInfo = dados.execucao;
  const vel = animar === 'repetir' ? Number(document.getElementById('vel').value) : 1;
  velocidadeBase = vel;
  const passos = dados.passos;
  // trace e auditoria juntos, na ordem do tempo
  const tudo = [...passos.map((p) => ({ em: p.em, p })), ...dados.auditoria.map((l) => ({ em: l.quando, l }))]
    .sort((a, b) => instante(a.em) - instante(b.em));
  const rodar = (item) => {
    if (item.p) { logPasso(item.p); if (animar) evento(item.p); }
    else { logAudit(item.l); if (animar) chamada(item.l); }
  };
  tudo.forEach((item) => item.l && auditVistos.add(chaveAudit(item.l)));
  vistos = dados.total_passos;
  if (animar === 'repetir' && tudo.length) {
    const inicio = instante(tudo[0].em), r = repeticao;
    for (const item of tudo) {
      r.timers.push(setTimeout(() => { if (r === repeticao) rodar(item); }, (instante(item.em) - inicio) / vel));
    }
    return;
  }
  tudo.forEach(rodar);
  if (!animar) estadoEstatico(passos, dados.auditoria);
}

function estadoEstatico(passos, auditoria) {
  chamadas = auditoria.length; barradas = auditoria.filter((l) => l.resultado !== 'ok').length;
  const abertos = {};
  passos.forEach((p) => { if (p.tipo === 'delegando') abertos[p.agente] = (abertos[p.agente] || 0) + 1; if (p.tipo === 'especialista') abertos[p.agente] = (abertos[p.agente] || 1) - 1; });
  Object.entries(abertos).forEach(([a, n]) => { if (n > 0 && pessoas[a]) pessoas[a].status = 'TRABALHANDO'; });
  const fim = passos.find((p) => p.tipo === 'fim');
  if (!fim && passos.length) { pessoas.orquestrador.status = 'TRABALHANDO'; pessoas.usuario.status = 'ESPERANDO'; }
}

async function acompanhar() {
  if (!execucaoId) return;
  try {
    const dados = await api(`/api/estado?execucao=${execucaoId}&desde=${vistos}`);
    execInfo = dados.execucao;
    const novos = [...dados.passos.map((p) => ({ em: p.em, p })),
      ...dados.auditoria.filter((l) => !auditVistos.has(chaveAudit(l))).map((l) => ({ em: l.quando, l }))]
      .sort((a, b) => instante(a.em) - instante(b.em));
    for (const item of novos) {
      if (item.p) { logPasso(item.p); evento(item.p); }
      else { auditVistos.add(chaveAudit(item.l)); logAudit(item.l); chamada(item.l); }
    }
    vistos = dados.total_passos;
  } catch (e) { /* tenta de novo no próximo ciclo */ }
}

async function listar() {
  try {
    todos = await api('/api/execucoes');
  } catch { return; }
  const sel = document.getElementById('execucoes'), atual = sel.value;
  sel.innerHTML = todos.map((r) => `<option value="${r.execucao_id}">${r.criada_em.slice(5, 16).replace('T', ' ')} · ${r.tipo} · ${r.estado} · ${esc(curto(r.pedido, 46))}</option>`).join('');
  if (execucaoId) sel.value = execucaoId; else if (atual) sel.value = atual;
  if (seguir && todos.length) {
    const nova = todos[0];
    if (nova.execucao_id !== execucaoId) {
      const viva = ['na_fila', 'em_andamento'].includes(nova.estado);
      await abrir(nova.execucao_id, viva); // execução nova, rodando: anima desde o começo
      sel.value = nova.execucao_id;
    }
  }
}

document.getElementById('execucoes').onchange = (e) => { document.getElementById('seguir').checked = seguir = false; abrir(e.target.value, false); };
document.getElementById('seguir').onchange = (e) => { seguir = e.target.checked; if (seguir) { execucaoId = null; listar(); } };
document.getElementById('repetir').onclick = () => { if (execucaoId) abrir(execucaoId, 'repetir'); };

