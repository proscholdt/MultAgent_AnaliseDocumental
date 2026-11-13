"""A memória do especialista: o que volta para ele e o quanto."""
from app.memoria import Memory, compact, split_runs


def run(prompt: str, result_size: int = 2000) -> list[dict]:
    """Uma chamada guardada: pedido, raciocínio, uma tool com resultado longo e a resposta."""
    return [
        {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": prompt}]},
        {"kind": "response", "parts": [{"part_kind": "thinking", "content": "Pensando no chunk 11."},
                                       {"part_kind": "tool-call", "tool_name": "ler_chunk", "args": {"chunk_index": 11},
                                        "tool_call_id": f"c-{prompt}"}]},
        {"kind": "request", "parts": [{"part_kind": "tool-return", "tool_name": "ler_chunk", "outcome": "success",
                                       "tool_call_id": f"c-{prompt}", "content": "| Receita | 1.325.059 |" * (result_size // 26)}]},
        {"kind": "response", "parts": [{"part_kind": "text", "content": f"Feito: {prompt}"}]},
    ]


def test_previous_calls_come_back_shortened():
    history = compact(split_runs(run("primeira") + run("segunda")), max_tokens=100_000)
    kinds = [part["part_kind"] for message in history for part in message["parts"]]
    assert "thinking" not in kinds  # o raciocínio está no trace, não precisa voltar
    returns = [part for message in history for part in message["parts"] if part["part_kind"] == "tool-return"]
    assert all(part["content"].startswith("[resultado de ler_chunk (ok) encurtado na memória") for part in returns)
    assert "Se precisar do conteúdo, chame a tool de novo." in returns[0]["content"]
    # O que o agente pediu, fez e respondeu continua inteiro.
    assert [p["content"] for m in history for p in m["parts"] if p["part_kind"] == "user-prompt"] == ["primeira", "segunda"]
    assert {"chunk_index": 11} in [p.get("args") for m in history for p in m["parts"]]


def test_short_results_stay_whole_and_the_oldest_calls_leave_above_the_ceiling():
    small = compact(split_runs(run("curta", result_size=100)), max_tokens=100_000)
    assert "| Receita |" in next(p["content"] for m in small for p in m["parts"] if p["part_kind"] == "tool-return")
    many = split_runs(run("1") + run("2") + run("3") + run("4"))
    capped = compact(many, max_tokens=300)
    prompts = [p["content"] for m in capped for p in m["parts"] if p["part_kind"] == "user-prompt"]
    assert prompts[-1] == "4" and len(prompts) < 4  # as mais antigas saíram; a mais recente fica sempre
    assert compact(split_runs(run("só")), max_tokens=1) != []  # mesmo acima do teto, a última fica


def test_memory_is_kept_per_user_conversation_agent_and_document(tmp_path):
    memory = Memory(tmp_path / "memoria.db")
    memory.append("ana", "conv-1", "extrator", "doc-a", [], run("primeira"))
    previous = memory.load("ana", "conv-1", "extrator", "doc-a")
    memory.append("ana", "conv-1", "extrator", "doc-a", previous, run("segunda"))
    stored = memory.load("ana", "conv-1", "extrator", "doc-a")
    assert len(split_runs(stored)) == 2
    # O guardado é o bruto: o encurtamento é só na leitura, e não se acumula.
    assert any(p.get("part_kind") == "thinking" for m in stored for p in m["parts"])
    for key in (("bruno", "conv-1", "extrator", "doc-a"), ("ana", "conv-2", "extrator", "doc-a"),
                ("ana", "conv-1", "analista", "doc-a"), ("ana", "conv-1", "extrator", "doc-b")):
        assert memory.load(*key) == [], key
    reopened = Memory(tmp_path / "memoria.db")  # sobrevive ao reinício do serviço
    assert len(split_runs(reopened.load("ana", "conv-1", "extrator", "doc-a"))) == 2
