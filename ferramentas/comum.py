"""Copia o código comum (comum/) para cada sistema, ou confere que as cópias estão iguais.

    python ferramentas/comum.py            copia (make comum)
    python ferramentas/comum.py --conferir sai com erro se alguma cópia divergir (CI)

Só a biblioteca padrão: roda com o Python do sistema, de qualquer pasta.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
COPIAS = {
    "telemetria.py": ["indexer/app/telemetria.py", "mcp-server/app/telemetria.py", "agents/app/telemetria.py"],
    "pacotes.py": ["indexer/app/pacotes.py", "mcp-server/app/pacotes.py", "agents/app/pacotes.py"],
}


def main(argv: list[str]) -> int:
    conferir = "--conferir" in argv
    divergentes = []
    for nome, destinos in COPIAS.items():
        fonte = (RAIZ / "comum" / nome).read_bytes()
        for destino in destinos:
            caminho = RAIZ / destino
            if caminho.exists() and caminho.read_bytes() == fonte:
                continue
            if conferir:
                divergentes.append(destino)
            else:
                caminho.write_bytes(fonte)
                print(f"copiado  comum/{nome} -> {destino}")
    if divergentes:
        print("Cópias diferentes de comum/ (edite em comum/ e rode `make comum`):")
        for destino in divergentes:
            print(f"  {destino}")
        return 1
    if conferir:
        print("comum/: todas as cópias iguais.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
