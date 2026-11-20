"""Extração estruturada: as tools pelas quais um valor entra na base.

Cada uma é um portão. A regra é a do sistema 1, aplicada aqui: nada que o modelo diz entra sem
uma conferência que não depende dele.

- Valor de tabela: o agente aponta o chunk, a linha e a coluna; o servidor lê a célula. O
  agente não digita o número.
- Valor de texto: o agente copia o trecho e diz o valor; o trecho tem de existir no chunk e
  conter aquele número, inteiro.
- "Não encontrado": o servidor procura os termos do campo no documento; se aparecem, o agente
  tem de dizer por que aquelas páginas não servem.

O que é calculado (variação, margem, EBITDA pela DRE) não entra por aqui: é o servidor que
calcula, ao fechar a extração.
"""
import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from app.dados.modelos import StoredValue
from app.dominio.documento import IndexedDocument
from app.dominio.periodo import PADRAO as PADRAO_PERIODO
from app.dominio.periodo import Periodo
from app.governanca.catalogo import Args, Call, tool
from app.governanca.erros import ToolRejected
from app.regras.campos import DOMINIO, FIELDS, MAIN_FIELDS, Campo, CampoPrincipal, atual
from app.regras.evidencias import occurrences, statement_tables
from app.regras.evidencias import squash_spaces as _squash
from app.regras.extracao_enxuta import compact
from app.regras.formulas import AlertDraft, Extraction, Value
from app.regras.numeros import (
    contains_number,
    number_tokens,
    parse_number,
    unit_in_text,
    unit_of_number,
)
from app.regras.tabelas import parse_table, read_cell, sibling_column
from app.regras.texto import check_script, plain

_MINUS_BEFORE = r"[-−–]\s*R\$\s*"  # "-R$5.673 milhões": o sinal vem antes da moeda, não do número


def _por_periodo() -> bool:
    """O domínio da chamada recorta por período contábil (com comparável), como as demonstrações?"""
    return atual().pacote.recorte.tipo == "periodo"


def periods(document: IndexedDocument) -> tuple[str, str | None]:
    """Período de referência do documento e o comparável (o mesmo período do ano anterior), nos
    rótulos canônicos de `Periodo` ("2022", "3T2025", "9M2025"). Num domínio sem período contábil
    (a data de um exame), a referência é o recorte do documento, sem comparável."""
    if not _por_periodo():
        return (document.period or "documento").strip(), None
    reference = Periodo.parse(document.period)
    if reference is None:
        return (document.period or "").strip(), None
    return reference.rotulo, reference.comparavel.rotulo


def to_value(row: StoredValue) -> Value:
    return Value(
        campo=row.campo, periodo=row.periodo, medida=row.medida, status=row.status,
        valor=Decimal(row.valor) if row.valor is not None else None, texto=row.texto_valor, tipo=row.tipo,
        unidade=row.unidade, unidade_origem=row.unidade_origem, escopo=row.escopo, origem=row.origem,
        pagina=row.pagina, pagina_final=row.pagina_final, chunk_index=row.chunk_index, secao=row.secao,
        linha=row.linha, coluna=row.coluna, trecho=row.trecho, motivo=row.motivo, evidencia=row.evidencia,
        nota=row.nota, alternativa_consolidada=row.alternativa_consolidada,
        periodo_confirmado=row.periodo_confirmado)


def _origem(document: Any, papel: str) -> Any:
    """De onde veio o metadado com aquele papel (entidade, recorte): o indexador guarda pelo nome do
    metadado no pacote (empresa, periodo); documentos de antes dos pacotes, pelo nome antigo."""
    metadado = DOMINIO.metadado(papel)
    antigo = {"entidade": "company", "recorte": "period"}[papel]
    source = document.metadata_source or {}
    return source.get(metadado.nome) if metadado and metadado.nome in source else source.get(antigo)


class SalvarIndicador(Args):
    doc_id: str = Field(description="Documento.")
    campo: Campo = Field(description="Qual campo. Os principais são os do relatório; os demais (quando há) são "
                         "valores de apoio, usados pelo servidor nos cálculos.")
    periodo: str | None = Field(default=None, pattern=PADRAO_PERIODO, description="Período do valor, nos domínios "
                                "com período contábil: o ano com 4 dígitos (ex.: 2022) num documento anual; o trimestre "
                                "(3T2025) ou o acumulado do ano (9M2025) num trimestral. Nos outros, null: vale a data "
                                "do documento.")
    medida: Literal["valor", "variacao_informada"] = Field(
        default="valor", description="'valor': o indicador em si. 'variacao_informada': a variação percentual "
        "contra o período anterior que a própria empresa escreveu (o servidor também calcula a variação e "
        "compara as duas).")
    origem: Literal["tabela", "texto"] = Field(description="De onde o valor vem: célula de tabela ou texto corrido.")
    chunk_index: int = Field(ge=0, description="Chunk onde o valor está.")
    rotulo_linha: str | None = Field(default=None, description="origem='tabela': rótulo da linha, copiado da "
                                     "primeira coluna da tabela.")
    rotulo_coluna: str | None = Field(default=None, description="origem='tabela': cabeçalho da coluna, copiado "
                                      "da tabela (ex.: 'Consolidado - 2022').")
    ocorrencia_linha: int | None = Field(default=None, ge=1, description="Só se o rótulo da linha se repete na "
                                         "tabela: qual ocorrência (1 = a primeira). Senão, null.")
    ocorrencia_coluna: int | None = Field(default=None, ge=1, description="Só se o cabeçalho da coluna se repete: "
                                          "qual ocorrência. Senão, null.")
    trecho: str | None = Field(default=None, description="origem='texto': a frase do documento que traz o valor, "
                               "copiada literalmente do chunk.")
    valor: str | None = Field(default=None, description="origem='texto': o número como está escrito no trecho "
                              "(ex.: '9.032', '3,32x', '23%'). Em origem='tabela' fica null: o servidor lê a célula.")
    nota: str | None = Field(default=None, max_length=300, description="Observação curta sobre o valor, se "
                             "necessária (ex.: 'EBITDA da distribuidora, não do consolidado'). Senão, null.")


_NUMBER_AND_UNIT = re.compile(r"^\s*(-?\d[\d.,]*)\s*(\S.*?)?\s*$")


def _plain_unit(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _cell_number(text: str, info: Any, row: str, column: str) -> Any:
    """O número da célula. Num campo `numero` (o resultado de um exame), a célula pode trazer a
    unidade junto ("92 mg/dL"): ela tem de ser a do pacote. Outra unidade não é convertida: é
    recusada, e o agente registra o que o documento diz."""
    number = parse_number(text)
    if number is not None or info.kind != "numero":
        return number
    match = _NUMBER_AND_UNIT.match(text or "")
    if match is None:
        return None
    unit = match.group(2) or ""
    if unit and info.unit and _plain_unit(unit) != _plain_unit(info.unit):
        raise ToolRejected(f"A célula ({row} × {column}) traz o resultado em '{unit}', e {info.label} é registrado em "
                           f"{info.unit}. O servidor não converte unidades: registre o resultado na análise, com a "
                           "citação, e este campo como não encontrado.")
    return parse_number(match.group(1))


def _from_table(args: SalvarIndicador, chunk: dict[str, Any]) -> dict[str, Any]:
    if chunk["chunk_type"] != "table":
        raise ToolRejected(f"O chunk {args.chunk_index} não é uma tabela. Para valor em texto corrido, use "
                           "origem='texto' com trecho e valor.")
    if not args.rotulo_linha or not args.rotulo_coluna:
        raise ToolRejected("Com origem='tabela', informe rotulo_linha e rotulo_coluna.")
    if args.valor is not None or args.trecho is not None:
        raise ToolRejected("Com origem='tabela' não se informa valor nem trecho: o servidor lê a célula.")
    cell = read_cell(chunk["text"], args.rotulo_linha, args.rotulo_coluna, row_occurrence=args.ocorrencia_linha,
                     column_occurrence=args.ocorrencia_coluna)
    number = _cell_number(cell.text, FIELDS[args.campo], cell.row_label, cell.column_label)
    if number is None:
        raise ToolRejected(f"A célula ({cell.row_label} × {cell.column_label}) não tem um número: está escrito "
                           f"'{cell.text or 'vazio'}'. Escolha outra linha ou coluna, ou registre o campo como não "
                           "encontrado.")
    confirmed, refusal = Periodo.parse(args.periodo).confere_coluna(cell.column_label) if _por_periodo() \
        else (True, None)
    if refusal:
        raise ToolRejected(refusal)
    table = parse_table(chunk["text"])
    plugin = atual().plugin
    scope = plugin.escopo_da_coluna(cell.column_label)
    return {
        "number": number, "trecho": f"{cell.header_text}\n{cell.row_text}", "linha": cell.row_label,
        "coluna": cell.column_label, "escopo": scope, "periodo_confirmado": confirmed,
        "alternativa_consolidada": plugin.escopo_alternativo(scope, table.header),
        "unidade": chunk.get("unit"), "unidade_origem": chunk.get("unit_source"),
        "table": table, "row_index": cell.row_index, "column_index": cell.column_index}


def _from_text(args: SalvarIndicador, chunk: dict[str, Any]) -> dict[str, Any]:
    if chunk["chunk_type"] == "table":
        # Citar a linha de uma tabela como "trecho" escaparia da leitura de célula: o número estaria
        # no trecho, mas a coluna (o ano, o escopo) seria escolha do modelo. Na 6ª execução real o
        # lucro e o capex da Light entraram assim.
        raise ToolRejected(f"O chunk {args.chunk_index} é uma tabela: use origem='tabela', com rotulo_linha e "
                           "rotulo_coluna, e o servidor lê a célula. Se o cabeçalho da coluna (ou o rótulo da linha) "
                           "se repete, informe ocorrencia_coluna (ou ocorrencia_linha).")
    if not args.trecho or not args.valor:
        raise ToolRejected("Com origem='texto', informe trecho (a frase copiada do chunk) e valor.")
    snippet = _squash(args.trecho)
    if len(snippet) < 12:
        raise ToolRejected("O trecho é curto demais: copie a frase inteira que traz o valor.")
    if snippet not in _squash(chunk["text"]):
        raise ToolRejected(f"O trecho não está no chunk {args.chunk_index}. Copie o texto literalmente, sem "
                           "resumir nem corrigir.")
    number = parse_number(args.valor)
    tokens = number_tokens(args.valor)
    if number is None or len(tokens) != 1:
        raise ToolRejected(f"'{args.valor}' não é um número só. Informe o valor como está escrito no trecho.")
    if not contains_number(snippet, tokens[0]):
        raise ToolRejected(f"O número {tokens[0]} não está no trecho citado.")
    unit = unit_of_number(snippet, tokens[0]) or unit_in_text(snippet)
    said = atual().plugin.ler_trecho(args.campo, snippet, tokens[0])
    return {
        "number": number, "trecho": snippet, "linha": None, "coluna": None, "escopo": said.get("escopo"),
        # Só o recorte por período tem o que conferir no trecho (o ano). Num laudo, o valor é do
        # documento, e a data dele não costuma estar na frase do resultado.
        "periodo_confirmado": args.periodo in number_tokens(snippet) if _por_periodo() else True,
        "alternativa_consolidada": False,
        "unidade": unit or chunk.get("unit"), "unidade_origem": "trecho" if unit else chunk.get("unit_source"),
        # "-R$5.673 milhões" (e o que o plugin disser, como "prejuízo de R$ 5.672 milhões"): o número
        # vem positivo, e o valor é negativo.
        "loss": bool(said.get("negativo"))
        or bool(re.search(_MINUS_BEFORE + re.escape(tokens[0]) + r"(?![\d.,]*\d)", snippet))}


@tool("salvar_indicador", "extracao", """
    Grava um indicador de um documento, depois de conferir a origem. Valor de tabela: informe
    chunk_index, rotulo_linha e rotulo_coluna, e o servidor lê a célula (não informe o número).
    Valor de texto: informe chunk_index, o trecho copiado do documento e o valor como está escrito.
    Se a conferência falhar, o erro diz o que corrigir. Salvar de novo o mesmo campo e período
    substitui o anterior. Em valor de tabela do ano de referência, o servidor grava também o valor
    do ano anterior, lido da coluna irmã da mesma linha, quando ela existe.""")
async def salvar_indicador(call: Call, args: SalvarIndicador) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    reference, comparable = periods(document)
    if _por_periodo():
        args = args.model_copy(update={"periodo": Periodo.parse(args.periodo or reference).rotulo})  # 3T25 → 3T2025
    else:
        args = args.model_copy(update={"periodo": reference})
    if comparable is not None and args.periodo not in (reference, comparable):
        raise ToolRejected(f"Este documento é de {reference}. São aceitos valores de {reference} e, para "
                           f"comparação, de {comparable}.")
    chunk = document.chunk(args.chunk_index)
    if chunk is None:
        raise ToolRejected(f"O documento tem chunks de 0 a {len(document.chunks) - 1}; não existe o "
                           f"{args.chunk_index}.")
    info = FIELDS[args.campo]
    if args.medida == "variacao_informada" and (not info.main or not comparable):
        raise ToolRejected("A variação informada só se aplica aos campos principais, em domínio com período "
                           "comparável.")
    if info.kind in ("texto", "data"):
        raise ToolRejected(f"{info.label} é um campo de {info.kind}: ainda não há leitura conferida para ele. "
                           "Registre o que o documento diz na análise (salvar_item_analise).")
    found = _from_table(args, chunk) if args.origem == "tabela" else _from_text(args, chunk)
    number = found["number"]
    kind = "percentual" if args.medida == "variacao_informada" else info.kind
    if kind == "valor" and number.kind != "valor":
        raise ToolRejected(f"{info.label} é um valor em dinheiro, e o número citado ({number.text}) é um "
                           f"{'percentual' if number.kind == 'percentual' else 'múltiplo'}.")
    if (kind, number.kind) in (("multiplo", "percentual"), ("percentual", "multiplo")):
        raise ToolRejected(f"{info.label} é um {'múltiplo (ex.: 3,32x)' if kind == 'multiplo' else 'percentual'}, e o "
                           f"número citado ({number.text}) é um {'percentual' if kind == 'multiplo' else 'múltiplo'}: "
                           "é outro índice. Se a empresa não informa este, use salvar_nao_encontrado.")
    if kind == "percentual" and number.kind != "percentual" and (
            args.origem == "texto" or "%" not in f"{found['linha']} {found['coluna']}"):
        raise ToolRejected(f"{info.label} é um percentual, e o número citado ({number.text}) não traz '%': é outro "
                           "valor. Não calcule a margem nem a variação: o servidor calcula. Se a empresa não escreve "
                           "o percentual, use salvar_nao_encontrado.")
    if kind == "numero" and number.kind == "multiplo":
        raise ToolRejected(f"{info.label} é um número{f' em {info.unit}' if info.unit else ''}, e o citado "
                           f"({number.text}) é um múltiplo: é outro valor.")
    if kind == "multiplo" and number.kind != "multiplo" and args.origem == "texto" and "vez" not in plain(found["trecho"]):
        raise ToolRejected(f"{info.label} é um múltiplo (ex.: 3,32x), e o número citado ({number.text}) não é: se a "
                           "empresa não escreve o múltiplo, use salvar_nao_encontrado.")
    atual().plugin.conferir_fonte(args, info, found, chunk, document)
    if args.nota:
        check_script(args.nota, "A nota")
    replaced = atual().plugin.campos_que_se_excluem(args.campo)
    if args.origem == "tabela" and args.medida == "valor":
        await _check_cell_is_free(call, args, document, found, replaced)
    for other in replaced:
        await call.deps.store.drop_values(call.caller.user_id, args.doc_id, other)
    value = -abs(number.value) if found.get("loss") else number.value
    # Número de outro domínio: a unidade é a do pacote (mg/dL), não a monetária da página.
    unit = {"percentual": "%", "multiplo": "x"}.get(kind, info.unit if kind == "numero" else found["unidade"])
    row = StoredValue(
        user_id=call.caller.user_id, doc_id=args.doc_id, fingerprint=document.fingerprint,
        run_id=call.caller.run_id, agent=call.caller.agent, campo=args.campo, periodo=args.periodo,
        medida=args.medida, status="encontrado", origem=args.origem, chunk_index=args.chunk_index,
        pagina=chunk["page_start"], pagina_final=chunk["page_end"],
        secao=" > ".join(chunk.get("section_path") or []), linha=found["linha"], coluna=found["coluna"],
        trecho=found["trecho"], texto_valor=number.text, valor=str(value), tipo=kind, unidade=unit,
        unidade_origem=None if kind != "valor" else found["unidade_origem"], escopo=found["escopo"],
        alternativa_consolidada=found["alternativa_consolidada"], periodo_confirmado=found["periodo_confirmado"],
        nota=args.nota)
    await call.deps.store.save_value(row)

    warnings = []
    previous = None
    if args.origem == "tabela" and args.medida == "valor" and comparable and args.periodo == reference:
        previous = await _save_comparable(call, row, found, reference, comparable, kind)
        if previous is None:
            warnings.append(f"Não achei nesta tabela a coluna de {comparable} para a mesma linha. Sem o valor de "
                            f"{comparable} a variação não é calculada: se ele existir em outro lugar, salve-o com "
                            f"periodo='{comparable}'.")
    if kind == "valor" and unit is None:
        warnings.append("A fonte não declara a unidade deste valor (mil, milhões).")
    if not found["periodo_confirmado"]:
        warnings.append(f"A fonte citada não escreve o período {args.periodo}: confira se o valor é desse período.")
    warnings += atual().plugin.avisos_do_valor(found)
    return {"salvo": {"campo": args.campo, "periodo": args.periodo, "medida": args.medida, "valor": number.text,
                      "valor_numerico": float(value), "unidade": unit, "escopo": found["escopo"],
                      "pagina": chunk["page_start"], "linha": found["linha"], "coluna": found["coluna"]},
            "ano_anterior_gravado_pelo_servidor": previous, "avisos": warnings}


async def _check_cell_is_free(call: Call, args: SalvarIndicador, document: IndexedDocument, found: dict[str, Any],
                              replaced: tuple[str, ...]) -> None:
    """Uma célula é um valor só. Gravada em dois campos, uma soma a contaria duas vezes (visto
    com a linha única 'Imobilizado e intangível', gravada como imobilizado e como intangível)."""
    stored = await call.deps.store.values(call.caller.user_id, args.doc_id, document.fingerprint)
    twin = next((row for row in stored if row.status == "encontrado" and row.medida == "valor"
                 and row.campo != args.campo and row.campo not in replaced and row.periodo == args.periodo
                 and row.chunk_index == args.chunk_index and row.trecho == found["trecho"]
                 and row.coluna == found["coluna"]), None)
    if twin is None:
        return
    hint = atual().plugin.dica_celula_repetida(args.campo, twin.campo) or \
        f"Se o campo certo é '{args.campo}', grave antes '{twin.campo}' de novo, apontando a célula correta."
    raise ToolRejected(f"Esta célula já está gravada como '{twin.campo}'. Uma célula é um valor só: em dois campos, "
                       f"ela seria contada duas vezes. {hint}")


async def _save_comparable(call: Call, row: StoredValue, found: dict[str, Any], reference: str, comparable: str,
                           kind: str) -> dict[str, Any] | None:
    """Grava o valor do ano anterior, lido da coluna irmã da mesma linha. O agente não precisa
    apontar duas vezes, e a variação deixa de depender de ele lembrar."""
    table = found["table"]
    # A coluna irmã troca o ano ("30/09/2025" -> "30/09/2024", "2022" -> "2021"): vale para o anual e
    # para o trimestral escrito com o ano inteiro.
    current, previous = Periodo.parse(reference), Periodo.parse(comparable)
    if current is None or previous is None:
        return None
    column = sibling_column(table.header, found["column_index"], str(current.ano), str(previous.ano))
    if column is None:
        return None
    number = parse_number(table.rows[found["row_index"]][column])
    if number is None or (kind == "valor" and number.kind != "valor"):
        return None
    label = table.header[column]
    await call.deps.store.save_value(StoredValue(
        user_id=row.user_id, doc_id=row.doc_id, fingerprint=row.fingerprint, run_id=row.run_id, agent=row.agent,
        campo=row.campo, periodo=comparable, medida="valor", status="encontrado", origem="tabela",
        chunk_index=row.chunk_index, pagina=row.pagina, pagina_final=row.pagina_final, secao=row.secao,
        linha=row.linha, coluna=label, trecho=row.trecho, texto_valor=number.text, valor=str(number.value),
        tipo=kind, unidade=row.unidade, unidade_origem=row.unidade_origem, escopo=atual().plugin.escopo_da_coluna(label),
        alternativa_consolidada=row.alternativa_consolidada, periodo_confirmado=True))
    return {"periodo": comparable, "valor": number.text, "coluna": label}


class SalvarNaoEncontrado(Args):
    doc_id: str = Field(description="Documento.")
    campo: CampoPrincipal = Field(description="O indicador que o documento não informa.")
    motivo: str = Field(min_length=20, max_length=600, description="Por que o campo não existe no documento, e "
                        "onde foi procurado.")
    paginas_descartadas: list[int] = Field(default_factory=list, description="Páginas em que o termo aparece e "
                                           "que não servem (o motivo explica por quê). Pode começar vazia: se o "
                                           "termo aparecer no documento, o erro lista as páginas.")


@tool("salvar_nao_encontrado", "extracao", """
    Registra que o documento não informa um indicador. O servidor procura os termos do campo no
    documento inteiro; se aparecerem, a chamada é recusada com as páginas, e você deve lê-las:
    se o valor está lá, use salvar_indicador; se não serve, repita a chamada listando essas
    páginas em paginas_descartadas e explicando no motivo. Nunca estime um valor.""")
async def salvar_nao_encontrado(call: Call, args: SalvarNaoEncontrado) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    reference, _ = periods(document)
    info = FIELDS[args.campo]
    check_script(args.motivo, "O motivo")
    if info.statement:
        pages = sorted({chunk["page_start"] for chunk in statement_tables(document, info.section)})
        if pages:
            raise ToolRejected(
                f"{info.label} é uma linha da demonstração financeira, e o documento tem essa demonstração (p. "
                f"{', '.join(map(str, pages[:6]))}): o valor está nela. Abra a tabela com ler_chunk e copie o rótulo "
                "da linha como está escrito (a linha do total do grupo, não o título). Se a célula não puder ser "
                "lida, use origem='texto' com uma nota dizendo isso.")
    evidence = atual().plugin.evidencia_de_valor(document, args.campo)
    if evidence:
        raise ToolRejected(
            f"O documento traz {info.label} com valor: não é 'não encontrado'. Grave com salvar_indicador a linha "
            "ou a frase da lista (prefira a linha de tabela). Se o que está escrito não é exatamente o indicador "
            "do exercício (a frase fala em outro ano, o valor é ajustado ou é de uma parte do grupo), grave mesmo "
            "assim e diga isso em 'nota': o relatório mostra a nota e o alerta, e o analista decide.",
            problemas=[f"p. {page}: {what}" for page, what in evidence[:5]])
    found = occurrences(document, info.terms)
    pending = sorted(set(found) - set(args.paginas_descartadas))
    if pending:
        raise ToolRejected(
            f"Os termos de '{info.label}' aparecem em páginas que você não descartou: "
            f"{', '.join(str(page) for page in pending)}. Leia-as antes de dizer que o campo não existe.",
            problemas=[f"p. {page}: …{found[page]}…" for page in pending[:10]])
    await call.deps.store.drop_values(call.caller.user_id, args.doc_id, args.campo)
    evidence = {"termos_procurados": list(info.terms), "paginas_com_o_termo": sorted(found),
                "paginas_descartadas": sorted(set(args.paginas_descartadas) & set(found))}
    await call.deps.store.save_value(StoredValue(
        user_id=call.caller.user_id, doc_id=args.doc_id, fingerprint=document.fingerprint,
        run_id=call.caller.run_id, agent=call.caller.agent, campo=args.campo, periodo=reference, medida="valor",
        status="nao_encontrado", motivo=_squash(args.motivo), evidencia=evidence))
    return {"salvo": {"campo": args.campo, "status": "nao_encontrado"}, "evidencia": evidence}


class FecharExtracao(Args):
    doc_id: str = Field(description="Documento.")
    apoio_ausente: str | None = Field(
        default=None, max_length=400, description="Normalmente null. Preencha só se a chamada foi recusada por "
        "falta de valores de apoio e o documento realmente não os traz (ex.: não há fluxo de caixa): diga quais "
        "faltam e por quê.")


async def build_extraction(call: Call, document: IndexedDocument) -> tuple[Extraction, list[StoredValue]]:
    reference, comparable = periods(document)
    rows = await call.deps.store.values(call.caller.user_id, document.doc_id, document.fingerprint)
    return Extraction([to_value(row) for row in rows], reference, comparable), rows


def _status_line(block: dict[str, Any]) -> str:
    informed, computed = block["informado"], block["calculado"]
    if informed and informed["status"] == "encontrado":
        line = f"informado: {informed['valor']} {informed['unidade'] or ''} (p. {informed['origem']['pagina']})"
    else:
        line = "não encontrado"
    if computed:
        line += f"; calculado: {computed['valor']} {computed['unidade'] if computed['unidade'] not in ('%', 'x') else ''}"
    variation = (block["variacao"] or {}).get("calculada")
    if variation:
        line += f"; variação: {variation['valor']}"
    return " ".join(line.split())


@tool("fechar_extracao", "extracao", """
    Fecha a extração de um documento. Confere que os campos principais têm valor ou "não encontrado",
    calcula o que os valores guardados permitem (variação contra o ano anterior, margem, EBITDA,
    dívida líquida, alavancagem, capex), roda as validações automáticas e publica o JSON. Se faltar
    campo, o erro lista quais. Indicador que a empresa não informa precisa dos valores de apoio para
    o servidor calcular, e o EBITDA precisa deles mesmo quando é informado, para ser conferido: se
    faltarem, o erro diz quais gravar.""")
async def fechar_extracao(call: Call, args: FecharExtracao) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    extraction, _ = await build_extraction(call, document)
    missing = extraction.missing()
    if missing:
        raise ToolRejected(
            f"Faltam {len(missing)} dos {len(MAIN_FIELDS)} campos principais. Cada um precisa de um valor "
            "(salvar_indicador) ou de "
            "um registro de não encontrado (salvar_nao_encontrado).",
            problemas=[f"{campo}: {FIELDS[campo].label}" for campo in missing])
    uncomputable = atual().plugin.conferir_fechamento(document, extraction, args.apoio_ausente)
    extraction.alerts.clear()  # a conferência acima já passou pelas fórmulas: os alertas saem de indicators()
    indicators = extraction.indicators()
    if uncomputable:
        check_script(args.apoio_ausente, "A explicação")
        extraction.alerts.append(AlertDraft(
            "calculo_impossivel", "atencao",
            mensagem=f"Sem valores de apoio no documento para calcular ou conferir "
                     f"{', '.join(FIELDS[c].label for c in uncomputable)}. {_squash(args.apoio_ausente)}",
            detalhes={"campos": uncomputable}))
    alerts = list({(a.codigo, a.campo, a.mensagem): a for a in extraction.alerts}.values())
    new_alerts = await call.deps.store.add_alerts(call.caller, args.doc_id, document.fingerprint, "validacao", alerts)
    payload = {
        "documento": {
            "doc_id": document.doc_id, "arquivo": document.file_name, "empresa": document.company,
            "origem_da_empresa": _origem(document, "entidade"), "tipo": document.doc_type,
            "paginas": document.page_count, "periodo_referencia": extraction.reference,
            "periodo_comparavel": extraction.comparable,
            "origem_do_periodo": _origem(document, "recorte")},
        "indicadores": indicators,
        "valores_de_apoio": extraction.support(),
        "alertas_de_validacao": [alert.view() for alert in alerts],
        "impressao_do_indice": document.fingerprint,
    }
    await call.deps.store.publish(call.caller, args.doc_id, document.fingerprint, payload)
    return {"publicada": True, "empresa": document.company, "periodo": extraction.reference,
            "indicadores": {campo: _status_line(indicators[campo]) for campo in MAIN_FIELDS},
            "alertas": [f"[{alert.severidade}] {alert.mensagem}" for alert in alerts],
            "alertas_novos": new_alerts}


class LerExtracao(Args):
    doc_id: str | None = Field(default=None, description="Um documento, ou null para todos os do usuário.")
    detalhe: Literal["resumo", "enxuto", "completo"] = Field(
        default="resumo", description="'resumo': uma linha por indicador. 'enxuto': só os campos do relatório, cada "
        "um com valor, unidade, variação e origem (página e trecho), ou 'não encontrado'. 'completo': o JSON "
        "inteiro, com os valores de apoio e as validações.")


@tool("ler_extracao", "extracao", """
    Lê a extração publicada de um documento (ou de todos). Diz também quando ela está desatualizada,
    isto é, quando o documento foi reindexado depois da extração.""")
async def ler_extracao(call: Call, args: LerExtracao) -> dict[str, Any]:
    if args.doc_id is not None:
        doc_ids = [args.doc_id]
    else:
        listed = await call.deps.indexer.documents(call.caller.user_token, call.caller.colecao_id)
        doc_ids = [d["doc_id"] for d in listed if d.get("status") == "indexed"]
    result = []
    for doc_id in doc_ids:
        document = await call.deps.documents.get(call.caller, doc_id)
        published = await call.deps.store.extraction(call.caller.user_id, doc_id)
        entry: dict[str, Any] = {"doc_id": doc_id, "arquivo": document.file_name, "empresa": document.company}
        if published is None:
            entry["extracao"] = None
        elif published.fingerprint != document.fingerprint:
            entry.update(extracao=None, desatualizada="O documento foi reindexado depois da extração. Refaça.")
        elif args.detalhe == "completo":
            entry["extracao"] = published.payload
        elif args.detalhe == "enxuto":
            entry["extracao"] = compact(published.payload)
        else:
            payload = published.payload
            entry["extracao"] = {
                "periodo_referencia": payload["documento"]["periodo_referencia"],
                "periodo_comparavel": payload["documento"]["periodo_comparavel"],
                "indicadores": {campo: _status_line(block) for campo, block in payload["indicadores"].items()},
                "alertas": len(payload["alertas_de_validacao"])}
        result.append(entry)
    return {"documentos": result}
