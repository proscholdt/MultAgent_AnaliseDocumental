"""Valida os pacotes de domínio (dominios/<id>/) e mostra o que cada um define (make dominios).

    uv run python -m app.ferramentas.dominios

Sai com erro se algum pacote for inválido: o mesmo erro que impediria a subida dos sistemas.
"""
import sys

from app import pacotes
from app.regras.campos import conferir


def _plugin(pasta, pacote) -> str:
    """O plugin declarado e os sistemas que têm código nele (dominios/<id>/plugin/<sistema>)."""
    if not pacote.plugin:
        return "nenhum"
    sistemas = sorted(p.stem for p in (pasta / pacote.id / "plugin").glob("*") if not p.name.startswith("_"))
    return f"{pacote.plugin} ({', '.join(sistemas) or 'sem código'})"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    pasta = pacotes.pasta_dos_dominios()
    try:
        todos = pacotes.todos(pasta)
        for pacote in todos.values():
            conferir(pacote)
    except pacotes.PacoteInvalido as error:
        print(f"INVÁLIDO: {error}")
        return 1
    for pacote in todos.values():
        extras = [nome for nome in ("prompts", "intencoes.yaml", "avaliacao") if (pasta / pacote.id / nome).exists()]
        print(f"\n{pacote.id}{' (padrão)' if pacote.id == pacotes.PADRAO else ''}: {pacote.nome}")
        print(f"  entidade: {pacote.entidade.rotulo} · recorte: {pacote.recorte.rotulo} ({pacote.recorte.tipo})"
              f" · plugin: {_plugin(pasta, pacote)}")
        print(f"  metadados: {', '.join(m.nome for m in pacote.metadados)}")
        print(f"  campos: {sum(c.principal for c in pacote.campos)} principais, {len(pacote.campos)} no total"
              f" · categorias: {len(pacote.categorias)} · perguntas: {len(pacote.perguntas)}")
        print(f"  arquivos: {', '.join(extras) or 'só o dominio.yaml'}")
    print(f"\n{len(todos)} pacote(s) válido(s) em {pasta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
