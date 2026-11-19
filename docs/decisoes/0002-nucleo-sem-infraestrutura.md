# 0002 — Regras puras, portas e casos de uso

**Contexto.** As regras do relatório estavam espalhadas: parte em `regras/` (importando o banco e
o indexador), parte dentro do grafo do orquestrador (`grafo.py`, 719 linhas) e parte nos prompts.

**Decisão.**
- `mcp-server/app/dominio` e `app/regras` são puros: sem SQLAlchemy, HTTP nem MCP.
  `app/casos_de_uso` lê a base pelas portas (`app/portas.py`) e aplica as regras.
- `agents/app/dominio` guarda as regras do relatório (intenções, o que é só do relatório,
  p1..pN, enunciado oficial, delegação antes da hora); o grafo só decide, delega e confere.
- Testes de arquitetura (`test_arquitetura.py`, `test_dominio_relatorio.py`) leem os imports e
  quebram se o núcleo voltar a depender de infraestrutura.

**Consequência.** As regras são testadas sem banco nem rede. O custo é uma camada a mais
(`casos_de_uso`) e alguns reexports para compatibilidade.
