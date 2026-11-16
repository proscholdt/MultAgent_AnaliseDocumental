// A sala no estilo Caverna do Dragão: o mesmo escritório e os mesmos eventos, redesenhados como
// uma caverna. O orquestrador é o Mestre dos Magos, os especialistas são a turma, os portões do
// MCP são guardados pela Tiamat (que cospe fogo em quem é barrado) e o Vingador aparece quando a
// execução falha. Carregado depois de cena.js, conversa.js e eventos.js: as funções declaradas
// aqui substituem as de lá (scripts clássicos dividem o escopo global).

// ============================================================================================
// Elenco
// ============================================================================================
Object.assign(VISUAL.usuario, { nome: 'BOBBY (VOCÊ)' });
Object.assign(VISUAL.orquestrador, { nome: 'MESTRE DOS MAGOS' });
Object.assign(VISUAL.extrator, { nome: 'HANK · EXTRATOR' });
Object.assign(VISUAL.validador, { nome: 'ERIC · VALIDADOR' });
Object.assign(VISUAL.analista, { nome: 'PRESTO · ANALISTA' });
Object.assign(VISUAL.pesquisador, { nome: 'SHEILA · PESQUISADORA' });
Object.assign(VISUAL.redator, { nome: 'DIANA · REDATORA' });
Object.assign(STATUS_COR, { LIVRE: '#8c7a5b', TRABALHANDO: '#5fd068', ESPERANDO: '#e8b13a', 'REUNIÃO': '#5aa9e6', ANDANDO: '#a89a80', PENSANDO: '#b48cf2', 'NO PORTÃO': '#e5533b' });

const HEROIS = {
  usuario:     { pele: '#f1c27d', cabelo: '#c0461b', top: '#7a4b2a', top2: '#5c3720', calca: '#4a3020', extra: 'barbaro' },
  extrator:    { pele: '#f1c27d', cabelo: '#e2b04a', top: '#4f7a3a', top2: '#3b5c2b', calca: '#6b4e2e', extra: 'arco' },
  validador:   { pele: '#f6cfa4', cabelo: '#d8641e', top: '#5b78b5', top2: '#c0c7d0', calca: '#2d3b5c', capa: '#b3262b', extra: 'escudo' },
  analista:    { pele: '#f1c27d', cabelo: '#6b3e1e', top: '#2f7d4a', top2: '#245f38', calca: '#245f38', extra: 'mago' },
  pesquisador: { pele: '#f6cfa4', cabelo: '#d2391b', top: '#7a3f8f', top2: '#5c2d6b', calca: '#4a2a5a', capa: '#6b3480', extra: 'capuz' },
  redator:     { pele: '#8d5524', cabelo: '#1f140c', top: '#e08a2e', top2: '#b8681c', calca: '#c87422', extra: 'bastao' },
};

let tc = 0;
let semente = 7;
const rnd = () => (semente = (semente * 16807) % 2147483647) / 2147483647;

// ============================================================================================
// O fundo parado (pintado uma vez)
// ============================================================================================
const PONTES = [282, 650, 830, ...XS.map((x) => x - 98)]; // onde as faixas de andar cruzam a lava
const fundo = document.createElement('canvas');
fundo.width = W; fundo.height = H;
(function pintarFundo() {
  const c = fundo.getContext('2d');
  c.fillStyle = '#211b17'; c.fillRect(0, 0, W, H);
  // lajes de pedra
  for (let y = 40; y < H; y += 26) {
    let x = -rnd() * 30;
    while (x < W) {
      const w = 30 + rnd() * 34, v = (58 + rnd() * 26) | 0;
      c.fillStyle = `rgb(${v},${v - 6},${v - 14})`; c.fillRect(x + 1, y + 1, w - 2, 24);
      c.fillStyle = 'rgba(255,255,255,.05)'; c.fillRect(x + 1, y + 1, w - 2, 2);
      c.fillStyle = 'rgba(0,0,0,.18)'; c.fillRect(x + 1, y + 22, w - 2, 3);
      x += w;
    }
  }
  // parede da caverna
  c.fillStyle = '#16110e'; c.fillRect(0, 0, W, 44);
  for (let x = -10; x < W; x += 16) {
    const h = 30 + rnd() * 20, g = (40 + rnd() * 22) | 0;
    c.fillStyle = `rgb(${g + 8},${g},${g - 6})`;
    c.beginPath(); c.moveTo(x - 4, 0); c.lineTo(x + 22, 0); c.lineTo(x + 12 + rnd() * 6, h); c.lineTo(x + 2, h - 8); c.fill();
  }
  // acampamento: grama, barraca
  c.fillStyle = '#2b3a1b'; c.fillRect(20, 52, 236, 248);
  for (let i = 0; i < 260; i++) { c.fillStyle = rnd() > 0.5 ? '#3d5226' : '#24321a'; c.fillRect(22 + rnd() * 230, 54 + rnd() * 242, 2, 4); }
  c.fillStyle = '#8a6b3d'; c.beginPath(); c.moveTo(26, 112); c.lineTo(62, 58); c.lineTo(98, 112); c.fill();
  c.fillStyle = '#6b522e'; c.beginPath(); c.moveTo(62, 58); c.lineTo(98, 112); c.lineTo(74, 112); c.fill();
  c.fillStyle = '#2a1d10'; c.beginPath(); c.moveTo(54, 112); c.lineTo(62, 84); c.lineTo(70, 112); c.fill();
  // pedra do mestre: um círculo de menires
  [[462, 66, 22, 52], [498, 54, 24, 64], [536, 48, 26, 70], [576, 54, 24, 64], [612, 66, 22, 52]].forEach(([x, y, w, h]) => {
    c.fillStyle = '#5d5a5f'; c.fillRect(x, y, w, h);
    c.fillStyle = '#77737a'; c.fillRect(x, y, w, 4); c.fillRect(x, y, 3, h);
    c.fillStyle = '#3c3a3f'; c.fillRect(x + w - 3, y, 3, h);
  });
  // covil da Tiamat: chão escuro e o tesouro
  c.fillStyle = '#17110f'; c.fillRect(720, 52, 384, 196);
  for (let i = 0; i < 420; i++) {
    const x = 735 + rnd() * 215, y = 214 + rnd() * 30 - Math.max(0, 20 - Math.abs(x - 840) / 6) * rnd();
    c.fillStyle = ['#d4a017', '#f2c94c', '#b8860b'][(rnd() * 3) | 0]; c.fillRect(x, y, 3, 2);
  }
  c.fillStyle = '#6b4423'; c.fillRect(1040, 214, 46, 28); c.fillStyle = '#d4a017'; c.fillRect(1040, 224, 46, 3); c.fillRect(1060, 222, 6, 8);
  c.strokeStyle = '#8a6b3d'; c.lineWidth = 4; c.strokeRect(720, 52, 384, 196);
  // ponte de pedra no corredor
  c.fillStyle = '#4a4038'; c.fillRect(0, CORREDOR - 22, W, 44);
  for (let x = 0; x < W; x += 34) { c.fillStyle = '#3a322b'; c.fillRect(x, CORREDOR - 22, 2, 44); }
  c.fillStyle = '#5a4f45'; c.fillRect(0, CORREDOR - 22, W, 3); c.fillRect(0, CORREDOR + 19, W, 3);
  PONTES.forEach((x) => { c.fillStyle = '#4a4038'; c.fillRect(x - 18, CORREDOR - 42, 36, 84); c.fillStyle = '#3a322b'; c.fillRect(x - 18, CORREDOR - 42, 2, 84); c.fillRect(x + 16, CORREDOR - 42, 2, 84); });
  // taverna: tábuas, lareira, barris, mesa
  c.fillStyle = '#3e2716'; c.fillRect(0, 590, W, H - 590);
  for (let y = 596; y < H; y += 12) { c.fillStyle = '#2e1c10'; c.fillRect(0, y, W, 2); }
  c.fillStyle = '#5a5050'; c.fillRect(526, 594, 68, 50); c.fillStyle = '#120c08'; c.fillRect(538, 606, 44, 38);
  [[478, 660], [646, 660]].forEach(([x, y]) => {
    c.fillStyle = '#6b4423'; c.fillRect(x - 14, y - 18, 28, 36);
    c.fillStyle = '#3a2414'; c.fillRect(x - 14, y - 10, 28, 3); c.fillRect(x - 14, y + 8, 28, 3);
  });
  c.fillStyle = '#5c3a1e'; c.beginPath(); c.ellipse(562, 704, 46, 18, 0, 0, Math.PI * 2); c.fill();
  [[540, 698], [570, 694], [588, 706]].forEach(([x, y]) => { c.fillStyle = '#c9b28a'; c.fillRect(x, y - 8, 8, 10); c.fillStyle = '#f5f0e0'; c.fillRect(x, y - 10, 8, 3); });
})();

// ============================================================================================
// Cenário
// ============================================================================================
const sombra = document.createElement('canvas');
sombra.width = W; sombra.height = H;
const sctx = sombra.getContext('2d');
const TOCHAS = [[300, 30], [460, 30], [640, 30], [706, 64], [1108, 36]];
const FAIXAS_LAVA = [CORREDOR - 40, CORREDOR + 22]; // y de cima das duas faixas de lava (18 px cada)

function luz(x, y, r, a) {
  const g = sctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, `rgba(0,0,0,${a})`); g.addColorStop(1, 'rgba(0,0,0,0)');
  sctx.fillStyle = g; sctx.fillRect(x - r, y - r, r * 2, r * 2);
}

function fogueira(x, y, t, tam = 1) {
  ctx.fillStyle = '#4a2c14'; ctx.fillRect(x - 12 * tam, y + 4, 24 * tam, 4); ctx.fillRect(x - 8 * tam, y + 1, 16 * tam, 4);
  for (let i = 0; i < 7; i++) {
    const h = (10 + 8 * Math.abs(Math.sin(t * 7 + i * 1.9))) * tam, dx = (i - 3) * 3 * tam;
    ctx.fillStyle = i % 2 ? '#ffb02e' : '#ff6a1a'; ctx.fillRect(x + dx - 2, y + 2 - h, 4, h);
    ctx.fillStyle = '#ffe9a0'; ctx.fillRect(x + dx - 1, y + 2 - h * 0.5, 2, h * 0.5);
  }
}

function chao() {
  const agora = performance.now();
  tc = agora / 1000;
  const t = tc;
  ctx.drawImage(fundo, 0, 0);
  // lava
  for (const y of FAIXAS_LAVA) {
    for (let x = 0; x < W; x += 6) {
      if (PONTES.some((px) => Math.abs(px - x) < 21)) continue;
      const v = Math.sin(x * 0.05 + t * 2) + Math.sin(x * 0.013 - t * 1.3);
      ctx.fillStyle = `hsl(${18 + v * 8},100%,${46 + v * 8}%)`; ctx.fillRect(x, y, 6, 18);
    }
    for (let i = 0; i < 6; i++) { // bolhas
      const bx = (i * 197 + t * 30) % W;
      if (PONTES.some((px) => Math.abs(px - bx) < 24)) continue;
      ctx.fillStyle = '#ffe07a'; ctx.beginPath(); ctx.arc(bx, y + 9 + Math.sin(t * 3 + i) * 3, 2 + Math.abs(Math.sin(t * 2 + i)) * 2, 0, Math.PI * 2); ctx.fill();
    }
  }
  // runas dos menires
  [[473, 92], [510, 84], [549, 80], [588, 84], [623, 92]].forEach(([x, y], i) => {
    const a = 0.5 + 0.5 * Math.sin(t * 1.5 + i * 1.3);
    ctx.fillStyle = `rgba(110,200,255,${0.35 + 0.6 * a})`; ctx.fillRect(x - 3, y, 6, 2); ctx.fillRect(x - 1, y - 6, 2, 14); ctx.fillRect(x - 4, y + 8, 8, 2);
  });
  fogueira(150, 252, t);
  fogueira(560, 640, t + 1, 1.2);
  uni(62, 262, t);
  // escuridão da caverna, com as luzes recortadas
  sctx.globalCompositeOperation = 'source-over'; sctx.clearRect(0, 0, W, H);
  sctx.fillStyle = 'rgba(6,3,12,.6)'; sctx.fillRect(0, 0, W, H);
  sctx.globalCompositeOperation = 'destination-out';
  const tremor = (i) => 1 + 0.06 * Math.sin(t * 9 + i * 2) + 0.04 * Math.sin(t * 23 + i);
  TOCHAS.forEach(([x, y], i) => luz(x, y + 10, 120 * tremor(i), 0.9));
  luz(150, 245, 140 * tremor(7), 1); luz(560, 630, 170 * tremor(8), 1); luz(545, 95, 130, 0.7);
  FAIXAS_LAVA.forEach((y) => { const g = sctx.createLinearGradient(0, y - 50, 0, y + 68); g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(0.5, 'rgba(0,0,0,.85)'); g.addColorStop(1, 'rgba(0,0,0,0)'); sctx.fillStyle = g; sctx.fillRect(0, y - 50, W, 118); });
  Object.values(pessoas).forEach((p) => { const s = LUGARES[p.id].sentado; luz(s.x + 62, s.y - 8, 90 * tremor(s.x), 0.85); luz(p.x, p.y - 20, 60, 0.5); });
  pacotes.forEach((k) => { const u = Math.min(1, (agora - k.t0) / k.ms); luz(k.x0 + (k.x1 - k.x0) * u, k.y0 + (k.y1 - k.y0) * u - Math.sin(u * Math.PI) * 40, 50, 0.7); });
  if (fogo) luz((CABECAS[2].hx + fogo.alvo.x) / 2, (CABECAS[2].hy + fogo.alvo.y) / 2, 260, 1);
  ctx.drawImage(sombra, 0, 0);
  // tochas na parede
  TOCHAS.forEach(([x, y], i) => {
    ctx.fillStyle = '#4a2c14'; ctx.fillRect(x - 2, y, 4, 14);
    const h = 9 + 4 * Math.abs(Math.sin(t * 8 + i * 3));
    ctx.fillStyle = '#ff7a1a'; ctx.fillRect(x - 4, y - h, 8, h); ctx.fillStyle = '#ffe08a'; ctx.fillRect(x - 2, y - h * 0.6, 4, h * 0.6);
  });
  dragaoVoando(t);
  rotulo('ACAMPAMENTO', 34, 292); rotulo('PEDRA DO MESTRE', 490, 262);
  rotulo('COVIL DA TIAMAT · MCP · PORTÕES', 732, 72); rotulo('BIBLIOTECA · ÍNDICE', 968, 72);
  rotulo('TAVERNA', 532, 762);
}

function rotulo(texto, x, y) {
  ctx.save(); ctx.font = "13px 'MedievalSharp', Georgia, serif";
  ctx.fillStyle = '#000'; ctx.fillText(texto, x + 1, y + 1);
  ctx.fillStyle = '#f2c94c'; ctx.fillText(texto, x, y); ctx.restore();
}

function uni(x, y, t) {
  const P = 2, b = Math.round(Math.sin(t * 2.5) * 1);
  const px = (cx, cy, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(x + cx * P, y + (cy + b) * P, w * P, h * P); };
  px(2, 4, 9, 5, '#f4f1ff'); px(9, 1, 4, 4, '#f4f1ff'); px(12, 3, 2, 2, '#f4f1ff');           // corpo e cabeça
  px(3, 9, 1, 3, '#e6e2f5'); px(5, 9, 1, 3, '#e6e2f5'); px(8, 9, 1, 3, '#e6e2f5'); px(10, 9, 1, 3, '#e6e2f5');
  px(11, -2, 1, 3, '#f2c94c'); px(12, -3, 1, 1, '#f2c94c');                                 // chifre
  px(8, 0, 2, 5, '#b48cf2'); px(0, 4, 2, 4, '#b48cf2');                                     // crina e rabo
  px(11, 2, 1, 1, '#1b1b1b');
}

const CABECAS = [
  { cor: '#e9e9f2', x: 772, y: 120 }, { cor: '#2c2c36', x: 804, y: 96 }, { cor: '#d63b3b', x: 840, y: 82 },
  { cor: '#3fa34d', x: 876, y: 96 }, { cor: '#3b6fd6', x: 908, y: 120 },
];
CABECAS.forEach((c) => { c.hx = c.x; c.hy = c.y; });

function racks() {
  const t = tc, bx = 840, by = 200, asa = Math.sin(t * 1.2) * 6;
  // asas
  ctx.fillStyle = '#3d1822';
  ctx.beginPath(); ctx.moveTo(bx - 20, by - 14); ctx.lineTo(bx - 112, by - 78 - asa); ctx.lineTo(bx - 96, by - 30); ctx.lineTo(bx - 78, by + 8); ctx.fill();
  ctx.beginPath(); ctx.moveTo(bx + 20, by - 14); ctx.lineTo(bx + 112, by - 78 - asa); ctx.lineTo(bx + 96, by - 30); ctx.lineTo(bx + 78, by + 8); ctx.fill();
  // pescoços
  CABECAS.forEach((c, i) => {
    c.hx = c.x + Math.sin(t * 1.3 + i) * 5; c.hy = c.y + Math.cos(t * 1.1 + i * 1.7) * 4;
    const sx = bx + (i - 2) * 14, sy = by - 12, qx = bx + (i - 2) * 34, qy = by - 60;
    ctx.lineCap = 'round';
    ctx.strokeStyle = '#0c0808'; ctx.lineWidth = 14; ctx.beginPath(); ctx.moveTo(sx, sy); ctx.quadraticCurveTo(qx, qy, c.hx, c.hy + 6); ctx.stroke();
    ctx.strokeStyle = c.cor; ctx.lineWidth = 10; ctx.beginPath(); ctx.moveTo(sx, sy); ctx.quadraticCurveTo(qx, qy, c.hx, c.hy + 6); ctx.stroke();
  });
  // corpo e rabo
  ctx.strokeStyle = '#4a1f2a'; ctx.lineWidth = 9; ctx.beginPath(); ctx.moveTo(bx + 40, by + 10); ctx.quadraticCurveTo(bx + 100, by + 40 + Math.sin(t) * 6, bx + 120, by + 6); ctx.stroke();
  ctx.fillStyle = '#4a1f2a'; ctx.beginPath(); ctx.ellipse(bx, by, 54, 26, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#c9a46a'; for (let i = -3; i <= 3; i++) ctx.fillRect(bx + i * 10 - 3, by + 4, 6, 14);
  ctx.lineCap = 'butt';
  // cabeças
  const atenta = piscaRack > 0;
  CABECAS.forEach((c) => {
    const x = c.hx, y = c.hy;
    ctx.fillStyle = '#d8d0c0'; ctx.fillRect(x - 10, y - 12, 3, 6); ctx.fillRect(x + 7, y - 12, 3, 6);
    ctx.fillStyle = c.cor; ctx.fillRect(x - 10, y - 7, 20, 12); ctx.fillRect(x - 6, y + 4, 12, 7);
    ctx.strokeStyle = '#0c0808'; ctx.lineWidth = 1; ctx.strokeRect(x - 10, y - 7, 20, 12); ctx.strokeRect(x - 6, y + 4, 12, 7);
    ctx.fillStyle = atenta ? '#fff36b' : '#ffb000'; ctx.fillRect(x - 7, y - 3, 4, 3); ctx.fillRect(x + 3, y - 3, 4, 3);
    ctx.fillStyle = '#0c0808'; ctx.fillRect(x - 4, y + 9, 2, 1); ctx.fillRect(x + 2, y + 9, 2, 1);
  });
  // biblioteca
  for (let i = 0; i < 2; i++) {
    const x = 965 + i * 64, y = 86;
    ctx.fillStyle = '#3e2716'; ctx.fillRect(x, y, 56, 124);
    for (let r = 0; r < 4; r++) {
      ctx.fillStyle = '#24160c'; ctx.fillRect(x + 3, y + 6 + r * 30, 50, 24);
      for (let b = 0; b < 7; b++) {
        ctx.fillStyle = ['#7a1f1f', '#2f4f6f', '#3f5f2a', '#8a6b3d', '#5c2d6b'][(b + r + i) % 5];
        if ((b + r * 2 + i) % 4 === 0) { ctx.fillStyle = '#e8d9b0'; ctx.fillRect(x + 5 + b * 7, y + 20 + r * 30, 6, 8); }
        else ctx.fillRect(x + 5 + b * 7, y + 10 + r * 30, 5, 20);
      }
    }
  }
}

function mesa(p) {
  const s = LUGARES[p.id].sentado, x = s.x - 76, y = s.y - 12, t = tc;
  if (p.id === 'orquestrador') {
    ctx.fillStyle = '#56535a'; ctx.fillRect(x + 20, y, 112, 40); ctx.fillStyle = '#6e6a72'; ctx.fillRect(x + 20, y, 112, 6);
    ctx.fillStyle = '#3c3a3f'; ctx.fillRect(x + 20, y + 34, 112, 6);
    const a = 0.5 + 0.5 * Math.sin(t * 2);
    ctx.fillStyle = `rgba(110,200,255,${0.4 + 0.5 * a})`; for (let i = 0; i < 6; i++) ctx.fillRect(x + 32 + i * 16, y + 18, 8, 3);
  } else {
    ctx.fillStyle = '#4a2c14'; ctx.fillRect(x + 6, y + 34, 6, 12); ctx.fillRect(x + 140, y + 34, 6, 12);
    ctx.fillStyle = '#7a4f2a'; ctx.fillRect(x, y, 152, 36);
    ctx.fillStyle = '#6b4423'; for (let i = 1; i < 4; i++) ctx.fillRect(x, y + i * 9, 152, 1);
    ctx.fillStyle = '#5c3a1e'; ctx.fillRect(x, y + 32, 152, 4);
  }
  // livro de feitiços (o monitor): as runas brilham quando o herói digita
  const mx = x + 10, my = y - 14, dig = p.digitando > 0;
  ctx.fillStyle = '#5c2d1e'; ctx.fillRect(mx - 2, my - 2, 44, 24);
  ctx.fillStyle = '#efe3c2'; ctx.fillRect(mx, my, 19, 20); ctx.fillRect(mx + 21, my, 19, 20);
  ctx.fillStyle = '#c9b28a'; ctx.fillRect(mx + 19, my, 2, 20);
  for (let l = 0; l < 4; l++) {
    const w = ((l * 7 + Math.floor(t * (dig ? 10 : 1))) % 10) + 5;
    ctx.fillStyle = dig ? `rgba(90,190,255,${0.7 + 0.3 * Math.sin(t * 9 + l)})` : '#6b5a44';
    ctx.fillRect(mx + 3, my + 4 + l * 4, Math.min(14, w), 2); ctx.fillRect(mx + 24, my + 4 + l * 4, Math.min(14, 18 - w), 2);
  }
  if (dig) { ctx.fillStyle = 'rgba(90,190,255,.25)'; ctx.fillRect(mx - 4, my - 6, 48, 30); }
  // pergaminho e vela
  ctx.fillStyle = '#e8d9b0'; ctx.fillRect(x + 100, y + 10, 22, 12); ctx.fillStyle = '#c9b28a'; ctx.fillRect(x + 98, y + 9, 4, 14); ctx.fillRect(x + 120, y + 9, 4, 14);
  ctx.fillStyle = '#efe6cc'; ctx.fillRect(x + 134, y + 4, 6, 12);
  const h = 6 + 2 * Math.abs(Math.sin(t * 11 + s.x));
  ctx.fillStyle = '#ff9a2e'; ctx.fillRect(x + 135, y + 4 - h, 4, h); ctx.fillStyle = '#fff2b0'; ctx.fillRect(x + 136, y + 4 - h * 0.5, 2, h * 0.5);
  if (p.ferramenta && performance.now() < p.ferramentaAte) {
    ctx.font = '10px "JetBrains Mono", monospace';
    const txt = p.ferramenta.texto, w = ctx.measureText(txt).width + 12;
    ctx.fillStyle = p.ferramenta.ok ? '#e8d9b0' : '#f2b8a8'; ctx.fillRect(x + 76 - w / 2, y + 48, w, 15);
    ctx.strokeStyle = p.ferramenta.ok ? '#6b4423' : '#a01b10'; ctx.lineWidth = 1; ctx.strokeRect(x + 76 - w / 2, y + 48, w, 15);
    ctx.fillStyle = p.ferramenta.ok ? '#3a2414' : '#7a0f08'; ctx.fillText(txt, x + 82 - w / 2, y + 59);
  }
}

// ============================================================================================
// Pessoas
// ============================================================================================
function boneco(p) {
  if (p.id === 'orquestrador') return mestreMagos(p);
  const h = HEROIS[p.id], P = 3, x0 = Math.round(p.x) - 15, y0 = Math.round(p.y) - 48;
  const px = (cx, cy, w, hh, c) => { ctx.fillStyle = c; ctx.fillRect(x0 + cx * P, y0 + cy * P, w * P, hh * P); };
  ctx.fillStyle = 'rgba(0,0,0,.3)'; ctx.beginPath(); ctx.ellipse(p.x, p.y + 1, 14, 4, 0, 0, Math.PI * 2); ctx.fill();
  const costas = p.dir === 'cima' && !p.sentado;
  if (h.capa) { if (costas) px(0, 6, 10, 8, h.capa); else { px(0, 6, 1, 7, h.capa); px(9, 6, 1, 7, h.capa); } }
  if (h.extra === 'bastao') { px(10, 0, 1, 16, '#c58b3c'); px(10, 0, 1, 1, '#ffd27a'); }
  if (!p.sentado) {
    const f = Math.floor(p.passo) % 2, andando = p.atual && (p.atual.tipo === 'andar' || p.atual.tipo === 'sentar');
    px(2, 11, 2, andando && f ? 3 : 4, h.calca); px(6, 11, 2, andando && !f ? 3 : 4, h.calca);
    px(2, andando && f ? 14 : 15, 2, 1, '#2a1a0e'); px(6, andando && !f ? 14 : 15, 2, 1, '#2a1a0e');
  }
  if (!costas || !h.capa) { px(1, 6, 8, 5, h.top); px(1, 6, 8, 1, h.top2); px(1, 9, 8, 1, '#3a2414'); }
  if (h.extra === 'barbaro') { px(1, 6, 2, 4, '#a07a50'); px(7, 6, 2, 4, '#a07a50'); }
  const dig = p.digitando > 0 && Math.floor(performance.now() / 120) % 2;
  px(0, 6, 1, 4, h.top); px(9, 6, 1, 4, h.top);
  px(0, dig ? 9 : 10, 1, 1, h.pele); px(9, dig ? 10 : 9, 1, 1, h.pele);
  px(2, 2, 6, 4, h.pele);
  px(2, 0, 6, 2, h.cabelo); px(1, 1, 1, 3, h.cabelo); px(8, 1, 1, 3, h.cabelo);
  if (costas) px(2, 2, 6, 3, h.cabelo);
  else { px(3, 3, 1, 1, '#1b1b1b'); px(6, 3, 1, 1, '#1b1b1b'); px(4, 5, 2, 1, shade(h.pele)); }
  switch (h.extra) {
    case 'barbaro':
      px(2, 0, 6, 1, '#8d8d99'); px(1, -1, 1, 2, '#eeeae0'); px(0, -2, 1, 1, '#eeeae0'); px(8, -1, 1, 2, '#eeeae0'); px(9, -2, 1, 1, '#eeeae0');
      px(10, 7, 1, 5, '#6b4423'); px(10, 4, 2, 3, '#7a4b2a');
      break;
    case 'arco': {
      const g = `hsl(38,100%,${58 + 12 * Math.sin(tc * 4)}%)`;
      ctx.save(); ctx.shadowColor = '#ffb347'; ctx.shadowBlur = 10;
      px(10, 3, 1, 1, g); px(11, 4, 1, 8, g); px(10, 12, 1, 1, g); ctx.restore();
      px(10, 4, 1, 8, 'rgba(255,240,200,.55)');
      break;
    }
    case 'escudo':
      if (!costas) { px(9, 7, 3, 4, '#c0c7d0'); px(9, 7, 3, 1, '#e4e8ee'); px(10, 8, 1, 2, '#d4a017'); }
      break;
    case 'mago':
      px(0, 1, 10, 1, '#2a6b3e'); px(2, 0, 6, 1, '#2f7d4a'); px(3, -1, 4, 1, '#2f7d4a'); px(4, -2, 3, 1, '#2f7d4a'); px(5, -3, 2, 1, '#2f7d4a'); px(6, -4, 2, 1, '#2f7d4a');
      if (!costas) { px(2, 3, 2, 1, '#cfd8dc'); px(6, 3, 2, 1, '#cfd8dc'); px(3, 3, 1, 1, '#1b1b1b'); px(6, 3, 1, 1, '#1b1b1b'); }
      break;
    case 'capuz':
      px(1, 0, 8, 2, h.capa); px(1, 1, 1, 5, h.capa); px(8, 1, 1, 5, h.capa);
      if (!costas) { px(2, 2, 1, 2, h.cabelo); px(7, 2, 1, 2, h.cabelo); }
      break;
    case 'bastao':
      px(2, 1, 6, 1, '#e08a2e');
      break;
  }
}

function mestreMagos(p) {
  const P = 3, t = tc;
  const andando = !p.sentado && p.atual && (p.atual.tipo === 'andar' || p.atual.tipo === 'sentar');
  const x0 = Math.round(p.x) - 15, y0 = Math.round(p.y) - 13 * P - (p.sentado ? 6 : 0);
  const px = (cx, cy, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(x0 + cx * P, y0 + cy * P, w * P, h * P); };
  // some quando anda: fica um vulto e brilhos em volta
  if (andando) {
    for (let i = 0; i < 10; i++) {
      const a = t * 3 + i * 0.63, r = 18 + 6 * Math.sin(t * 5 + i);
      ctx.fillStyle = `rgba(255,226,140,${0.5 + 0.5 * Math.sin(t * 8 + i)})`;
      ctx.fillRect(p.x + Math.cos(a) * r - 1, y0 + 20 + Math.sin(a) * r * 0.8 - 1, 3, 3);
    }
  } else {
    ctx.fillStyle = 'rgba(0,0,0,.3)'; ctx.beginPath(); ctx.ellipse(p.x, p.y + 1, 12, 4, 0, 0, Math.PI * 2); ctx.fill();
  }
  ctx.save(); ctx.globalAlpha = andando ? 0.28 + 0.12 * Math.sin(t * 6) : 1;
  const costas = p.dir === 'cima' && !p.sentado;
  px(1, 6, 8, 6, '#b3262b'); px(0, 9, 10, 3, '#b3262b'); px(0, 12, 10, 1, '#8a1c20');
  px(1, 6, 8, 1, '#e0b048'); if (!costas) { px(2, 7, 1, 2, '#e0b048'); px(7, 7, 1, 2, '#e0b048'); px(4, 7, 2, 5, '#9a1f24'); }
  px(0, 10, 1, 1, '#e8bb8e'); px(9, 10, 1, 1, '#e8bb8e');
  if (!p.sentado) { px(2, 13, 2, 1, '#5c3a1e'); px(6, 13, 2, 1, '#5c3a1e'); }
  px(2, 1, 6, 5, '#e8bb8e'); px(3, 0, 4, 1, '#e8bb8e');
  px(1, 2, 1, 3, '#f2f2f2'); px(8, 2, 1, 3, '#f2f2f2');
  if (!costas) {
    px(2, 2, 2, 1, '#f2f2f2'); px(6, 2, 2, 1, '#f2f2f2');
    px(3, 3, 1, 1, '#2b1a0c'); px(6, 3, 1, 1, '#2b1a0c');
    px(4, 4, 2, 1, '#d19a6a'); px(4, 5, 2, 1, '#b07a52');
  }
  ctx.restore();
  // pensando: uma bola de luz sobre a cabeça
  if (p.bolha && p.bolha.tipo === 'pensar') {
    const g = ctx.createRadialGradient(p.x, y0 - 8, 1, p.x, y0 - 8, 14);
    g.addColorStop(0, 'rgba(255,255,220,.95)'); g.addColorStop(1, 'rgba(255,200,80,0)');
    ctx.fillStyle = g; ctx.fillRect(p.x - 14, y0 - 22, 28, 28);
  }
}

function desenharVingador(agora) {
  if (!vingador) return;
  const u = (agora - vingador.t0) / 7000;
  if (u > 1) { vingador = null; return; }
  const a = Math.min(1, u * 5, (1 - u) * 5), x = 340, y = 230, P = 3, x0 = x - 15, y0 = y - 48;
  ctx.save(); ctx.globalAlpha = a;
  const g = ctx.createRadialGradient(x, y - 24, 4, x, y - 24, 60);
  g.addColorStop(0, 'rgba(120,0,20,.6)'); g.addColorStop(1, 'rgba(60,0,10,0)'); ctx.fillStyle = g; ctx.fillRect(x - 60, y - 84, 120, 120);
  const px = (cx, cy, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(x0 + cx * P, y0 + cy * P, w * P, h * P); };
  px(-1, 6, 12, 10, '#7a0f14');
  px(1, 6, 8, 5, '#2b2b33'); px(2, 11, 2, 4, '#1a1a1f'); px(6, 11, 2, 4, '#1a1a1f'); px(0, 6, 1, 4, '#2b2b33'); px(9, 6, 1, 4, '#2b2b33');
  px(2, 2, 6, 4, '#9aa0a8'); px(2, 0, 6, 2, '#1a1a1f'); px(1, 1, 1, 4, '#1a1a1f'); px(8, 1, 1, 4, '#1a1a1f'); px(4, -3, 2, 3, '#d8d0c0');
  px(3, 3, 1, 1, '#ff2a2a'); px(6, 3, 1, 1, '#ff2a2a'); px(4, 5, 2, 1, '#5a5f66');
  ctx.font = "13px 'MedievalSharp', Georgia, serif";
  const txt = 'Vocês nunca voltarão para casa!', w = ctx.measureText(txt).width + 16;
  ctx.fillStyle = '#1a0508'; ctx.strokeStyle = '#c0161f'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.roundRect(x - w / 2, y - 112, w, 24, 6); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#ff6a6a'; ctx.fillText(txt, x - w / 2 + 8, y - 95);
  ctx.restore();
}

function dragaoVoando(t) {
  const u = (t % 32) / 10;
  if (u > 1) return;
  const x = W + 70 - u * (W + 180), y = 30 + Math.sin(u * 9) * 8, asa = Math.sin(t * 9) * 10;
  ctx.save(); ctx.translate(x, y);
  ctx.strokeStyle = '#a3301c'; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(18, 0); ctx.quadraticCurveTo(40, 10, 58, -6); ctx.stroke();
  ctx.fillStyle = '#7e2414'; ctx.beginPath(); ctx.moveTo(-8, -3); ctx.lineTo(12, -3); ctx.lineTo(6, -22 - asa); ctx.fill();
  ctx.fillStyle = '#c23b22'; ctx.beginPath(); ctx.ellipse(0, 0, 22, 7, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillRect(-34, -8, 14, 9); ctx.fillRect(-26, -4, 8, 6);
  ctx.fillStyle = '#e8d9b0'; ctx.fillRect(-24, -12, 2, 4);
  ctx.fillStyle = '#ffd400'; ctx.fillRect(-30, -6, 3, 2);
  ctx.fillStyle = '#9a2a18'; ctx.beginPath(); ctx.moveTo(-4, -2); ctx.lineTo(14, -2); ctx.lineTo(2, -16 + asa * 0.6); ctx.fill();
  ctx.restore();
}

function etiquetas(p) {
  const y = p.y - 58;
  ctx.font = "11px 'MedievalSharp', Georgia, serif";
  const nome = p.v.nome, wn = ctx.measureText(nome).width + 10;
  ctx.fillStyle = '#2a1a0e'; ctx.fillRect(p.x - wn / 2, y - 11, wn, 14);
  ctx.strokeStyle = '#c9a227'; ctx.lineWidth = 1; ctx.strokeRect(p.x - wn / 2 + 0.5, y - 10.5, wn - 1, 13);
  ctx.fillStyle = '#f3e3c0'; ctx.fillText(nome, p.x - wn / 2 + 5, y);
  ctx.font = "7px 'Press Start 2P', monospace";
  const st = p.status, ws = ctx.measureText(st).width + 8;
  ctx.fillStyle = STATUS_COR[st] || '#8c7a5b'; ctx.fillRect(p.x - ws / 2, y - 25, ws, 12);
  ctx.fillStyle = '#1a0f06'; ctx.fillText(st, p.x - ws / 2 + 4, y - 16);
}

// ============================================================================================
// Balões, pacotes e fogo
// ============================================================================================
function bolha(p) {
  if (!p.bolha) return;
  ctx.save();
  ctx.font = "13px 'MedievalSharp', Georgia, serif";
  const linhas = quebrar(p.bolha.texto, 30);
  const w = Math.max(...linhas.map((l) => ctx.measureText(l).width)) + 18, h = linhas.length * 15 + 10;
  const x = Math.min(Math.max(p.x - w / 2, 4), W - w - 4);
  let y = p.y - 92 - h;
  if (y < 44) y = 44;
  const pensa = p.bolha.tipo === 'pensar', alerta = p.bolha.tipo === 'alerta';
  ctx.fillStyle = alerta ? '#ffd9cc' : pensa ? '#ece4ff' : '#f3e3c0';
  ctx.strokeStyle = alerta ? '#a01b10' : pensa ? '#5b3fa0' : '#6b4423'; ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.roundRect(x, y, w, h, 6); ctx.fill(); ctx.stroke();
  if (pensa) {
    [[0, 8, 4], [-6, 16, 3]].forEach(([dx, dy, r]) => { ctx.beginPath(); ctx.arc(p.x + dx, y + h + dy - 2, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); });
  } else {
    ctx.beginPath(); ctx.moveTo(p.x - 6, y + h - 2); ctx.lineTo(p.x, y + h + 9); ctx.lineTo(p.x + 6, y + h - 2); ctx.fill();
    ctx.beginPath(); ctx.moveTo(p.x - 6, y + h); ctx.lineTo(p.x, y + h + 9); ctx.lineTo(p.x + 6, y + h); ctx.stroke();
  }
  ctx.fillStyle = alerta ? '#7a0f08' : '#2a1a0e';
  linhas.forEach((l, i) => ctx.fillText(l, x + 9, y + 18 + i * 15));
  ctx.restore();
}

let fogo = null, vingador = null;

function desenharPacotes(agora) {
  for (let i = pacotes.length - 1; i >= 0; i--) {
    const k = pacotes[i], u = Math.min(1, (agora - k.t0) / k.ms);
    const pos = (v) => [k.x0 + (k.x1 - k.x0) * v, k.y0 + (k.y1 - k.y0) * v - Math.sin(v * Math.PI) * 40];
    const ruim = k.cor === '#ff6b6b';
    for (let j = 6; j >= 1; j--) {
      const [x, y] = pos(Math.max(0, u - j * 0.03));
      ctx.fillStyle = ruim ? `rgba(255,90,60,${0.5 - j * 0.07})` : `rgba(255,214,110,${0.6 - j * 0.08})`;
      ctx.fillRect(x - 2, y - 2, 4, 4);
    }
    const [x, y] = pos(u);
    const g = ctx.createRadialGradient(x, y, 1, x, y, 9);
    g.addColorStop(0, '#fffbe6'); g.addColorStop(0.5, ruim ? '#ff4a2a' : k.cor === '#f4b740' ? '#ffb02e' : '#7fd4ff'); g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g; ctx.fillRect(x - 9, y - 9, 18, 18);
    if (u >= 1) { pacotes.splice(i, 1); piscaRack = 400; if (k.depois) pacote(k.depois); }
  }
  desenharFogo(agora);
  desenharVingador(agora);
}

function desenharFogo(agora) {
  if (!fogo) return;
  const u = (agora - fogo.t0) / fogo.ms;
  if (u > 1) { fogo = null; return; }
  const c = CABECAS[2], x0 = c.hx, y0 = c.hy + 10, x1 = fogo.alvo.x, y1 = fogo.alvo.y;
  const alcance = Math.min(1, u * 2.5), some = u > 0.7 ? 1 - (u - 0.7) / 0.3 : 1;
  for (let i = 0; i < 70; i++) {
    const f = (i / 70) * alcance;
    const x = x0 + (x1 - x0) * f + Math.sin(i * 12.9 + agora * 0.02) * 16 * f;
    const y = y0 + (y1 - y0) * f + Math.cos(i * 7.1 + agora * 0.025) * 12 * f;
    ctx.fillStyle = f < 0.25 ? `rgba(255,245,170,${0.9 * some})` : f < 0.65 ? `rgba(255,150,40,${0.75 * some})` : `rgba(220,50,20,${0.6 * some})`;
    ctx.beginPath(); ctx.arc(x, y, (3 + f * 14) * (0.4 + 0.6 * some), 0, Math.PI * 2); ctx.fill();
  }
}

// Portão barrou: a Tiamat cospe fogo em quem foi barrado.
const chamadaOriginal = chamada;
chamada = function (linha) {
  chamadaOriginal(linha);
  if (linha.resultado === 'ok') return;
  const quem = ['orquestrador', 'sistema'].includes(linha.agente) ? pessoas.orquestrador : pessoas[linha.agente];
  if (quem) fogo = { t0: performance.now(), ms: 1600 / Math.min(4, velocidadeBase), alvo: { x: quem.x, y: quem.y - 22 } };
};

// Execução que não terminou bem: o Vingador aparece.
const eventoOriginal = evento;
evento = function (passo) {
  eventoOriginal(passo);
  if (passo.tipo === 'fim' && passo.estado !== 'completa') vingador = { t0: performance.now() };
};

// ============================================================================================
// O jeito de falar de cada um
// ============================================================================================
const FALAS_MESTRE = [
  [/^Pode deixar, vou organizar a equipe\.$/, () => 'O caminho de casa começa por esta pergunta, jovens.'],
  [/^Lembrando (\d+) turno\(s\) anteriores…$/, (m) => `O que foi dito ${m[1]} vez(es) antes ilumina o que virá…`],
  [/^Isso é uma pergunta\.$/, () => 'Uma pergunta… a resposta está mais perto do que imaginam.'],
  [/^Isso é um relatório\.$/, () => 'Um relatório. Longa é a jornada, mas cada página guarda uma pista.'],
  [/^Isso é fora do escopo\.$/, () => 'Este caminho não leva a lugar algum, jovens.'],
  [/^Isso é (.+)\.$/, (m) => `${m[1].replace(/_/g, ' ')}… nem tudo é o que parece.`],
  [/^Onda (\d+): (\d+) tarefa\(s\)\.$/, (m) => `Onda ${m[1]}: ${m[2]} missão(ões). O que buscam está nas páginas.`],
  [/^Acho que terminamos\. Vou conferir\.$/, () => 'Talvez seja o fim… ou apenas o começo. Vou conferir.'],
  [/^Servidor, o que ainda falta\?$/, () => 'Tiamat, o que ainda guardas?'],
  [/^Nada pendente\. Tudo pronto!$/, () => 'Nada mais ela guarda. A missão está cumprida!'],
  [/^Faltam (\d+) item\(ns\)\. Mais uma rodada\.$/, (m) => `Ainda há ${m[1]} tesouro(s) escondido(s). Mais uma jornada.`],
  [/^Pronto! O resultado está no chat\.$/, () => 'A resposta está no chat. Mas cuidado com o Vingador…'],
  [/^Terminou como (.+)\.$/, (m) => `Terminou como ${m[1]}. Nem toda jornada leva para casa.`],
];
const RESPOSTAS = {
  extrator: 'Deixa comigo, pessoal!', validador: 'Por que sempre eu?! Tá, tá…', analista: 'Vou tentar um feitiço… tomara que funcione!',
  pesquisador: 'C-certo, Mestre dos Magos.', redator: 'É pra já!',
};
function falaDaTurma(id, texto) {
  if (texto === 'Ok, comigo!') return RESPOSTAS[id] || null;
  if (texto === 'Valeu, equipe!') return 'Valeu, pessoal! Agora a gente volta pra casa?';
  if (texto === 'Hmm, vou olhar.') return 'Ei! Cadê o Mestre dos Magos?!';
  let m = texto.match(/^Pronto: (.+)$/);
  if (m) return id === 'validador' ? `Pronto: ${m[1]} De nada, hein.` : `Missão cumprida: ${m[1]}`;
  if (texto === 'Pronto!') return 'Missão cumprida!';
  m = texto.match(/^Parei: (.+)$/);
  if (m) return `Parei: ${m[1]} Cadê o Mestre quando a gente precisa?!`;
  return null;
}
function traduzir(texto, tabela) {
  for (const [re, f] of tabela) { const m = String(texto).match(re); if (m) return f(m); }
  return null;
}
const fazerOriginal = Pessoa.prototype.fazer;
Pessoa.prototype.fazer = function (acao) {
  if (typeof acao.texto === 'string') {
    if (acao.tipo === 'alerta') acao = { ...acao, texto: acao.texto.replace(/^Portão:/, 'Tiamat barrou:') };
    else if (acao.tipo === 'falar' || acao.tipo === 'pensar') {
      if (this.id === 'orquestrador') {
        const novo = traduzir(acao.texto, FALAS_MESTRE);
        // o pedido ao herói e o raciocínio seguem inteiros na conversa; só o balão ganha o enigma
        acao = novo ? { ...acao, texto: novo }
          : { ...acao, texto: (acao.tipo === 'pensar' ? 'Hmm… ' : 'Jovem guerreiro: ') + acao.texto, completo: acao.completo ?? acao.texto };
      } else {
        const novo = falaDaTurma(this.id, acao.texto);
        if (novo) acao = { ...acao, texto: novo };
      }
    }
  }
  return fazerOriginal.call(this, acao);
};

// ============================================================================================
// O avatar do Mestre e os enigmas do painel
// ============================================================================================
const AVATAR = 'data:image/svg+xml,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<path d="M10 64c2-12 10-18 22-18s20 6 22 18z" fill="#b3262b"/>
<path d="M14 50c6-5 12-6 18-6s12 1 18 6l-4 5c-4-3-9-5-14-5s-10 2-14 5z" fill="#e0b048"/>
<path d="M30 50h4v14h-4z" fill="#9a1f24"/>
<ellipse cx="32" cy="30" rx="13" ry="15" fill="#e8bb8e"/>
<ellipse cx="19" cy="31" rx="3" ry="4" fill="#d9a77a"/><ellipse cx="45" cy="31" rx="3" ry="4" fill="#d9a77a"/>
<path d="M18 24c-5 2-6 8-3 12 1-4 3-6 5-7zM46 24c5 2 6 8 3 12-1-4-3-6-5-7z" fill="#f2f2f2"/>
<path d="M22 25q4-3 8 0M34 25q4-3 8 0" stroke="#f2f2f2" stroke-width="2.4" fill="none" stroke-linecap="round"/>
<circle cx="27" cy="29" r="1.6" fill="#2b1a0c"/><circle cx="37" cy="29" r="1.6" fill="#2b1a0c"/>
<path d="M31 31q1 5 3 3" stroke="#b07a52" stroke-width="1.4" fill="none"/>
<path d="M27 38q5 3 10 0" stroke="#9a5a3a" stroke-width="1.5" fill="none" stroke-linecap="round"/>
<path d="M24 17q8-4 16 0" stroke="#f6d3ad" stroke-width="2" fill="none" opacity=".7"/>
</svg>`);
document.querySelectorAll('img.avatar').forEach((img) => (img.src = AVATAR));

const linhaFalaOriginal = linhaFala;
linhaFala = function (f) {
  const div = linhaFalaOriginal(f);
  if (f.de === VISUAL.orquestrador.nome) {
    const img = new Image(); img.src = AVATAR; img.alt = ''; img.className = 'av';
    div.querySelector('b')?.before(img);
    div.classList.add('mestre');
  }
  return div;
};

const ENIGMAS = [
  'Para voltar para casa, sigam a fonte de cada número.',
  'Nem tudo que brilha no PDF é um indicador.',
  'O portão que vos barra é o mesmo que vos protege.',
  'Quando a resposta parecer longe, olhem a página.',
  'O Vingador espreita nos números sem fonte.',
  'Tiamat guarda os portões: só passa o que foi provado.',
  'O mapa está no índice; o tesouro, nos trechos.',
  'Às vezes a resposta já foi dada… no turno anterior.',
  'Um gabarito não mente, jovens. Mas também não ensina.',
];
let enigmaI = Math.floor(Math.random() * ENIGMAS.length);
function enigma() {
  const el = document.getElementById('sabedoria');
  if (!el) return;
  el.classList.remove('entra'); void el.offsetWidth; el.classList.add('entra');
  el.textContent = '“' + ENIGMAS[enigmaI++ % ENIGMAS.length] + '”';
}
enigma(); setInterval(enigma, 9000);
