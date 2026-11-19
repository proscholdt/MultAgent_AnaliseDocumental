"""O resumo da linha de base (scripts/baseline.py), sem rodar agentes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from baseline import resumir, rodada


def _avaliacao(certos: int) -> dict:
    return {"resumo": {"invencao": {"inventados": 0, "de": 5}, "status_certo": {"acertos": certos, "de": 24},
                       "perguntas_certas": {"acertos": 5, "de": 5}, "inconsistencias_na_fonte": {"acertos": 0, "de": 0}}}


def test_rodada_e_resumo():
    execucao = {"id": "exec-1", "estado": "completa", "iniciada_em": "2026-01-01T10:00:00+00:00",
                "terminada_em": "2026-01-01T10:05:00+00:00", "uso": {"total": {"custo_usd": 0.28, "uso": {"total": 10}}}}
    a, b = rodada(_avaliacao(24), execucao), rodada(_avaliacao(18), {**execucao, "uso": {}})
    assert a["segundos"] == 300 and a["status_certo"] == 1.0 and a["inconsistencias_na_fonte"] is None
    resumo = resumir([a, b])
    assert resumo["status_certo"] == {"media": 0.875, "min": 0.75, "max": 1.0}
    assert resumo["custo_usd"]["media"] == 0.28  # a rodada sem custo não entra na média
