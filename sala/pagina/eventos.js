// Cada passo do orquestrador e cada chamada da auditoria do MCP vira uma ação na sala.
// Scripts clássicos, carregados em ordem pelo index.html: o que um declara, os seguintes enxergam.

// ============================================================================================
// Eventos → ações
// ============================================================================================
const GRAVA = { salvar_indicador: 'indicadores', salvar_nao_encontrado: 'ausências', fechar_extracao: 'extração fechada',
  registrar_alerta: 'alertas', concluir_revisao: 'revisão concluída', salvar_item_analise: 'achados', registrar_ausencia_analise: 'ausências',
  salvar_resposta: 'resposta', salvar_resumo: 'resumo', montar_relatorio: 'relatório montado' };
const RAG = new Set(['buscar_trechos', 'ler_chunk', 'ler_pagina', 'listar_documentos']);
let nomesDocs = {}, pensamentos = {};

function curto(t, n = 70) { t = String(t ?? ''); return t.length > n ? t.slice(0, n - 1) + '…' : t; }
function nomeDoc(id) { return id ? (nomesDocs[id] || 'documento ' + id.slice(0, 6)) : ''; }
function relato(passo) {
  if (passo.estado !== 'concluido') return `Parei: ${passo.estado}${passo.erro ? ' (' + passo.erro + ')' : ''}.`;
  const g = Object.entries(passo.gravacoes || {}).map(([k, n]) => (n > 1 ? `${n} ` : '') + (GRAVA[k] || k));
  return g.length ? `Pronto: ${g.join(', ')}.` : 'Pronto!';
}

function evento(passo) {
  const O = pessoas.orquestrador, U = pessoas.usuario;
  const ag = passo.agente && pessoas[passo.agente];
  switch (passo.tipo) {
    case 'inicio':
      U.fazer({ tipo: 'status', valor: 'ESPERANDO' });
      O.fazer({ tipo: 'andar', lugar: 'usuario', alvo: LUGARES.usuario.visita });
      O.fazer({ tipo: 'acao', fn: () => U.fazer({ tipo: 'falar', texto: curto(passo.pedido, 110), completo: passo.pedido, ms: 2600, para: 'orquestrador' }) });
      O.fazer({ tipo: 'esperar', ms: 2600 });
      O.fazer({ tipo: 'falar', texto: 'Pode deixar, vou organizar a equipe.', ms: 1600, para: 'usuario' });
      O.fazer({ tipo: 'voltar' });
      break;
    case 'conversa':
      O.fazer({ tipo: 'pensar', texto: `Lembrando ${passo.turnos_anteriores} turno(s) anteriores…`, ms: 1400 });
      break;
    case 'intencao': {
      const r = { pergunta: 'uma pergunta', relatorio: 'um relatório', fora_do_escopo: 'fora do escopo' }[passo.intencao] || passo.intencao;
      O.fazer({ tipo: 'pensar', texto: `Isso é ${r}.`, ms: 1400 });
      break;
    }
    case 'orquestrador': {
      const d = passo.decisao;
      if (d && d.delegacoes && d.delegacoes.length) O.fazer({ tipo: 'falar', texto: `Onda ${passo.onda}: ${d.delegacoes.length} tarefa(s).`, ms: 1300 });
      else if (d && d.terminado) O.fazer({ tipo: 'falar', texto: 'Acho que terminamos. Vou conferir.', ms: 1300 });
      break;
    }
    case 'delegando':
      if (!ag) break;
      O.fazer({ tipo: 'andar', lugar: passo.agente, alvo: LUGARES[passo.agente].visita, statusChegada: 'REUNIÃO' });
      O.fazer({ tipo: 'falar', texto: (passo.doc_id ? nomeDoc(passo.doc_id) + ': ' : '') + curto(passo.tarefa, 90),
                completo: (passo.doc_id ? nomeDoc(passo.doc_id) + ': ' : '') + passo.tarefa, ms: 2200, para: passo.agente });
      O.fazer({ tipo: 'acao', fn: () => { ag.fazer({ tipo: 'falar', texto: 'Ok, comigo!', ms: 900, para: 'orquestrador' }); ag.fazer({ tipo: 'status', valor: 'TRABALHANDO' }); } });
      O.fazer({ tipo: 'voltar' });
      pensamentos[passo.agente] = 0;
      break;
    case 'raciocinio':
      if (!ag) break;
      if (passo.agente !== 'orquestrador' && (pensamentos[passo.agente] = (pensamentos[passo.agente] || 0) + 1) > 2) break;
      ag.fazer({ tipo: 'pensar', texto: curto(String(passo.texto).replace(/\*\*/g, '').replace(/\s+/g, ' '), 110),
                 completo: String(passo.texto).replace(/\*\*/g, '').trim(), ms: 1800 });
      break;
    case 'especialista':
      if (!ag) break;
      ag.fazer({ tipo: 'status', valor: 'ESPERANDO' });
      ag.fazer({ tipo: 'andar', lugar: 'orquestrador', alvo: { x: LUGARES.orquestrador.visita.x + 30 * (ESPECIALISTAS.indexOf(passo.agente) - 2), y: LUGARES.orquestrador.visita.y }, statusChegada: 'REUNIÃO' });
      ag.fazer({ tipo: 'falar', texto: relato(passo), ms: 2000, para: 'orquestrador' });
      ag.statusDepois = 'LIVRE';
      ag.fazer({ tipo: 'voltar' });
      break;
    case 'conferencia':
      O.fazer({ tipo: 'andar', lugar: 'mcp', alvo: LUGARES.mcp.visita, statusChegada: 'REUNIÃO' });
      O.fazer({ tipo: 'falar', texto: 'Servidor, o que ainda falta?', ms: 1300 });
      O.fazer({ tipo: 'falar', texto: passo.completo ? 'Nada pendente. Tudo pronto!' : `Faltam ${passo.pendencias} item(ns). Mais uma rodada.`, ms: 1700 });
      O.fazer({ tipo: 'voltar' });
      break;
    case 'fim':
      O.fazer({ tipo: 'andar', lugar: 'usuario', alvo: LUGARES.usuario.visita });
      O.fazer({ tipo: 'falar', texto: passo.estado === 'completa' ? 'Pronto! O resultado está no chat.' : `Terminou como ${passo.estado}.`, ms: 2000, para: 'usuario' });
      O.fazer({ tipo: 'acao', fn: () => { U.fazer({ tipo: 'falar', texto: passo.estado === 'completa' ? 'Valeu, equipe!' : 'Hmm, vou olhar.', ms: 1400, para: 'orquestrador' }); U.fazer({ tipo: 'status', valor: 'LIVRE' }); } });
      O.fazer({ tipo: 'status', valor: 'LIVRE' });
      O.statusDepois = 'LIVRE';
      O.fazer({ tipo: 'voltar' });
      break;
  }
}

function chamada(linha) {
  chamadas++;
  const ok = linha.resultado === 'ok';
  if (!ok) barradas++;
  const quem = ['orquestrador', 'sistema'].includes(linha.agente) ? pessoas.orquestrador : pessoas[linha.agente];
  if (!quem) return;
  const s = LUGARES[quem.id].sentado;
  quem.ferramenta = { texto: linha.tool, ok }; quem.ferramentaAte = performance.now() + 1500 / velocidadeBase;
  quem.digitando = 0.8;
  if (quem.id !== 'orquestrador' && quem.status !== 'TRABALHANDO' && quem.sentado) quem.status = 'TRABALHANDO';
  const daMesa = { x0: s.x - 50, y0: s.y - 30, x1: RACK.x + Math.random() * 80, y1: RACK.y, cor: ok ? '#3ddc84' : '#ff6b6b' };
  if (RAG.has(linha.tool) && ok) daMesa.depois = { x0: RACK.x + 60, y0: RACK.y, x1: ARQUIVO.x, y1: ARQUIVO.y, cor: '#f4b740', ms: 500 };
  pacote(daMesa);
  if (!ok && linha.resultado === 'rejeitada') quem.fazer({ tipo: 'alerta', texto: 'Portão: ' + curto(linha.detalhe, 80),
                completo: `Portão (${linha.tool}): ${linha.detalhe}`, ms: 1600 });
}

