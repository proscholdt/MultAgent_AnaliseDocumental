// O escritório: os lugares, as pessoas, a fila de ações de cada uma e o desenho no canvas.
// Scripts clássicos, carregados em ordem pelo index.html: o que um declara, os seguintes enxergam.

// ============================================================================================
// Cenário
// ============================================================================================
const tela = document.getElementById('tela');
const ctx = tela.getContext('2d');
ctx.imageSmoothingEnabled = false;
const W = tela.width, H = tela.height, CORREDOR = 340;

const XS = [130, 345, 560, 775, 990];
const ESPECIALISTAS = ['extrator', 'validador', 'analista', 'pesquisador', 'redator'];
const LUGARES = {
  usuario: { sentado: { x: 135, y: 150 }, visita: { x: 205, y: 272 }, faixa: 282 },
  orquestrador: { sentado: { x: 545, y: 150 }, visita: { x: 545, y: 232 }, faixa: 650 },
  mcp: { visita: { x: 830, y: 262 }, faixa: 830 },
};
ESPECIALISTAS.forEach((nome, i) => {
  LUGARES[nome] = { sentado: { x: XS[i], y: 452 }, visita: { x: XS[i], y: 540 }, faixa: XS[i] - 98 };
});
const RACK = { x: 790, y: 135 }, ARQUIVO = { x: 1030, y: 150 };

const VISUAL = {
  usuario:      { nome: 'VOCÊ',         camisa: '#2f4f86', cabelo: '#3b2a1a', pele: '#f1c27d', calca: '#1f2a3a' },
  orquestrador: { nome: 'ORQUESTRADOR', camisa: '#1f2937', cabelo: '#111111', pele: '#e0ac69', calca: '#111827', gravata: '#c0392b' },
  extrator:     { nome: 'EXTRATOR',     camisa: '#0f766e', cabelo: '#5b3a1e', pele: '#f1c27d', calca: '#243142' },
  validador:    { nome: 'VALIDADOR',    camisa: '#b42318', cabelo: '#8d5524', pele: '#c68642', calca: '#243142' },
  analista:     { nome: 'ANALISTA',     camisa: '#7c3aed', cabelo: '#e6be8a', pele: '#ffdbac', calca: '#243142' },
  pesquisador:  { nome: 'PESQUISADOR',  camisa: '#b7791f', cabelo: '#2d1b0e', pele: '#8d5524', calca: '#243142' },
  redator:      { nome: 'REDATOR',      camisa: '#2563eb', cabelo: '#a0522d', pele: '#f1c27d', calca: '#243142' },
};
const STATUS_COR = { LIVRE: '#5c6b7a', TRABALHANDO: '#2fbf71', ESPERANDO: '#d29922', REUNIÃO: '#4c8ff0', ANDANDO: '#7d8590', PENSANDO: '#a371f7', 'NO PORTÃO': '#e5534b' };

// ============================================================================================
// Pessoas e ações
// ============================================================================================
let velocidadeBase = 1;
class Pessoa {
  constructor(id) {
    this.id = id; this.v = VISUAL[id]; this.lugar = id;
    const s = LUGARES[id].sentado; this.x = s.x; this.y = s.y;
    this.sentado = true; this.fila = []; this.atual = null; this.status = 'LIVRE';
    this.bolha = null; this.passo = 0; this.digitando = 0; this.ferramenta = null; this.ferramentaAte = 0;
  }
  fazer(acao) { this.fila.push(acao); }
  get vel() { return Math.min(8, velocidadeBase * (1 + this.fila.length / 4)); }
  limpar() { this.fila = []; this.atual = null; this.bolha = null; }
}
const pessoas = {};
['usuario', 'orquestrador', ...ESPECIALISTAS].forEach((id) => (pessoas[id] = new Pessoa(id)));
const pacotes = [];
let piscaRack = 0;

function caminho(p, lugarAlvo, alvo) {
  const de = LUGARES[p.lugar], para = LUGARES[lugarAlvo];
  return [{ x: de.faixa, y: p.y }, { x: de.faixa, y: CORREDOR }, { x: para.faixa, y: CORREDOR }, { x: para.faixa, y: alvo.y }, alvo];
}

function iniciar(p, a) {
  a.t = 0;
  if (a.tipo === 'andar') {
    p.sentado = false; p.status = a.status || 'ANDANDO';
    a.pts = caminho(p, a.lugar, a.alvo);
  } else if (a.tipo === 'sentar') {
    const s = LUGARES[p.id].sentado;
    a.pts = caminho(p, p.id, s); p.sentado = false; p.status = 'ANDANDO';
  } else if (a.tipo === 'falar' || a.tipo === 'pensar' || a.tipo === 'alerta') {
    p.bolha = { tipo: a.tipo, texto: a.texto };
    if (a.tipo === 'pensar') p.statusAntes = p.status;
    // Na conversa vai o texto inteiro; o balão mostra só o começo.
    registrarFala(p, a.para, a.completo ?? a.texto, { falar: 'fala', pensar: 'pensa', alerta: 'portao' }[a.tipo]);
  } else if (a.tipo === 'status') {
    p.status = a.valor;
  } else if (a.tipo === 'acao') {
    a.fn();
  } else if (a.tipo === 'voltar') {
    if (p.fila.some((x) => x.tipo === 'andar')) return true; // vai a outro lugar: não volta à mesa
    p.fila.unshift({ tipo: 'sentar' });
  }
  return ['status', 'acao', 'voltar'].includes(a.tipo);
}

function atualizar(p, dt) {
  if (!p.atual) {
    while (p.fila.length) {
      const a = p.fila.shift();
      if (iniciar(p, a)) continue;
      p.atual = a; break;
    }
    if (!p.atual) return;
  }
  const a = p.atual, v = p.vel;
  if (a.tipo === 'andar' || a.tipo === 'sentar') {
    let restante = 150 * v * dt;
    while (restante > 0 && a.pts.length) {
      const alvo = a.pts[0], dx = alvo.x - p.x, dy = alvo.y - p.y, d = Math.hypot(dx, dy);
      if (d <= restante) { p.x = alvo.x; p.y = alvo.y; restante -= d; a.pts.shift(); }
      else { p.x += (dx / d) * restante; p.y += (dy / d) * restante; restante = 0; p.dir = dy < -0.5 ? 'cima' : 'baixo'; }
    }
    p.passo += dt * 10 * v;
    if (!a.pts.length) {
      p.atual = null;
      if (a.tipo === 'sentar') { p.sentado = true; p.lugar = p.id; p.dir = 'baixo'; if (p.status === 'ANDANDO') p.status = p.statusDepois || 'LIVRE'; }
      else { p.lugar = a.lugar; p.dir = 'cima'; p.status = a.statusChegada || 'REUNIÃO'; }
    }
  } else {
    a.t += dt * 1000 * v;
    const dur = a.ms || 1800;
    if (a.tipo === 'trabalhar') p.digitando = 0.4;
    if (a.t >= dur) {
      p.atual = null;
      if (['falar', 'pensar', 'alerta'].includes(a.tipo)) p.bolha = null;
    }
  }
}

// ============================================================================================
// Desenho
// ============================================================================================
function chao() {
  for (let y = 40; y < H; y += 20) for (let x = 0; x < W; x += 20) {
    ctx.fillStyle = ((x + y) / 20) % 2 ? '#c9c2b4' : '#c2bbac'; ctx.fillRect(x, y, 20, 20);
  }
  ctx.fillStyle = '#3a4656'; ctx.fillRect(0, 0, W, 40);           // parede do fundo
  ctx.fillStyle = '#4b5a6c'; for (let x = 10; x < W; x += 140) ctx.fillRect(x, 8, 90, 22); // janelas
  ctx.fillStyle = '#9db3c9'; for (let x = 14; x < W; x += 140) ctx.fillRect(x, 12, 82, 14);
  ctx.fillStyle = '#7f8c9a'; ctx.fillRect(0, CORREDOR - 22, W, 44); // corredor
  ctx.fillStyle = '#8d9aa8'; for (let x = 0; x < W; x += 40) ctx.fillRect(x, CORREDOR - 1, 22, 2);
  // sala do usuário
  ctx.fillStyle = '#41538a'; ctx.fillRect(20, 52, 236, 248);
  ctx.fillStyle = '#4a5f9c'; for (let y = 60; y < 300; y += 16) ctx.fillRect(28, y, 220, 2);
  ctx.fillStyle = '#5c4636'; ctx.fillRect(16, 48, 244, 6); ctx.fillRect(16, 48, 6, 256); ctx.fillRect(254, 48, 6, 196);
  rotulo('SALA DO USUÁRIO', 34, 292, '#cfd8ff');
  // sala do MCP e arquivo
  ctx.fillStyle = '#2d3743'; ctx.fillRect(720, 52, 384, 196);
  ctx.fillStyle = '#36424f'; for (let x = 724; x < 1100; x += 24) ctx.fillRect(x, 52, 2, 196);
  rotulo('SERVIDOR MCP · PORTÕES', 736, 72, '#9ef0c5');
  rotulo('ARQUIVO · INDEXADOR', 960, 72, '#ffd59a');
  // copa (faixa de baixo): piso de madeira, tapete, plantas, cafeteira
  ctx.fillStyle = '#8a6a4a'; ctx.fillRect(0, 590, W, H - 590);
  ctx.fillStyle = '#7b5d40'; for (let y = 596; y < H; y += 14) ctx.fillRect(0, y, W, 2);
  ctx.fillStyle = '#5c4636'; ctx.fillRect(0, 586, W, 6);
  ctx.fillStyle = '#3d6b5a'; ctx.fillRect(470, 640, 180, 90); ctx.fillStyle = '#4f8a74'; ctx.fillRect(480, 650, 160, 70);
  ctx.fillStyle = '#2b2f36'; ctx.fillRect(530, 600, 60, 34); ctx.fillStyle = '#c0392b'; ctx.fillRect(540, 606, 10, 6);
  ctx.fillStyle = '#e8e2d6'; ctx.fillRect(556, 620, 10, 10);
  rotulo('COPA', 538, 662, '#e8f5ee');
  [[455, 612], [668, 612]].forEach(([x, y]) => { ctx.fillStyle = '#6b4226'; ctx.fillRect(x, y + 18, 18, 16); ctx.fillStyle = '#2e8b57'; ctx.fillRect(x - 6, y, 30, 20); ctx.fillStyle = '#3cb371'; ctx.fillRect(x - 2, y - 6, 22, 10); });
}

function rotulo(t, x, y, cor) { ctx.font = "8px 'Press Start 2P', monospace"; ctx.fillStyle = cor; ctx.fillText(t, x, y); }

function racks(tempo) {
  for (let i = 0; i < 3; i++) {
    const x = 750 + i * 52, y = 84;
    ctx.fillStyle = '#11161c'; ctx.fillRect(x, y, 40, 128);
    ctx.fillStyle = '#1e2731'; ctx.fillRect(x + 3, y + 3, 34, 122);
    for (let r = 0; r < 10; r++) {
      ctx.fillStyle = '#28323e'; ctx.fillRect(x + 6, y + 8 + r * 12, 28, 8);
      const on = (Math.sin(tempo / 120 + r * 1.7 + i * 3) > (piscaRack > 0 ? -0.6 : 0.4));
      ctx.fillStyle = on ? (r % 3 ? '#3ddc84' : '#58a6ff') : '#0f2a1a';
      ctx.fillRect(x + 28, y + 10 + r * 12, 3, 3);
    }
  }
  // estantes do arquivo
  for (let i = 0; i < 2; i++) {
    const x = 965 + i * 64, y = 86;
    ctx.fillStyle = '#5a3d24'; ctx.fillRect(x, y, 56, 124);
    for (let r = 0; r < 4; r++) {
      ctx.fillStyle = '#3e2a19'; ctx.fillRect(x + 3, y + 6 + r * 30, 50, 24);
      for (let b = 0; b < 7; b++) { ctx.fillStyle = ['#c0392b', '#2980b9', '#27ae60', '#f39c12', '#8e44ad'][(b + r + i) % 5]; ctx.fillRect(x + 5 + b * 7, y + 10 + r * 30, 5, 20); }
    }
  }
}

function mesa(p) {
  const s = LUGARES[p.id].sentado, x = s.x - 76, y = s.y - 12;
  ctx.fillStyle = '#7a5434'; ctx.fillRect(x, y + 34, 152, 10);       // frente
  ctx.fillStyle = '#b8875a'; ctx.fillRect(x, y, 152, 36);            // tampo
  ctx.fillStyle = '#a17447'; ctx.fillRect(x, y + 30, 152, 4);
  // monitor
  const mx = x + 10, my = y - 22;
  ctx.fillStyle = '#20262e'; ctx.fillRect(mx, my, 40, 28); ctx.fillRect(mx + 16, my + 28, 8, 6);
  const tela = p.digitando > 0 ? '#1f6f50' : '#183a5a';
  ctx.fillStyle = tela; ctx.fillRect(mx + 3, my + 3, 34, 20);
  ctx.fillStyle = '#7ee2b8';
  for (let l = 0; l < 4; l++) ctx.fillRect(mx + 6, my + 6 + l * 4, ((l * 7 + Math.floor(performance.now() / (p.digitando > 0 ? 90 : 900))) % 20) + 6, 2);
  // papéis e caneca
  ctx.fillStyle = '#eef1f4'; ctx.fillRect(x + 104, y + 8, 20, 14); ctx.fillStyle = '#c9ced4'; ctx.fillRect(x + 107, y + 11, 14, 2);
  ctx.fillStyle = '#d35400'; ctx.fillRect(x + 132, y + 10, 8, 9);
  if (p.ferramenta && performance.now() < p.ferramentaAte) {
    ctx.font = '10px "JetBrains Mono", monospace';
    const t = p.ferramenta.texto, w = ctx.measureText(t).width + 10;
    ctx.fillStyle = p.ferramenta.ok ? 'rgba(18,60,38,.92)' : 'rgba(110,25,25,.92)';
    ctx.fillRect(x + 76 - w / 2, y + 48, w, 15);
    ctx.fillStyle = p.ferramenta.ok ? '#9ef0c5' : '#ffd0d0'; ctx.fillText(t, x + 81 - w / 2, y + 59);
  }
}

function boneco(p) {
  const P = 3, x0 = Math.round(p.x) - 15, y0 = Math.round(p.y) - 48, v = p.v;
  const px = (cx, cy, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(x0 + cx * P, y0 + cy * P, w * P, h * P); };
  ctx.fillStyle = 'rgba(0,0,0,.22)'; ctx.beginPath(); ctx.ellipse(p.x, p.y + 1, 14, 4, 0, 0, Math.PI * 2); ctx.fill();
  const costas = p.dir === 'cima' && !p.sentado;
  // pernas
  if (!p.sentado) {
    const f = Math.floor(p.passo) % 2, andando = p.atual && (p.atual.tipo === 'andar' || p.atual.tipo === 'sentar');
    px(2, 11, 2, andando && f ? 3 : 4, v.calca); px(6, 11, 2, andando && !f ? 3 : 4, v.calca);
    px(2, andando && f ? 14 : 15, 2, 1, '#111'); px(6, andando && !f ? 14 : 15, 2, 1, '#111');
  }
  // corpo e braços
  px(1, 6, 8, 5, v.camisa); px(1, 6, 8, 1, shade(v.camisa));
  const dig = p.digitando > 0 && Math.floor(performance.now() / 120) % 2;
  px(0, 6, 1, 4, v.camisa); px(9, 6, 1, 4, v.camisa);
  px(0, dig ? 9 : 10, 1, 1, v.pele); px(9, dig ? 10 : 9, 1, 1, v.pele);
  if (v.gravata && !costas) px(4, 6, 2, 4, v.gravata);
  // cabeça
  px(2, 2, 6, 4, v.pele);
  px(2, 0, 6, 2, v.cabelo); px(1, 1, 1, 3, v.cabelo); px(8, 1, 1, 3, v.cabelo);
  if (costas) px(2, 2, 6, 3, v.cabelo);
  else { px(3, 3, 1, 1, '#1b1b1b'); px(6, 3, 1, 1, '#1b1b1b'); px(4, 5, 2, 1, shade(v.pele)); }
}

function shade(hex) {
  const n = parseInt(hex.slice(1), 16), r = (n >> 16) * 0.75, g = ((n >> 8) & 255) * 0.75, b = (n & 255) * 0.75;
  return `rgb(${r | 0},${g | 0},${b | 0})`;
}

function etiquetas(p) {
  const y = p.y - 58;
  ctx.font = "7px 'Press Start 2P', monospace";
  const nome = p.v.nome, wn = ctx.measureText(nome).width + 8;
  ctx.fillStyle = 'rgba(10,14,20,.85)'; ctx.fillRect(p.x - wn / 2, y - 10, wn, 12);
  ctx.fillStyle = '#e6edf3'; ctx.fillText(nome, p.x - wn / 2 + 4, y - 1);
  const st = p.status, ws = ctx.measureText(st).width + 8, cor = STATUS_COR[st] || '#5c6b7a';
  ctx.fillStyle = cor; ctx.fillRect(p.x - ws / 2, y - 24, ws, 12);
  ctx.fillStyle = '#0b0f14'; ctx.fillText(st, p.x - ws / 2 + 4, y - 15);
}

function quebrar(texto, max) {
  const palavras = String(texto).split(/\s+/), linhas = [];
  let l = '';
  for (const w of palavras) { if ((l + ' ' + w).trim().length > max) { if (l) linhas.push(l); l = w; } else l = (l + ' ' + w).trim(); }
  if (l) linhas.push(l);
  if (linhas.length > 4) { linhas.length = 4; linhas[3] = linhas[3].slice(0, max - 1) + '…'; }
  return linhas;
}

function bolha(p) {
  if (!p.bolha) return;
  const linhas = quebrar(p.bolha.texto, 30);
  ctx.font = '11px "JetBrains Mono", monospace';
  const w = Math.max(...linhas.map((l) => ctx.measureText(l).width)) + 16, h = linhas.length * 14 + 10;
  let x = Math.min(Math.max(p.x - w / 2, 4), W - w - 4), y = p.y - 92 - h;
  if (y < 44) y = 44;
  const pensa = p.bolha.tipo === 'pensar', alerta = p.bolha.tipo === 'alerta';
  ctx.fillStyle = alerta ? '#fff1f0' : pensa ? '#f3eeff' : '#ffffff';
  ctx.strokeStyle = alerta ? '#e5534b' : pensa ? '#8957e5' : '#1b2430'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.roundRect(x, y, w, h, 6); ctx.fill(); ctx.stroke();
  if (pensa) { [[0, 8, 4], [-6, 16, 3]].forEach(([dx, dy, r]) => { ctx.beginPath(); ctx.arc(p.x + dx, y + h + dy - 2, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); }); }
  else { ctx.beginPath(); ctx.moveTo(p.x - 6, y + h - 1); ctx.lineTo(p.x, y + h + 9); ctx.lineTo(p.x + 6, y + h - 1); ctx.fill(); ctx.beginPath(); ctx.moveTo(p.x - 6, y + h); ctx.lineTo(p.x, y + h + 9); ctx.lineTo(p.x + 6, y + h); ctx.stroke(); }
  ctx.fillStyle = alerta ? '#a40e26' : '#141a22';
  linhas.forEach((l, i) => ctx.fillText(l, x + 8, y + 17 + i * 14));
}

function desenharPacotes(agora) {
  for (let i = pacotes.length - 1; i >= 0; i--) {
    const k = pacotes[i], t = Math.min(1, (agora - k.t0) / k.ms);
    const x = k.x0 + (k.x1 - k.x0) * t, y = k.y0 + (k.y1 - k.y0) * t - Math.sin(t * Math.PI) * 40;
    ctx.fillStyle = k.cor; ctx.fillRect(x - 6, y - 4, 12, 8);
    ctx.strokeStyle = '#0b0f14'; ctx.lineWidth = 1; ctx.strokeRect(x - 6, y - 4, 12, 8);
    ctx.beginPath(); ctx.moveTo(x - 6, y - 4); ctx.lineTo(x, y + 1); ctx.lineTo(x + 6, y - 4); ctx.stroke();
    if (t >= 1) { pacotes.splice(i, 1); piscaRack = 400; if (k.depois) pacote(k.depois); }
  }
}

function pacote(k) { pacotes.push({ ...k, t0: performance.now(), ms: (k.ms || 700) / Math.min(4, velocidadeBase) }); }

let ultimo = performance.now();
function quadro(agora) {
  const dt = Math.min(0.1, (agora - ultimo) / 1000); ultimo = agora;
  piscaRack = Math.max(0, piscaRack - dt * 1000);
  Object.values(pessoas).forEach((p) => { atualizar(p, dt); p.digitando = Math.max(0, p.digitando - dt); });
  chao(); racks(agora);
  // mesas e pessoas, de cima para baixo
  const cena = [];
  Object.values(pessoas).forEach((p) => {
    const s = LUGARES[p.id].sentado;
    cena.push({ y: s.y + 20, f: () => mesa(p) });
    cena.push({ y: p.sentado ? s.y + 10 : p.y + 30, f: () => boneco(p) });
  });
  cena.sort((a, b) => a.y - b.y).forEach((c) => c.f());
  Object.values(pessoas).forEach(etiquetas);
  desenharPacotes(agora);
  Object.values(pessoas).forEach(bolha);
  requestAnimationFrame(quadro);
}
requestAnimationFrame(quadro);

