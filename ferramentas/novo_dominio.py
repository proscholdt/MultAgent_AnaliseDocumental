"""Cria um pacote de domínio a partir do modelo (make novo-dominio ID=<id>).

    python ferramentas/novo_dominio.py <id>

Copia dominios/_modelo/ para dominios/<id>/ com o id no lugar. Não sobrescreve um pacote que já
existe. Só a biblioteca padrão: roda com o Python do sistema, de qualquer pasta.
"""
import re
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) != 1 or not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", argv[0]):
        print("Uso: make novo-dominio ID=<id>  (minúsculas, números e _; começa com letra)")
        return 2
    destino = RAIZ / "dominios" / argv[0]
    if destino.exists():
        print(f"{destino} já existe: escolha outro id.")
        return 1
    shutil.copytree(RAIZ / "dominios" / "_modelo", destino)
    yaml = destino / "dominio.yaml"
    yaml.write_bytes(yaml.read_bytes().replace(b"__ID__", argv[0].encode("utf-8")))
    print(f"Criado {destino}. Edite o dominio.yaml e rode `make dominios`; reinicie os sistemas para valer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
