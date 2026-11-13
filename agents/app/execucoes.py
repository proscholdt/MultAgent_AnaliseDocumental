"""As execuções e o trace de cada uma, em arquivos na pasta de dados.

    data/execucoes/<id>.json    o estado da execução (sem o token do usuário)
    data/execucoes/<id>.jsonl   o trace: uma linha por passo

O trace é local de propósito. Ele traz argumentos de tools, que contêm texto dos documentos;
mandar isso a um painel de fora é uma decisão de quem opera, não o padrão.
"""
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KINDS = ("livre", "pergunta", "relatorio")
RUNNING = ("na_fila", "em_andamento")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Run:
    id: str
    user_id: str
    tipo: str  # livre | pergunta | relatorio
    pedido: str
    estado: str = "na_fila"  # na_fila | em_andamento | completa | incompleta | falhou | interrompida
    criada_em: str = field(default_factory=now)
    iniciada_em: str | None = None
    terminada_em: str | None = None
    rodadas: int = 0  # quantas vezes o orquestrador rodou (1 + as vezes em que a conferência pediu mais)
    agentes_digest: str = ""  # o retrato dos arquivos de agente que esta execução usou
    mensagem: str = ""  # a mensagem final do orquestrador
    resultado: dict[str, Any] | None = None
    pendencias: list[dict[str, Any]] = field(default_factory=list)
    uso: dict[str, Any] = field(default_factory=dict)
    erro: str | None = None


class Trace:
    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, tipo: str, **fields: Any) -> None:
        line = json.dumps({"em": now(), "tipo": tipo, **fields}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Runs:
    def __init__(self, data_dir: Path) -> None:
        self.folder = data_dir / "execucoes"
        self.folder.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, Run] = {}
        for path in self.folder.glob("*.json"):
            try:
                run = Run(**json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, TypeError):
                continue
            if run.estado in RUNNING:  # o serviço caiu com ela no meio
                run.estado, run.erro = "interrompida", "o serviço foi reiniciado durante a execução"
                self._write(run)
            self._runs[run.id] = run

    def _write(self, run: Run) -> None:
        (self.folder / f"{run.id}.json").write_text(json.dumps(asdict(run), ensure_ascii=False, indent=1),
                                                    encoding="utf-8")

    def create(self, user_id: str, tipo: str, pedido: str) -> Run:
        run = Run(id=f"exec-{uuid.uuid4().hex[:12]}", user_id=user_id, tipo=tipo, pedido=pedido)
        self._runs[run.id] = run
        self._write(run)
        return run

    def save(self, run: Run) -> None:
        self._write(run)

    def get(self, run_id: str, user_id: str) -> Run | None:
        """Só o dono vê a execução: para outro usuário ela não existe."""
        run = self._runs.get(run_id)
        return run if run is not None and run.user_id == user_id else None

    def of(self, user_id: str) -> list[Run]:
        return sorted((run for run in self._runs.values() if run.user_id == user_id),
                      key=lambda run: run.criada_em, reverse=True)

    def trace(self, run_id: str) -> Trace:
        return Trace(self.folder / f"{run_id}.jsonl")
