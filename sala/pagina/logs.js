// As abas e os logs do Loki.
// Scripts clássicos, carregados em ordem pelo index.html: o que um declara, os seguintes enxergam.

// abas
const abaSala = document.getElementById('aba-sala'), abaLogs = document.getElementById('aba-logs');
abaSala.onclick = () => { abaSala.classList.add('ativa'); abaLogs.classList.remove('ativa'); document.getElementById('sala').hidden = false; document.getElementById('logs').hidden = true; };
abaLogs.onclick = () => { abaLogs.classList.add('ativa'); abaSala.classList.remove('ativa'); document.getElementById('sala').hidden = true; document.getElementById('logs').hidden = false; carregarLoki(); };

async function carregarLoki() {
  if (document.getElementById('logs').hidden) return;
  try {
    const q = document.getElementById('consulta').value;
    const linhas = await api(`/api/loki?consulta=${encodeURIComponent(q)}&minutos=60&limite=400`);
    document.getElementById('loki').innerHTML = linhas.length
      ? linhas.map((l) => `<span class="s">[${esc(l.servico)}]</span> ${esc(l.linha)}`).join('\n')
      : 'nenhuma linha na última hora para esta consulta.';
  } catch (e) { document.getElementById('loki').textContent = 'Loki indisponível: ' + e.message; }
}
document.getElementById('consulta').onchange = carregarLoki;
setInterval(carregarLoki, 5000);

