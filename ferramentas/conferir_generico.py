"""Confere que o núcleo dos sistemas é genérico: nenhum nome nem texto do código fala de um domínio.

    python ferramentas/conferir_generico.py            (make generico; roda no CI)

O que é de um domínio mora no pacote dele (dominios/<id>/: o YAML, os prompts e o plugin). Este
script lê o código Python de indexer/app, mcp-server/app e agents/app e procura vocabulário de um
domínio nos nomes (variáveis, funções, argumentos, atributos) e nos textos (as mensagens que os
agentes e o usuário leem). Comentários e docstrings ficam de fora: contam a história de uma regra
("na Millet, p. 41...") e não mudam o comportamento.

Uma linha que precisa do termo (o nome do domínio padrão, uma tabela antiga do banco) leva o
comentário `# genérico: <motivo>`. Só a biblioteca padrão: roda com o Python do sistema.
"""
import ast
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SISTEMAS = ("indexer/app", "mcp-server/app", "agents/app")
# O vocabulário das demonstrações financeiras, o domínio de origem do projeto.
TERMOS = re.compile(r"ebitda|receita|controladora|demonstra[cç][aãoõ]|financeir|empresa|lucro|"
                    r"capex|alavancagem|balan[cç]o\s+patrimonial|guidance", re.IGNORECASE)
LIBERADO = "# genérico:"
# A cópia do formato dos pacotes diz qual é o domínio padrão: é o único lugar que o nomeia.
ARQUIVOS_LIBERADOS = {"pacotes.py"}


def _docstrings(tree: ast.AST) -> set[int]:
    """Os ids dos nós de docstring (a primeira expressão de texto de um módulo, classe ou função)."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def achados(path: Path) -> list[tuple[int, str]]:
    texto = path.read_text(encoding="utf-8")
    linhas = texto.splitlines()
    tree = ast.parse(texto)
    docstrings = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        palavras: list[str] = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            palavras = [node.value]
        elif isinstance(node, ast.Name):
            palavras = [node.id]
        elif isinstance(node, ast.Attribute):
            palavras = [node.attr]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            palavras = [node.name]
        elif isinstance(node, ast.arg) or isinstance(node, ast.keyword) and node.arg:
            palavras = [node.arg]
        for palavra in palavras:
            match = TERMOS.search(palavra)
            if not match:
                continue
            linha = getattr(node, "lineno", 0)
            fim = getattr(node, "end_lineno", linha) or linha
            trecho = linhas[linha - 1:fim]
            if any(LIBERADO in parte for parte in trecho) or (linha > 1 and LIBERADO in linhas[linha - 2]):
                continue
            out.append((linha, match.group(0)))
    return sorted(set(out))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    total = 0
    for sistema in SISTEMAS:
        for path in sorted((RAIZ / sistema).rglob("*.py")):
            if path.name in ARQUIVOS_LIBERADOS:
                continue
            for linha, termo in achados(path):
                total += 1
                print(f"{path.relative_to(RAIZ).as_posix()}:{linha}: '{termo}'")
    if total:
        print(f"\n{total} uso(s) de vocabulário de domínio no núcleo. Leve a regra para o pacote do domínio "
              "(dominios/<id>/) ou, se o termo for mesmo necessário, marque a linha com '# genérico: <motivo>'.")
        return 1
    print("Núcleo genérico: nenhum vocabulário de domínio no código de", ", ".join(SISTEMAS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
