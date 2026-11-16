// A sala psicodélica: o mesmo escritório e os mesmos eventos, redesenhados, com o orquestrador no
// papel de mestre Yoda. Carregado depois de cena.js, conversa.js e eventos.js: as funções de
// desenho declaradas aqui substituem as de lá (scripts clássicos dividem o escopo global).

// ============================================================================================
// Relógio e cores
// ============================================================================================
let calmo = false;
try { calmo = matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { /* segue animado */ }
let relogio = 0, relogioAntes = performance.now(), tt = 0;
function tique() {
  const a = performance.now();
  relogio += Math.min(0.1, (a - relogioAntes) / 1000) * (calmo ? 0.15 : 1);
  relogioAntes = a;
  return relogio;
}
const hsl = (h, s = 100, l = 60, a = 1) => `hsla(${((h % 360) + 360) % 360},${s}%,${l}%,${a})`;
const HUE = { usuario: 220, orquestrador: 100, extrator: 170, validador: 340, analista: 280, pesquisador: 40, redator: 200 };

VISUAL.orquestrador.nome = 'MESTRE YODA';
Object.assign(STATUS_COR, { LIVRE: '#8a7fb0', TRABALHANDO: '#5dff8f', ESPERANDO: '#ffd23f', 'REUNIÃO': '#3ff3ff',
  ANDANDO: '#c9b8ff', PENSANDO: '#ff7bf2', 'NO PORTÃO': '#ff4f6d' });

const ESTRELAS = Array.from({ length: 80 }, (_, i) => ({ x: (i * 397) % W, y: 44 + ((i * 211) % (H - 50)), f: i * 1.7 }));

// ============================================================================================
// Cenário
// ============================================================================================
function chao() {
  const t = (tt = tique());
  // espiral arco-íris que passeia pela sala
  const cx = W / 2 + Math.sin(t * 0.3) * 140, cy = H / 2 + Math.cos(t * 0.21) * 90;
  const g = ctx.createConicGradient(t * 0.35, cx, cy);
  for (let i = 0; i <= 12; i++) g.addColorStop(i / 12, hsl(i * 30 + t * 35, 85, 52));
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  // túnel: anéis escuros saindo do centro
  ctx.lineWidth = 22; ctx.strokeStyle = 'rgba(12,0,32,.32)';
  for (let r = (t * 50) % 70; r < 1000; r += 70) { ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke(); }
  // xadrez que ondula
  for (let y = 0; y < H; y += 28) for (let x = (y / 28) % 2 ? 28 : 0; x < W; x += 56) {
    const w = Math.sin(x * 0.018 + t * 1.2) + Math.cos(y * 0.022 - t * 0.9);
    ctx.fillStyle = `rgba(16,0,40,${0.22 + 0.09 * w})`; ctx.fillRect(x, y, 28, 28);
  }
  ctx.fillStyle = 'rgba(10,0,28,.28)'; ctx.fillRect(0, 0, W, H); // véu: pessoas e balões precisam aparecer
  // estrelas
  ESTRELAS.forEach((s) => {
    const a = 0.5 + 0.5 * Math.sin(t * 2 + s.f), r = 1 + a * 2;
    ctx.fillStyle = `rgba(255,255,255,${a * 0.75})`;
    ctx.fillRect(s.x - r, s.y - 0.5, r * 2, 1); ctx.fillRect(s.x - 0.5, s.y - r, 1, r * 2);
  });
  // parede do fundo: uma onda
  ctx.fillStyle = 'rgba(8,0,22,.88)'; ctx.fillRect(0, 0, W, 40);
  for (let x = 0; x < W; x += 8) {
    const y = 20 + Math.sin(x * 0.03 + t * 2) * 10;
    ctx.fillStyle = hsl(x * 0.6 + t * 80, 100, 62); ctx.fillRect(x, y - 2, 8, 4);
  }
  // corredor: estrada arco-íris
  for (let i = 0; i < 7; i++) { ctx.fillStyle = hsl(i * 45 + t * 60, 95, 58, 0.85); ctx.fillRect(0, CORREDOR - 21 + i * 6, W, 6); }
  ctx.fillStyle = 'rgba(255,255,255,.4)';
  for (let x = ((t * 120) % 60) - 60; x < W; x += 60) {
    ctx.beginPath(); ctx.moveTo(x, CORREDOR - 12); ctx.lineTo(x + 14, CORREDOR); ctx.lineTo(x, CORREDOR + 12); ctx.lineTo(x + 6, CORREDOR); ctx.fill();
  }
  // salas
  portal(20, 52, 236, 248, t, 260); rotulo('SALA DO USUÁRIO', 34, 292);
  portal(720, 52, 384, 196, t, 140); rotulo('SERVIDOR MCP · PORTÕES', 736, 72); rotulo('ARQUIVO · INDEXADOR', 960, 72);
  mandala(545, 128, 92, t * 0.4, 12); rotulo('TEMPLO DO MESTRE', 480, 258);
  // auras sob as mesas dos especialistas
  XS.forEach((x) => {
    const rg = ctx.createRadialGradient(x, 470, 10, x, 470, 115);
    rg.addColorStop(0, 'rgba(10,0,30,.78)'); rg.addColorStop(1, 'rgba(10,0,30,0)');
    ctx.fillStyle = rg; ctx.beginPath(); ctx.ellipse(x, 470, 115, 100, 0, 0, Math.PI * 2); ctx.fill();
  });
  // copa cósmica
  ctx.fillStyle = 'rgba(10,0,28,.6)'; ctx.beginPath(); ctx.moveTo(0, H);
  for (let x = 0; x <= W; x += 20) ctx.lineTo(x, 590 + Math.sin(x * 0.02 + t * 1.5) * 6);
  ctx.lineTo(W, H); ctx.fill();
  mandala(560, 688, 62, -t * 0.9, 8);
  cogumelo(455, 640, t, 0); cogumelo(668, 640, t, 2); cogumelo(120, 690, t, 4); cogumelo(1000, 700, t, 1); cogumelo(700, 730, t, 3);
  lampada(492, 662, t); lampada(632, 662, t + 3);
  rotulo('COPA CÓSMICA', 512, 760);
}

function rotulo(texto, x, y) {
  ctx.save(); ctx.font = "8px 'Press Start 2P', monospace";
  ctx.shadowColor = hsl(tt * 90 + x, 100, 60); ctx.shadowBlur = 8; ctx.fillStyle = '#fff'; ctx.fillText(texto, x, y);
  ctx.restore();
}

function portal(x, y, w, h, t, base) {
  ctx.fillStyle = 'rgba(8,0,26,.72)'; ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = hsl(base + t * 50, 100, 65, 0.18); ctx.lineWidth = 1;
  for (let gx = x + 16; gx < x + w; gx += 16) { ctx.beginPath(); ctx.moveTo(gx, y); ctx.lineTo(gx, y + h); ctx.stroke(); }
  const lg = ctx.createLinearGradient(x, y, x + w, y + h);
  for (let i = 0; i <= 6; i++) lg.addColorStop(i / 6, hsl(base + i * 60 + t * 90, 100, 62));
  ctx.save(); ctx.shadowColor = hsl(base + t * 90, 100, 60); ctx.shadowBlur = 14; ctx.strokeStyle = lg; ctx.lineWidth = 4;
  ctx.strokeRect(x, y, w, h); ctx.restore();
}

function mandala(cx, cy, r, rot, n) {
  for (let k = 3; k >= 1; k--) {
    const rr = (r * k) / 3;
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(k % 2 ? rot : -rot * 0.7);
    for (let i = 0; i < n; i++) {
      ctx.rotate((Math.PI * 2) / n);
      ctx.fillStyle = hsl(i * (360 / n) + k * 70 + rot * 60, 100, 60, 0.5);
      ctx.beginPath(); ctx.ellipse(rr * 0.55, 0, rr * 0.45, rr * 0.13, 0, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
  }
  ctx.fillStyle = hsl(rot * 80, 100, 70, 0.9); ctx.beginPath(); ctx.arc(cx, cy, r * 0.12, 0, Math.PI * 2); ctx.fill();
}

function cogumelo(x, y, t, f) {
  const b = Math.sin(t * 1.4 + f) * 3;
  ctx.fillStyle = '#f3e9d2'; ctx.fillRect(x - 4, y, 8, 22);
  ctx.fillStyle = hsl(t * 40 + f * 70, 100, 58);
  ctx.beginPath(); ctx.ellipse(x + b, y + 1, 18, 12, 0, Math.PI, 0); ctx.fill();
  ctx.fillStyle = '#fff';
  [[-9, -4], [0, -8], [8, -3]].forEach(([dx, dy]) => ctx.fillRect(x + b + dx - 2, y + dy - 2, 4, 4));
}

function lampada(x, y, t) {
  ctx.fillStyle = '#2a1a3a'; ctx.fillRect(x - 13, y + 66, 26, 10); ctx.fillRect(x - 8, y - 8, 16, 8);
  ctx.fillStyle = hsl(t * 20 + 220, 80, 22, 0.95);
  ctx.beginPath(); ctx.moveTo(x - 8, y); ctx.lineTo(x + 8, y); ctx.lineTo(x + 14, y + 66); ctx.lineTo(x - 14, y + 66); ctx.fill();
  for (let i = 0; i < 3; i++) {
    const by = y + 33 + Math.sin(t * 0.8 + i * 2.1) * 24;
    ctx.fillStyle = hsl(t * 20 + 10 + i * 30, 100, 60);
    ctx.beginPath(); ctx.ellipse(x + Math.sin(t + i) * 3, by, 4 + i, 6 + i, 0, 0, Math.PI * 2); ctx.fill();
  }
}

function racks() {
  const t = tt;
  for (let i = 0; i < 3; i++) {
    const x = 750 + i * 52, y = 84;
    ctx.fillStyle = '#07000f'; ctx.fillRect(x, y, 40, 128);
    ctx.save(); ctx.strokeStyle = hsl(t * 80 + i * 120, 100, 60); ctx.shadowColor = ctx.strokeStyle; ctx.shadowBlur = 10;
    ctx.lineWidth = 2; ctx.strokeRect(x + 1, y + 1, 38, 126); ctx.restore();
    for (let r = 0; r < 10; r++) {
      ctx.fillStyle = '#1a0a2e'; ctx.fillRect(x + 6, y + 8 + r * 12, 28, 8);
      const on = Math.sin(t * 8 + r * 1.7 + i * 3) > (piscaRack > 0 ? -0.6 : 0.3);
      ctx.fillStyle = on ? hsl(r * 36 + t * 120 + i * 50, 100, 62) : '#140022';
      ctx.fillRect(x + 9, y + 10 + r * 12, 22, 3);
    }
  }
  for (let i = 0; i < 2; i++) {
    const x = 965 + i * 64, y = 86;
    ctx.fillStyle = '#12051f'; ctx.fillRect(x, y, 56, 124);
    for (let r = 0; r < 4; r++) for (let b = 0; b < 7; b++) {
      ctx.fillStyle = hsl(b * 50 + r * 30 + t * 60 + i * 90, 100, 58);
      ctx.fillRect(x + 5 + b * 7, y + 10 + r * 30 + Math.round(Math.sin(t * 2 + b + r) * 2), 5, 20);
    }
  }
}

function mesa(p) {
  const s = LUGARES[p.id].sentado, x = s.x - 76, y = s.y - 12, t = tt, h = HUE[p.id];
  ctx.fillStyle = 'rgba(14,0,34,.92)'; ctx.fillRect(x, y, 152, 44);
  ctx.save(); ctx.shadowColor = hsl(h + t * 60, 100, 60); ctx.shadowBlur = 12; ctx.strokeStyle = ctx.shadowColor; ctx.lineWidth = 2;
  ctx.strokeRect(x, y, 152, 44); ctx.beginPath(); ctx.moveTo(x, y + 34); ctx.lineTo(x + 152, y + 34); ctx.stroke(); ctx.restore();
  // monitor: uma espiral, rápida quando digita
  const mx = x + 10, my = y - 22, digita = p.digitando > 0;
  ctx.fillStyle = '#05000c'; ctx.fillRect(mx, my, 40, 28); ctx.fillRect(mx + 16, my + 28, 8, 6);
  const g = ctx.createConicGradient(t * (digita ? 4 : 0.6), mx + 20, my + 13);
  for (let i = 0; i <= 6; i++) g.addColorStop(i / 6, hsl(h + i * 60, 100, digita ? 60 : 38));
  ctx.fillStyle = g; ctx.fillRect(mx + 3, my + 3, 34, 20);
  if (p.id === 'orquestrador') {
    // bola de cristal
    const rg = ctx.createRadialGradient(x + 112, y + 10, 2, x + 114, y + 14, 12);
    rg.addColorStop(0, '#ffffff'); rg.addColorStop(0.4, hsl(t * 70, 100, 70)); rg.addColorStop(1, hsl(t * 70 + 120, 100, 40, 0.9));
    ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(x + 114, y + 14, 11, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#5a3b22'; ctx.fillRect(x + 106, y + 24, 16, 5);
  } else {
    ctx.fillStyle = '#f5f0ff'; ctx.fillRect(x + 104, y + 8, 20, 14);
    ctx.fillStyle = hsl(h + 180, 70, 70); ctx.fillRect(x + 107, y + 11, 14, 2);
  }
  ctx.fillStyle = hsl(h + 180 + t * 40, 90, 55); ctx.fillRect(x + 132, y + 10, 8, 9);
  for (let k = 0; k < 3; k++) { ctx.fillStyle = hsl(t * 90 + k * 50, 100, 70, 0.7); ctx.fillRect(x + 134 + Math.sin(t * 3 + k) * 2, y + 5 - k * 4, 2, 3); }
  if (p.ferramenta && performance.now() < p.ferramentaAte) {
    ctx.font = '10px "JetBrains Mono", monospace';
    const txt = p.ferramenta.texto, w = ctx.measureText(txt).width + 10;
    ctx.fillStyle = p.ferramenta.ok ? 'rgba(20,70,40,.95)' : 'rgba(120,20,40,.95)';
    ctx.fillRect(x + 76 - w / 2, y + 48, w, 15);
    ctx.fillStyle = p.ferramenta.ok ? '#b6ffcf' : '#ffd0dc'; ctx.fillText(txt, x + 81 - w / 2, y + 59);
  }
}

// ============================================================================================
// Pessoas
// ============================================================================================
function brilho(x, y, raio, h, a = 0.5) {
  const rg = ctx.createRadialGradient(x, y, 2, x, y, raio);
  rg.addColorStop(0, hsl(h, 100, 62, a)); rg.addColorStop(1, hsl(h, 100, 60, 0));
  ctx.fillStyle = rg; ctx.fillRect(x - raio, y - raio, raio * 2, raio * 2);
}

function boneco(p) {
  if (p.id === 'orquestrador') return mestre(p);
  const P = 3, x0 = Math.round(p.x) - 15, y0 = Math.round(p.y) - 48, v = p.v, h = HUE[p.id] + tt * 40;
  const camisa = hsl(h, 90, 52), camisa2 = hsl(h, 90, 38), calca = hsl(h + 180, 55, 26);
  const px = (cx, cy, w, hh, c) => { ctx.fillStyle = c; ctx.fillRect(x0 + cx * P, y0 + cy * P, w * P, hh * P); };
  brilho(p.x, p.y - 24, 36, h, 0.45);
  ctx.fillStyle = 'rgba(0,0,0,.3)'; ctx.beginPath(); ctx.ellipse(p.x, p.y + 1, 14, 4, 0, 0, Math.PI * 2); ctx.fill();
  const costas = p.dir === 'cima' && !p.sentado;
  if (!p.sentado) {
    const f = Math.floor(p.passo) % 2, andando = p.atual && (p.atual.tipo === 'andar' || p.atual.tipo === 'sentar');
    px(2, 11, 2, andando && f ? 3 : 4, calca); px(6, 11, 2, andando && !f ? 3 : 4, calca);
    px(2, andando && f ? 14 : 15, 2, 1, '#111'); px(6, andando && !f ? 14 : 15, 2, 1, '#111');
  }
  px(1, 6, 8, 5, camisa); px(1, 6, 8, 1, camisa2);
  px(3, 8, 1, 1, hsl(h + 120, 100, 70)); px(6, 9, 1, 1, hsl(h + 240, 100, 70)); // estampa
  const dig = p.digitando > 0 && Math.floor(performance.now() / 120) % 2;
  px(0, 6, 1, 4, camisa); px(9, 6, 1, 4, camisa);
  px(0, dig ? 9 : 10, 1, 1, v.pele); px(9, dig ? 10 : 9, 1, 1, v.pele);
  px(2, 2, 6, 4, v.pele);
  px(2, 0, 6, 2, v.cabelo); px(1, 1, 1, 3, v.cabelo); px(8, 1, 1, 3, v.cabelo);
  if (costas) px(2, 2, 6, 3, v.cabelo);
  else {
    // óculos redondos coloridos
    px(2, 3, 2, 1, hsl(h + 150, 100, 60)); px(6, 3, 2, 1, hsl(h + 210, 100, 60)); px(4, 3, 2, 1, '#1b1b1b');
    px(4, 5, 2, 1, shade(v.pele));
  }
  px(3, -1, 4, 1, hsl(h + 90, 100, 60)); // faixa na cabeça
}

const VERDE = '#8cc152', VERDE_ESC = '#5e8f32', ORELHA = '#d9a98c', MANTO = '#8a6640', MANTO_ESC = '#6b4c2e', TUNICA = '#cdb68a', BENGALA = '#5a3b22';
function mestre(p) {
  const P = 3, t = tt;
  const flutua = p.sentado ? 0 : 6 + Math.sin(t * 2.2) * 3; // levita quando anda
  const x0 = Math.round(p.x) - 15, y0 = Math.round(p.y) - 14 * P - Math.round(flutua) - (p.sentado ? 4 : 0);
  const px = (cx, cy, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(x0 + cx * P, y0 + cy * P, w * P, h * P); };
  // aura da Força
  const raio = 44 + Math.sin(t * 1.6) * 5;
  const rg = ctx.createRadialGradient(p.x, y0 + 20, 4, p.x, y0 + 20, raio);
  rg.addColorStop(0, hsl(100 + t * 60, 100, 65, 0.65)); rg.addColorStop(0.6, hsl(160 + t * 60, 100, 55, 0.25)); rg.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = rg; ctx.fillRect(p.x - raio, y0 + 20 - raio, raio * 2, raio * 2);
  if (!p.sentado) { ctx.fillStyle = 'rgba(0,0,0,.28)'; ctx.beginPath(); ctx.ellipse(p.x, p.y + 1, 13 - flutua * 0.4, 4, 0, 0, Math.PI * 2); ctx.fill(); }
  // pensando: anéis girando em volta da cabeça
  if (p.bolha && p.bolha.tipo === 'pensar') {
    for (let k = 0; k < 3; k++) {
      ctx.strokeStyle = hsl(t * 120 + k * 120, 100, 65, 0.85); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.ellipse(p.x, y0 + 8, 26 + k * 7, 8 + k * 2, Math.sin(t + k) * 0.4, t * 2 + k, t * 2 + k + Math.PI * 1.3); ctx.stroke();
    }
  }
  const costas = p.dir === 'cima' && !p.sentado;
  px(11, 6, 1, 8, BENGALA); px(10, 5, 2, 1, BENGALA);            // bengala
  px(1, 7, 8, 5, MANTO); px(0, 9, 10, 4, MANTO); px(-1, 11, 12, 2, MANTO_ESC); // manto
  if (!costas) { px(3, 7, 4, 5, TUNICA); px(4, 7, 2, 1, MANTO_ESC); }
  px(-1, 9, 1, 2, VERDE); px(10, 8, 1, 2, VERDE);                 // mãos
  if (!p.sentado) { const f = Math.floor(p.passo) % 2; px(2, 13, 2, 1, f ? VERDE_ESC : VERDE); px(6, 13, 2, 1, f ? VERDE : VERDE_ESC); }
  // orelhas (de vez em quando mexem)
  const mexe = Math.sin(t * 1.8) > 0.85 ? 1 : 0;
  px(-4, 2 - mexe, 1, 1, VERDE); px(-4, 3, 5, 1, VERDE); px(-2, 4, 3, 1, VERDE);
  px(13, 2 - mexe, 1, 1, VERDE); px(9, 3, 5, 1, VERDE); px(9, 4, 3, 1, VERDE);
  if (!costas) { px(-3, 3, 3, 1, ORELHA); px(10, 3, 3, 1, ORELHA); }
  // cabeça e o cabelo ralo
  px(1, 1, 8, 6, VERDE); px(2, 0, 6, 1, VERDE); px(2, 6, 6, 1, VERDE_ESC);
  px(1, 0, 1, 1, '#eeeeff'); px(8, 0, 1, 1, '#eeeeff'); px(3, -1, 1, 1, '#ddddee'); px(6, -1, 1, 1, '#ddddee');
  px(2, 2, 6, 1, VERDE_ESC); // rugas
  if (!costas) {
    const pisca = t % 4 < 0.12;
    px(2, 3, 2, 1, pisca ? VERDE_ESC : '#efe9b8'); px(6, 3, 2, 1, pisca ? VERDE_ESC : '#efe9b8');
    if (!pisca) { px(3, 3, 1, 1, '#2b1a0c'); px(6, 3, 1, 1, '#2b1a0c'); }
    px(4, 4, 2, 1, VERDE_ESC); px(3, 5, 4, 1, '#4c7a28');
  }
}

// ============================================================================================
// Balões e pacotes
// ============================================================================================
function bolha(p) {
  if (!p.bolha) return;
  const linhas = quebrar(p.bolha.texto, 30);
  ctx.font = '11px "JetBrains Mono", monospace';
  const w = Math.max(...linhas.map((l) => ctx.measureText(l).width)) + 16, h = linhas.length * 14 + 10;
  const x = Math.min(Math.max(p.x - w / 2, 4), W - w - 4);
  let y = p.y - 92 - h;
  if (y < 44) y = 44;
  const pensa = p.bolha.tipo === 'pensar', alerta = p.bolha.tipo === 'alerta', base = HUE[p.id] + tt * 50;
  ctx.save();
  ctx.fillStyle = alerta ? '#ffe3ea' : pensa ? hsl(base, 100, 94) : p.id === 'orquestrador' ? '#effbe2' : '#fffdf5';
  const lg = ctx.createLinearGradient(x, y, x + w, y + h);
  for (let i = 0; i <= 6; i++) lg.addColorStop(i / 6, alerta ? hsl(340 + i * 6, 100, 50) : hsl(base + i * 60, 100, 55));
  ctx.strokeStyle = lg; ctx.lineWidth = 3; ctx.shadowColor = alerta ? '#ff4f6d' : hsl(base, 100, 60); ctx.shadowBlur = 14;
  ctx.beginPath(); ctx.roundRect(x, y, w, h, 10); ctx.fill(); ctx.stroke();
  ctx.shadowBlur = 0;
  if (pensa) {
    [[0, 8, 4], [-6, 16, 3]].forEach(([dx, dy, r]) => { ctx.beginPath(); ctx.arc(p.x + dx, y + h + dy - 2, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); });
  } else {
    ctx.beginPath(); ctx.moveTo(p.x - 6, y + h - 2); ctx.lineTo(p.x, y + h + 9); ctx.lineTo(p.x + 6, y + h - 2); ctx.fill();
    ctx.beginPath(); ctx.moveTo(p.x - 6, y + h); ctx.lineTo(p.x, y + h + 9); ctx.lineTo(p.x + 6, y + h); ctx.stroke();
  }
  ctx.fillStyle = alerta ? '#a40e26' : '#1a0b2e';
  linhas.forEach((l, i) => ctx.fillText(l, x + 8, y + 17 + i * 14));
  ctx.restore();
}

function desenharPacotes(agora) {
  for (let i = pacotes.length - 1; i >= 0; i--) {
    const k = pacotes[i], t = Math.min(1, (agora - k.t0) / k.ms);
    const pos = (u) => [k.x0 + (k.x1 - k.x0) * u, k.y0 + (k.y1 - k.y0) * u - Math.sin(u * Math.PI) * 40];
    for (let j = 7; j >= 1; j--) { // rastro arco-íris
      const [x, y] = pos(Math.max(0, t - j * 0.035));
      ctx.fillStyle = hsl(tt * 200 + j * 40 + i * 30, 100, 62, 1 - j / 8);
      ctx.beginPath(); ctx.arc(x, y, 6 - j * 0.5, 0, Math.PI * 2); ctx.fill();
    }
    const [x, y] = pos(t);
    ctx.save(); ctx.shadowColor = k.cor; ctx.shadowBlur = 12; ctx.fillStyle = k.cor;
    ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke(); ctx.restore();
    if (t >= 1) { pacotes.splice(i, 1); piscaRack = 400; if (k.depois) pacote(k.depois); }
  }
}

// ============================================================================================
// O jeito de falar do mestre
// ============================================================================================
const FALAS_MESTRE = [
  [/^Pode deixar, vou organizar a equipe\.$/, () => 'Organizar a equipe, eu vou. Hmmm.'],
  [/^Lembrando (\d+) turno\(s\) anteriores…$/, (m) => `Lembrar ${m[1]} turno(s) passados, eu devo…`],
  [/^Isso é uma pergunta\.$/, () => 'Uma pergunta, isto é. Hmm.'],
  [/^Isso é um relatório\.$/, () => 'Um relatório, isto é. Longa, a jornada será.'],
  [/^Isso é fora do escopo\.$/, () => 'Fora do escopo, isto está. Recusar, eu devo.'],
  [/^Isso é (.+)\.$/, (m) => `${m[1].replace(/_/g, ' ')}, isto é. Hmm.`],
  [/^Onda (\d+): (\d+) tarefa\(s\)\.$/, (m) => `Onda ${m[1]}, esta é. ${m[2]} tarefa(s) distribuir, eu vou.`],
  [/^Acho que terminamos\. Vou conferir\.$/, () => 'Terminado, acho que está. Conferir, eu vou.'],
  [/^Servidor, o que ainda falta\?$/, () => 'Servidor, o que falta ainda? Diga-me.'],
  [/^Nada pendente\. Tudo pronto!$/, () => 'Pendente, nada está. Pronto, tudo está!'],
  [/^Faltam (\d+) item\(ns\)\. Mais uma rodada\.$/, (m) => `${m[1]} item(ns) faltam. Mais uma rodada, fazer nós vamos.`],
  [/^Pronto! O resultado está no chat\.$/, () => 'No chat, o resultado está. Que a Força com você esteja.'],
  [/^Terminou como (.+)\.$/, (m) => `Como ${m[1]}, terminou. Hmm. Nublado, o futuro está.`],
];
const FALAS_EQUIPE = [
  [/^Ok, comigo!$/, () => 'Sim, Mestre!'],
  [/^Valeu, equipe!$/, () => 'Valeu, Mestre Yoda!'],
  [/^Hmm, vou olhar\.$/, () => 'Hmm, vou olhar, Mestre.'],
  [/^Pronto: (.+)$/, (m) => `Mestre, pronto: ${m[1]}`],
  [/^Pronto!$/, () => 'Pronto, Mestre!'],
];
function traduzir(texto, tabela) {
  for (const [re, f] of tabela) { const m = String(texto).match(re); if (m) return f(m); }
  return null;
}
const fazerOriginal = Pessoa.prototype.fazer;
Pessoa.prototype.fazer = function (acao) {
  if ((acao.tipo === 'falar' || acao.tipo === 'pensar') && typeof acao.texto === 'string') {
    const ehMestre = this.id === 'orquestrador';
    const novo = traduzir(acao.texto, ehMestre ? FALAS_MESTRE : FALAS_EQUIPE);
    if (novo) acao = { ...acao, texto: novo };
    // o pedido ao especialista e o raciocínio seguem inteiros na conversa; só o balão ganha o sotaque
    else if (ehMestre) acao = { ...acao, texto: (acao.tipo === 'pensar' ? 'Sinto na Força… ' : 'Hmm, padawan: ') + acao.texto, completo: acao.completo ?? acao.texto };
  }
  return fazerOriginal.call(this, acao);
};

// ============================================================================================
// O avatar e a sabedoria do painel
// ============================================================================================
const AVATAR = 'data:image/svg+xml,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<path d="M14 64c2-11 9-17 18-17s16 6 18 17z" fill="#8a6640"/><path d="M25 48l7 9 7-9" fill="#cdb68a"/>
<path d="M21 30 2 19c1 9 6 16 17 20z" fill="#8cc152"/><path d="M19 31 7 23c2 6 5 10 12 13z" fill="#d9a98c"/>
<path d="M43 30 62 19c-1 9-6 16-17 20z" fill="#8cc152"/><path d="M45 31 57 23c-2 6-5 10-12 13z" fill="#d9a98c"/>
<ellipse cx="32" cy="33" rx="15" ry="14" fill="#8cc152"/>
<path d="M22 25q10-4 20 0M24 28q8-3 16 0" stroke="#5e8f32" stroke-width="1.4" fill="none"/>
<ellipse cx="25.5" cy="33" rx="4.2" ry="3.2" fill="#efe9b8"/><ellipse cx="38.5" cy="33" rx="4.2" ry="3.2" fill="#efe9b8"/>
<circle cx="26.5" cy="33" r="2" fill="#2b1a0c"/><circle cx="37.5" cy="33" r="2" fill="#2b1a0c"/>
<circle cx="27.2" cy="32.3" r=".6" fill="#fff"/><circle cx="38.2" cy="32.3" r=".6" fill="#fff"/>
<path d="M31 35q1 3 3 1" stroke="#5e8f32" stroke-width="1.3" fill="none"/>
<path d="M26 41q6 3 12 0" stroke="#4c7a28" stroke-width="1.6" fill="none" stroke-linecap="round"/>
<path d="M24 21q-3-4-1-7M30 19q0-4 2-6M38 20q3-3 2-7" stroke="#f2f2ff" stroke-width="1" fill="none"/>
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

const SABEDORIA = [
  'Sem fonte, número não há.',
  'Inventar o valor, o portão não deixa.',
  'Fazer ou não fazer. Tentativa, não há.',
  'Paciência. Lento, o parse do PDF é.',
  'Ao gabarito, comparar você deve.',
  'Muitos tokens, caro fica. Hmm.',
  'Sem crédito, nem a Força responde (429).',
  'Delegar bem, metade do trabalho é.',
  'Pela página, a citação provar você deve.',
  'Num trace só, cabe o relatório inteiro. Agora.',
];
let sabedoriaI = Math.floor(Math.random() * SABEDORIA.length);
function sabedoria() {
  const el = document.getElementById('sabedoria');
  if (!el) return;
  el.classList.remove('entra'); void el.offsetWidth; el.classList.add('entra');
  el.textContent = '“' + SABEDORIA[sabedoriaI++ % SABEDORIA.length] + '”';
}
sabedoria(); setInterval(sabedoria, 8000);

const botaoCalmar = document.getElementById('calmar');
function aplicarCalma() {
  document.body.classList.toggle('calmo', calmo);
  botaoCalmar.textContent = calmo ? '✺ Viajar' : '☮ Calmar';
  botaoCalmar.title = calmo ? 'Volta o movimento todo' : 'Desacelera as animações';
}
botaoCalmar.onclick = () => { calmo = !calmo; aplicarCalma(); };
aplicarCalma();
