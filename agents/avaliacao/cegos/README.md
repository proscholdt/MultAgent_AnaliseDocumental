# Gabarito cego

As regras de extração e de inconsistência foram desenhadas olhando Light, Millet e Brasal; a nota
máxima em `../gabarito.json` mede só esses três. Este gabarito mede se o sistema generaliza.

**A regra que dá valor a ele: quem escreve regras e prompts não abre estes PDFs.** Erro achado
aqui vira pergunta ("que tipo de documento quebra isto?"), e a correção é testada nos três PDFs
de desenvolvimento; os cegos só medem.

## Como montar

1. Escolha de 5 a 10 demonstrações anuais de empresas que não estão no gabarito, de setores
   diferentes (banco e seguradora ficam de fora: o plano de contas é outro).
2. Confira à mão, na camada de texto do PDF, os campos de `mcp-server/config/dominio.yaml` e grave
   em `gabarito.json` nesta pasta, no mesmo formato de `../gabarito.json` (copie a estrutura de
   um documento de lá). Os PDFs não vão para o Git (`*.pdf` nesta pasta está no `.gitignore`).
3. Envie os PDFs pelo frontend com um usuário só para isso.

## Como medir

    uv run python scripts/baseline.py --usuario <u> --senha <s> --rodadas 3 \
        --gabarito avaliacao/cegos/gabarito.json --zerar-entre

O resultado fica em `avaliacao/baseline/`. Compare com a linha de base dos três PDFs de
desenvolvimento: a distância entre as duas é o quanto as regras estão ajustadas a eles.
