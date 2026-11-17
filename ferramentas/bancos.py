"""Copia os bancos SQLite dos contêineres para bancos/, para abrir no DBeaver (make bancos).

Os SQLite ficam dentro dos volumes Docker, onde o DBeaver não chega. A cópia usa o backup do
próprio SQLite, dentro do contêiner, que leva junto o que ainda está no arquivo -wal: copiar o .db
com o serviço gravando pode dar um arquivo pela metade. Antes de copiar, as cópias antigas saem
(com os -wal e -shm que o DBeaver deixa ao lado): um -wal velho ao lado de um .db novo é um
banco inconsistente. No fim, as tabelas de cada cópia com o número de linhas.

Só a biblioteca padrão: roda com o Python do sistema, de qualquer pasta.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent.parent / "bancos"
BANCOS = [  # contêiner, arquivo dentro dele, nome da cópia
    ("mcp-server-mcp-server-1", "/data/mcp.db", "mcp.db"),
    ("agents-orquestrador-1", "/data/conversas.db", "agentes-conversas.db"),
    *[(f"agents-{nome}-1", "/data/memoria.db", f"memoria-{nome}.db")
      for nome in ("extrator", "validador", "analista", "pesquisador", "redator")],
]
# Só leitura (mode=ro): um banco que ainda não existe não é criado vazio dentro do contêiner.
BACKUP = "import sqlite3; sqlite3.connect('file:{origem}?mode=ro', uri=True).backup(sqlite3.connect('/tmp/copia.db'))"


def docker(*args: str) -> subprocess.CompletedProcess:
    # MSYS_NO_PATHCONV: no Git Bash, /data e /tmp viram caminhos do Windows sem isto.
    return subprocess.run(["docker", *args], check=False, capture_output=True, text=True,
                          env={**os.environ, "MSYS_NO_PATHCONV": "1"})


def no_ar() -> list[str]:
    """Os contêineres que não estão rodando."""
    fora = []
    for conteiner, _, _ in BANCOS:
        estado = docker("inspect", "-f", "{{.State.Running}}", conteiner)
        if estado.stdout.strip() != "true":
            fora.append(conteiner)
    return fora


def limpar() -> None:
    PASTA.mkdir(exist_ok=True)
    presos = []
    for arquivo in PASTA.glob("*.db*"):
        try:
            arquivo.unlink()
        except PermissionError:
            presos.append(arquivo.name)
    if presos:
        sys.exit(f"Arquivos em uso: {', '.join(presos)}. Desconecte essas conexões no DBeaver "
                 "(botão direito > Desconectar) e rode de novo.")


def copiar(conteiner: str, origem: str, destino: str) -> str | None:
    """Copia um banco; devolve o erro, se houver."""
    passos = [("exec", conteiner, "python", "-c", BACKUP.format(origem=origem)),
              ("cp", f"{conteiner}:/tmp/copia.db", str(PASTA / destino)),
              ("exec", conteiner, "rm", "-f", "/tmp/copia.db")]
    for passo in passos:
        feito = docker(*passo)
        if feito.returncode != 0:
            erro = (feito.stderr or feito.stdout).strip().splitlines()[-1]
            if "unable to open database" in erro:
                return "ainda não existe: o serviço cria o banco no primeiro uso"
            return erro
    return None


def resumo(arquivo: Path) -> str:
    with sqlite3.connect(arquivo) as banco:
        tabelas = [nome for (nome,) in banco.execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name")]
        linhas = {tabela: banco.execute(f'select count(*) from "{tabela}"').fetchone()[0] for tabela in tabelas}
    if not linhas:
        return "vazio (sem tabelas: o serviço ainda não gravou nada)"
    return ", ".join(f"{tabela} {total}" for tabela, total in linhas.items())


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if fora := no_ar():
        sys.exit(f"Fora do ar: {', '.join(fora)}. Rode make subir antes.")
    limpar()
    erros = 0
    for conteiner, origem, destino in BANCOS:
        erro = copiar(conteiner, origem, destino)
        if erro and erro.startswith("ainda não existe"):
            print(f"{destino:<25} {erro}")
        elif erro:
            erros += 1
            print(f"{destino:<25} ERRO: {erro}")
        else:
            print(f"{destino:<25} {resumo(PASTA / destino)}")
    print(f"\nCópias em {PASTA}: abra no DBeaver como SQLite (conexões já abertas: botão direito > Atualizar).")
    print("Postgres do indexador, direto: localhost:5432, banco, usuário e senha indexer.")
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
