# 0001 — O modelo aponta, o servidor MCP lê e confere

**Contexto.** Modelos pequenos (`gpt-5.4-nano`) erram números e citações. Corrigir no prompt não
garante nada: um trecho do PDF pode mandar o modelo fazer outra coisa.

**Decisão.** Nenhuma gravação passa sem conferência no servidor MCP. O extrator indica chunk,
linha e coluna, e o servidor lê a célula; "não encontrado" só vale depois de o servidor procurar
os termos; texto só grava com citação literal do chunk. A política (`policy.yaml`) dá a cada
agente só as tools dele, e cada tool que grava tem um dono só.

**Consequência.** Mais código no servidor e um salto de rede a mais. Em troca, mudar prompt ou
modelo não abre brecha, e cada erro achado vira um portão com teste.
