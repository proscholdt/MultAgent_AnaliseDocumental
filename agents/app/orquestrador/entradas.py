"""O que o orquestrador recebe a cada rodada: o pedido, a conversa até aqui, os relatos da onda e a equipe."""
import dataclasses
import json
from typing import Any

from app.nucleo.agentes import AgentFile
from app.orquestrador.estado import Estado
from app.orquestrador.execucoes import Run

_TURNS_IN_PROMPT = 6

_TURN_CHARS = 900

def conversation_so_far(turns: list[dict]) -> str:
    """Os turnos anteriores da conversa, para o orquestrador: o pedido e o que foi respondido."""
    if not turns:
        return ""
    lines = ["## A conversa até aqui (turnos anteriores, do mais antigo ao mais novo)"]
    for turn in turns[-_TURNS_IN_PROMPT:]:
        answer = turn.get("resposta") or ""
        if len(answer) > _TURN_CHARS:
            answer = answer[:_TURN_CHARS] + "…"
        lines += [f"- Pedido: {turn['pedido']}", f"  Resposta: {answer or '(sem resposta)'} [{turn.get('estado')}]"]
    return "\n".join(lines)

def first_input(run: Run, turns: list[dict] | None = None) -> str:
    if run.tipo == "relatorio":
        text = ("Produza o relatório consolidado completo dos documentos do usuário: extração, revisão das "
                "fontes, análise qualitativa, as cinco perguntas, os resumos e a montagem do relatório.")
    elif run.tipo == "relatorio_empresa":
        text = (f"Produza o relatório de UMA empresa, como o usuário pediu: «{run.pedido}». Empresa: "
                f"{run.doc_id or 'diga em `empresa` o doc_id dela'}. Use `pendencias` com esse doc_id.")
    elif run.tipo == "pergunta":
        text = f"Responda à pergunta do usuário, com as fontes. Pergunta: «{run.pedido}»"
    elif run.tipo == "auto":
        text = f"Mensagem do usuário (diga em `intencao` o que ela pede): «{run.pedido}»"
    else:
        text = run.pedido
    history = conversation_so_far(turns or [])
    return f"{history}\n\n## Pedido atual\n{text}" if history else text

def retry_input(run: Run, pending: list[dict[str, Any]], reason: str | None, turns: list[dict] | None = None) -> str:
    lines = [first_input(run, turns), "", "Uma rodada anterior deste pedido terminou sem concluir."]
    if reason:
        lines.append(f"Motivo: {reason}.")
    if pending:
        lines += ["O que ainda falta, segundo a base:", json.dumps(pending, ensure_ascii=False, indent=1)]
    lines.append("O que já foi feito está gravado e não precisa ser refeito. Resolva só o que falta.")
    return "\n".join(lines)

def reports_input(reports: list[dict]) -> str:
    return ("Relatos da onda que terminou (montados pelo sistema, não pelos especialistas):\n"
            + json.dumps(reports, ensure_ascii=False, indent=1)
            + "\n\nDecida a próxima onda. Chame `pendencias` se o pedido envolve o relatório.")

def with_team(spec: AgentFile, catalog: dict[str, Any]) -> AgentFile:
    """O prompt do orquestrador mais a lista dos especialistas no ar, com a descrição de cada um
    (vem do arquivo do agente, pelo catálogo A2A)."""
    team = "\n".join(f"- `{entry['nome']}`: {entry['descricao']}" for entry in catalog["agentes"])
    return dataclasses.replace(spec, prompt=f"{spec.prompt}\n\n## Especialistas disponíveis\n\n{team}")

def turn_answer(run: Run, state: Estado) -> str:
    """O que este turno respondeu, para os turnos seguintes da conversa lembrarem."""
    answers = (state.get("resultado") or {}).get("respostas") or []
    if answers:
        return " ".join(answer["resposta"] for answer in answers)
    if run.tipo == "relatorio" and state.get("completo"):
        return "Relatório consolidado montado."
    if run.tipo == "relatorio_empresa" and state.get("completo"):
        return f"Relatório da empresa {run.doc_id} montado."
    return state.get("mensagem") or ""
