"""Avaliação da busca híbrida nos três PDFs do teste.

    uv run python scripts/eval_search.py                 # usuário "smoke"

Roda contra a API em execução, depois de `scripts/smoke.py`. São perguntas cuja resposta tem
página conhecida: os indicadores pedidos no teste, as demonstrações primárias, riscos e notas.
Para cada uma, conta em que posição vem o chunk certo, isto é, o que cobre uma das páginas
esperadas e contém o trecho esperado. Imprime acerto nos 8 primeiros, no 1º lugar e o MRR.
"""
import argparse
import sys

import httpx

K = 8
# (arquivo, pergunta, páginas aceitas, trecho que o chunk precisa conter)
CASES = [
    ("Brasal.pdf", "Qual foi a receita operacional líquida da Brasal em 2022?", {8}, "1.325.059"),
    ("Brasal.pdf", "demonstração do resultado lucro líquido do exercício", {8}, "177.244"),
    ("Brasal.pdf", "balanço patrimonial total do ativo", {6}, "Total do ativo"),
    ("Brasal.pdf", "balanço patrimonial empréstimos e financiamentos passivo circulante", {7},
     "Empréstimos e financiamentos"),
    ("Brasal.pdf", "fluxo de caixa das atividades operacionais", {11}, "atividades operacionais"),
    ("Brasal.pdf", "aquisição de imobilizado capex investimentos", {11}, "imobilizado"),
    ("Brasal.pdf", "composição dos empréstimos e financiamentos taxa de juros vencimento", {37, 38, 39}, ""),
    ("Brasal.pdf", "risco de liquidez risco de crédito gestão de riscos", {49, 50, 51}, "isco"),
    ("Brasal.pdf", "eventos subsequentes", {51}, ""),
    ("Brasal.pdf", "contexto operacional atividade da companhia", {12}, "Companhia"),
    ("Millet.pdf", "Qual foi a receita operacional líquida da Millet em 2022?", {7}, "462.127"),
    ("Millet.pdf", "lucro líquido do exercício", {7}, "14.743"),
    ("Millet.pdf", "balanço patrimonial total do ativo", {6}, "Total do ativo"),
    ("Millet.pdf", "fluxo de caixa das atividades operacionais", {10}, "atividades operacionais"),
    ("Millet.pdf", "aquisição de imobilizado", {10}, "imobilizado"),
    ("Millet.pdf", "dívida líquida gestão de capital", {42}, "13.520"),
    ("Millet.pdf", "contexto operacional constituição da companhia", {11}, "constituída"),
    ("Millet.pdf", "risco de crédito risco de liquidez", {40, 41, 42}, "isco"),
    ("Millet.pdf", "receita operacional líquida composição impostos sobre vendas", {38, 39}, "eceita"),
    ("Millet.pdf", "eventos subsequentes", {43}, ""),
    ("Light.pdf", "Qual foi a receita operacional líquida da Light em 2022?", {9}, "12.107"),
    ("Light.pdf", "demonstração do resultado receita líquida consolidado", {20}, "Receita líquida"),
    ("Light.pdf", "Qual foi o EBITDA da Light em 2022?", {9, 10}, "1.180"),
    ("Light.pdf", "resultado líquido prejuízo do exercício", {10, 20}, "líquido"),
    ("Light.pdf", "dívida líquida e alavancagem Dívida Líquida/EBITDA", {10}, "3,32x"),
    ("Light.pdf", "investimentos capex no ano", {10}, "1.506"),
    ("Light.pdf", "demonstração dos fluxos de caixa atividades operacionais", {23}, "atividades operacionais"),
    ("Light.pdf", "balanço patrimonial total do ativo", {18}, "ATIVO"),
    ("Light.pdf", "continuidade operacional renovação da concessão incerteza", {25, 26, 27, 28}, "concessão"),
    ("Light.pdf", "Lei 14.385 créditos de PIS e COFINS", {28, 29}, "14.385"),
    ("Light.pdf", "risco de liquidez", {101, 102}, "liquidez"),
    ("Light.pdf", "risco de taxa de câmbio", {98, 99}, "câmbio"),
    ("Light.pdf", "covenants cláusulas de vencimento antecipado", {26, 27, 66, 70}, "ovenant"),
    ("Light.pdf", "eventos subsequentes", {105, 106, 107, 108}, ""),
    ("Light.pdf", "mensagem da administração", {2, 3}, ""),
    ("Light.pdf", "perdas não técnicas", {5, 6}, ""),
    ("Light.pdf", "reconhecimento de receitas política contábil", {42, 43}, "eceita"),
    ("Light.pdf", "provisões para contingências cíveis trabalhistas", {75, 76, 77, 78, 79, 80}, "rovis"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", default="smoke")
    parser.add_argument("--password", default="smoke-senha-123")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        token = client.post("/auth/login", data={"username": args.username, "password": args.password})
        if token.status_code != 200:
            print("Login recusado. Rode scripts/smoke.py antes, com o mesmo usuário.")
            return 1
        client.headers["Authorization"] = f"Bearer {token.json()['access_token']}"
        doc_ids = {d["file_name"]: d["doc_id"] for d in client.get("/api/documents").json()}
        missing = {case[0] for case in CASES} - set(doc_ids)
        if missing:
            print("Documentos não indexados para este usuário:", sorted(missing))
            return 1

        ranks = []
        for file_name, query, pages, needle in CASES:
            hits = client.post("/api/search", json={"query": query, "limit": K,
                                                    "doc_id": doc_ids[file_name]}).json()["hits"]
            rank = next((position for position, hit in enumerate(hits, start=1)
                         if set(range(hit["page_start"], hit["page_end"] + 1)) & pages
                         and needle.lower() in hit["text"].lower()), None)
            ranks.append(rank)
            top = f"p.{hits[0]['page_start']} [{hits[0]['chunk_type']}]" if hits else "(vazio)"
            print(f"  {f'{rank}º' if rank else '—':>3}  {file_name:<10} {query[:66]:<66} | topo: {top}")

    found = [rank for rank in ranks if rank]
    mrr = sum(1 / rank for rank in found) / len(ranks)
    print(f"\n{len(ranks)} perguntas | entre os {K} primeiros: {len(found)} | em 1º lugar: {found.count(1)} "
          f"| até o 3º: {sum(rank <= 3 for rank in found)} | MRR: {mrr:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
