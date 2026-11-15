// O login e o começo: listar execuções e acompanhar a mais nova.
// Scripts clássicos, carregados em ordem pelo index.html: o que um declara, os seguintes enxergam.

// login
document.getElementById('form-login').onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api('/api/entrar', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ usuario: document.getElementById('u').value, senha: document.getElementById('s').value }) });
    document.getElementById('login').hidden = true; comecar();
  } catch { document.getElementById('erro-login').textContent = 'Não entrou. Confira usuário e senha.'; }
};

let iniciado = false;
async function comecar() {
  if (iniciado) return; iniciado = true;
  await carregarDocs();
  await listar();
  setInterval(acompanhar, 1000);
  setInterval(listar, 4000);
}
api('/api/sessao').then(comecar).catch(() => {});
