"""Avaliação: compara o que está na base com o gabarito conferido à mão.

Aqui só há conta, sem rede nem modelo: recebe o gabarito e um retrato da base (o que os
scripts leem pelo servidor MCP) e devolve as métricas. O que se mede:

- status de cada um dos 8 indicadores de cada documento (informado ou não encontrado);
- valor informado, página e valor do ano anterior;
- valor calculado, onde o gabarito traz um;
- invenção: indicador que a empresa não informa e que o sistema gravou como informado;
- alertas automáticos que deviam ter disparado;
- inconsistências do texto da fonte que o validador devia ter achado;
- as cinco perguntas: o que a resposta tem de conter e de que documento tem de citar;
- estabilidade: o quanto dois retratos (duas execuções) coincidem.
"""
from typing import Any

_SCALE = {"R$": 1, "R$ mil": 1_000, "R$ milhões": 1_000_000, "R$ bilhões": 1_000_000_000}
MONEY_TOLERANCE = 0.001  # 0,1%: aceita "9.032 milhões" e "9.032.236 mil" como o mesmo valor
RATIO_TOLERANCE = 0.006  # percentual e múltiplo, em valor absoluto


def in_reais(value: float | None, unit: str | None) -> float | None:
    """Dinheiro em reais; percentual, múltiplo e as unidades de outros domínios (mg/dL) ficam na
    própria escala. Dinheiro sem unidade declarada não tem como ser comparado: None."""
    if value is None:
        return None
    if unit in ("%", "x"):
        return float(value)
    if unit in _SCALE:
        return float(value) * _SCALE[unit]
    return float(value) if unit and not unit.startswith("R$") else None


def same(found: float | None, expected: float | None, unit: str | None) -> bool:
    if found is None or expected is None:
        return False
    if unit in ("%", "x"):
        return abs(found - expected) <= RATIO_TOLERANCE
    return abs(found - expected) <= MONEY_TOLERANCE * max(abs(expected), 1)


def _status(block: dict[str, Any] | None) -> str:
    informed = (block or {}).get("informado")
    if not informed:
        return "sem_status"
    return "informado" if informed["status"] == "encontrado" else "nao_encontrado"


def check_field(expected: dict[str, Any], block: dict[str, Any] | None) -> dict[str, Any]:
    """Um indicador contra o gabarito. Cada conferência vale True, False ou None (não se aplica)."""
    status = _status(block)
    alternative = expected.get("tambem_aceito")
    if alternative and status == alternative["status"] != expected["status"]:
        expected = alternative
    row: dict[str, Any] = {"status_esperado": expected["status"], "status": status,
                           "status_certo": status == expected["status"],
                           "valor_certo": None, "pagina_certa": None, "comparavel_certo": None, "calculado_certo": None,
                           # Invenção: a empresa não informa, e o sistema gravou um valor como informado.
                           "inventado": expected["status"] == "nao_encontrado" and status == "informado"}
    informed = (block or {}).get("informado") or {}
    if expected["status"] == "informado" and ("texto" in expected or "data" in expected):
        # Campo de texto: o valor gravado contém o esperado (sem caixa nem espaços a mais). Data: a
        # mesma, em dd/mm/aaaa.
        shown = " ".join(str(informed.get("valor") or "").split())
        if "data" in expected:
            row["valor_certo"] = status == "informado" and shown == expected["data"]
        else:
            row["valor_certo"] = status == "informado" and expected["texto"].casefold() in shown.casefold()
        row["pagina_certa"] = status == "informado" and informed["origem"]["pagina"] in expected["paginas"]
        row["encontrado"] = shown or None
        return row
    if expected["status"] == "informado":
        unit = expected["unidade"]
        found = in_reais(informed.get("valor_numerico"), informed.get("unidade")) if status == "informado" else None
        row["valor_certo"] = same(found, in_reais(expected["valor"], unit), unit)
        row["pagina_certa"] = status == "informado" and informed["origem"]["pagina"] in expected["paginas"]
        shown, shown_unit = str(informed.get("valor")), informed.get("unidade") or ""
        # "3,32x" e "16,7%" já trazem a unidade no próprio valor.
        row["encontrado"] = (shown if shown.endswith(shown_unit) else f"{shown} {shown_unit}".strip())             if found is not None else None
        if "comparavel" in expected:
            previous = (block or {}).get("comparavel") or {}
            row["comparavel_certo"] = same(in_reais(previous.get("valor_numerico"), previous.get("unidade")),
                                           in_reais(expected["comparavel"], unit), unit)
    if "calculado" in expected:
        computed = (block or {}).get("calculado") or {}
        unit = expected["unidade"]
        row["calculado_certo"] = same(in_reais(computed.get("valor_numerico"), computed.get("unidade")),
                                      in_reais(expected["calculado"], unit), unit)
        row["calculado"] = computed.get("valor")
    return row


def _source_alert_found(alerts: list[dict[str, Any]], expected: dict[str, Any]) -> bool:
    return any(alert["codigo"] == "inconsistencia_na_fonte" and alert["detalhes"].get("pagina") == expected["pagina"]
               and expected["contem"] in alert["detalhes"].get("citacao", "") for alert in alerts)


def _automatic_alert_found(alerts: list[dict[str, Any]], expected: dict[str, Any]) -> bool:
    return any(alert["codigo"] == expected["codigo"] and expected.get("campo") in (None, alert.get("campo"))
               for alert in alerts)


def check_answer(expected: dict[str, Any], answer: dict[str, Any] | None) -> dict[str, Any]:
    if answer is None:
        return {"respondida": False, "certa": False, "faltou": ["a resposta"]}
    text = answer["resposta"].casefold()
    missing = [term for term in expected.get("deve_conter", []) if term.casefold() not in text]
    any_of = expected.get("deve_conter_um_de")
    if any_of and not any(term.casefold() in text for term in any_of):
        missing.append(" ou ".join(any_of))
    sources = " ".join(answer.get("fontes", []))
    missing += [f"fonte em {file_name}" for file_name in expected.get("fontes_de", []) if file_name not in sources]
    return {"respondida": True, "certa": not missing, "faltou": missing, "fontes": len(answer.get("fontes", []))}


def _rate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    applicable = [row for row in rows if row[key] is not None]
    hits = sum(bool(row[key]) for row in applicable)
    return {"acertos": hits, "de": len(applicable), "taxa": round(hits / len(applicable), 3) if applicable else None}


def coverage(gabarito: dict[str, Any], snapshot: dict[str, Any], doc_ids: set[str] | None = None) -> dict[str, Any]:
    """Quais documentos do retrato têm gabarito. O documento é reconhecido pelo doc_id (o hash do
    PDF), não pelo nome: outro PDF chamado Brasal.pdf não é a Brasal do gabarito.

    `doc_ids` limita a avaliação (o relatório de uma empresa só avalia a empresa dele). As perguntas
    comparam as empresas, então só são avaliadas quando o retrato tem exatamente os documentos do
    gabarito, nem mais nem menos: com outra empresa junto, a resposta certa pode ser outra."""
    by_id = {expected["doc_id"]: name for name, expected in gabarito["documentos"].items() if expected.get("doc_id")}
    present = [doc for doc in snapshot["documentos"] if doc_ids is None or doc["doc_id"] in doc_ids]
    with_key = {by_id[doc["doc_id"]]: doc for doc in present if doc["doc_id"] in by_id}
    without = [doc["arquivo"] for doc in present if doc["doc_id"] not in by_id]
    all_snapshot = {doc["doc_id"] for doc in snapshot["documentos"]}
    questions = doc_ids is None and all_snapshot == set(by_id)
    reason = None if questions else ("relatório de uma empresa: as perguntas comparam as empresas" if doc_ids is not None
                                     else "os documentos não são exatamente os do gabarito")
    return {"documentos": with_key, "sem_gabarito": without, "perguntas": questions, "perguntas_motivo": reason}


def evaluate(gabarito: dict[str, Any], snapshot: dict[str, Any], doc_ids: set[str] | None = None) -> dict[str, Any]:
    scope = coverage(gabarito, snapshot, doc_ids)
    documents = scope["documentos"]
    rows, automatic, source = [], [], []
    for file_name, expected_doc in gabarito["documentos"].items():
        if file_name not in documents:
            continue  # documento do gabarito que não está nesta base (ou fora deste relatório)
        doc = documents[file_name]
        indicators = campos_do(doc)
        for campo, expected in (expected_doc.get("campos") or expected_doc.get("indicadores") or {}).items():
            rows.append({"arquivo": file_name, "campo": campo, **check_field(expected, indicators.get(campo))})
        alerts = doc.get("alertas") or []
        for expected in expected_doc.get("alertas_automaticos_esperados", []):
            automatic.append({"arquivo": file_name, "codigo": expected["codigo"], "campo": expected.get("campo"),
                              "disparou": _automatic_alert_found(alerts, expected)})
        for expected in expected_doc.get("inconsistencias_na_fonte_esperadas", []):
            source.append({"arquivo": file_name, "pagina": expected["pagina"], "contem": expected["contem"],
                           "registrada": _source_alert_found(alerts, expected)})

    answers = {answer["pergunta_id"]: answer for answer in snapshot.get("respostas", [])}
    questions = {question_id: check_answer(expected, answers.get(question_id))
                 for question_id, expected in gabarito["perguntas"].items()
                 if not question_id.startswith("_") and scope["perguntas"]}
    not_informed = [row for row in rows if row["status_esperado"] == "nao_encontrado"]
    invented = [row for row in not_informed if row["inventado"]]
    return {
        "avaliados": sorted(documents), "sem_gabarito": scope["sem_gabarito"],
        "perguntas_nao_avaliadas": scope["perguntas_motivo"],
        "indicadores": rows,
        "resumo": {
            "status_certo": _rate(rows, "status_certo"),
            "valor_informado_certo": _rate(rows, "valor_certo"),
            "pagina_certa": _rate(rows, "pagina_certa"),
            "ano_anterior_certo": _rate(rows, "comparavel_certo"),
            "valor_calculado_certo": _rate(rows, "calculado_certo"),
            # A taxa que mais importa: de tudo o que a empresa NÃO informa, quanto o sistema inventou.
            "invencao": {"inventados": len(invented), "de": len(not_informed),
                         "taxa": round(len(invented) / len(not_informed), 3) if not_informed else None},
            "alertas_automaticos": {"acertos": sum(a["disparou"] for a in automatic), "de": len(automatic)},
            "inconsistencias_na_fonte": {"acertos": sum(s["registrada"] for s in source), "de": len(source)},
            "perguntas_certas": {"acertos": sum(q["certa"] for q in questions.values()), "de": len(questions)},
            "citacoes_validas": snapshot.get("citacoes"),
        },
        "alertas_automaticos": automatic,
        "inconsistencias_na_fonte": source,
        "perguntas": questions,
    }


def campos_do(doc: dict[str, Any]) -> dict[str, Any]:
    """Os campos da extração de um documento do retrato. Retrato de antes dos nomes genéricos: a
    chave era `indicadores`."""
    extracao = doc.get("extracao") or {}
    return extracao.get("campos") or extracao.get("indicadores") or {}


def stability(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Quanto duas execuções coincidem: status e valor (informado ou calculado) de cada indicador."""
    def values(snapshot: dict[str, Any]) -> dict[tuple[str, str], tuple]:
        result = {}
        for doc in snapshot["documentos"]:
            for campo, block in campos_do(doc).items():
                informed, computed = block.get("informado") or {}, block.get("calculado") or {}
                result[(doc["arquivo"], campo)] = (_status(block), informed.get("valor"), computed.get("valor"))
        return result

    left, right = values(first), values(second)
    keys = sorted(set(left) | set(right))
    different = [{"arquivo": key[0], "campo": key[1], "antes": left.get(key), "depois": right.get(key)}
                 for key in keys if left.get(key) != right.get(key)]
    return {"campos": len(keys), "iguais": len(keys) - len(different), "diferentes": different,
            "taxa": round((len(keys) - len(different)) / len(keys), 3) if keys else None}


def _mark(value: bool | None) -> str:
    return "—" if value is None else "sim" if value else "**NÃO**"


def render(report: dict[str, Any]) -> str:
    """As métricas em Markdown, para o README e para a pasta de entrega."""
    summary = report["resumo"]

    def line(label: str, rate: dict[str, Any]) -> str:
        return f"| {label} | {rate['acertos']} de {rate['de']} |"

    out = ["| Medida | Resultado |", "|---|---|",
           line("Status certo (informado ou não encontrado)", summary["status_certo"]),
           line("Valor informado certo", summary["valor_informado_certo"]),
           line("Página certa", summary["pagina_certa"]),
           line("Valor do ano anterior certo", summary["ano_anterior_certo"]),
           line("Valor calculado certo", summary["valor_calculado_certo"]),
           (f"| Invenção (não informado gravado como informado) | {summary['invencao']['inventados']} de "
            f"{summary['invencao']['de']} |"),
           line("Alertas automáticos esperados que dispararam", summary["alertas_automaticos"]),
           line("Inconsistências do texto da fonte registradas", summary["inconsistencias_na_fonte"]),
           line("Perguntas com o conteúdo e a fonte esperados", summary["perguntas_certas"])]
    if summary.get("citacoes_validas"):
        citations = summary["citacoes_validas"]
        out.append(f"| Citações que existem literalmente no trecho citado | {citations['validas']} de {citations['total']} |")
    out += ["", "| Documento | Indicador | Esperado | Obtido | Valor | Página | Ano anterior | Calculado |",
            "|---|---|---|---|---|---|---|---|"]
    for row in report["indicadores"]:
        got = row["status"] + (f" ({row['encontrado']})" if row.get("encontrado") else "")
        out.append(f"| {row['arquivo']} | {row['campo']} | {row['status_esperado']} | "
                   f"{got if row['status_certo'] else '**' + got + '**'} | {_mark(row['valor_certo'])} | "
                   f"{_mark(row['pagina_certa'])} | {_mark(row['comparavel_certo'])} | {_mark(row['calculado_certo'])} |")
    out += ["", "| Pergunta | Certa | O que faltou |", "|---|---|---|"]
    for question_id, result in report["perguntas"].items():
        out.append(f"| {question_id} | {_mark(result['certa'])} | {'; '.join(result['faltou']) or '—'} |")
    return "\n".join(out)
