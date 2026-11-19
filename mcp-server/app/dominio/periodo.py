"""O período de um valor como entidade: exercício anual, trimestre ou acumulado do ano.

Antes, o período era um ano de 4 dígitos, e o comparável era o ano anterior. Agora:

- anual ``2022``: comparável ``2021``;
- trimestre ``3T2025`` (aceita ``3T25``): comparável ``3T2024``, o mesmo trimestre do ano anterior;
- acumulado ``9M2025`` (aceita ``9M25``): comparável ``9M2024``.

O rótulo canônico é o que fica gravado. A coluna de uma tabela confere com o período quando cita o
ano (anual) ou o trimestre/acumulado daquele ano; data de fim de trimestre também vale
("30/09/2025" é o 3T2025 ou o 9M2025). No anual, a regra é a de antes: só o ano.
"""
import re
from dataclasses import dataclass
from typing import Literal

Tipo = Literal["anual", "trimestre", "acumulado"]

_ANUAL = re.compile(r"(19|20)\d{2}")
_TRIMESTRE = re.compile(r"([1-4])T(\d{2}|\d{4})", re.IGNORECASE)
_ACUMULADO = re.compile(r"(3|6|9|12)M(\d{2}|\d{4})", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_FIM_DO_TRIMESTRE = {3: "31/03", 6: "30/06", 9: "30/09", 12: "31/12"}
# Aceito em `periodo` das tools: um dos três formatos.
PADRAO = r"^((19|20)\d{2}|[1-4][Tt](\d{2}|\d{4})|(3|6|9|12)[Mm](\d{2}|\d{4}))$"


def _ano(texto: str) -> int:
    return int(texto) if len(texto) == 4 else 2000 + int(texto)


@dataclass(frozen=True)
class Periodo:
    tipo: Tipo
    ano: int
    trimestre: int | None = None  # 1 a 4, no trimestre
    meses: int | None = None  # 3, 6, 9 ou 12, no acumulado

    @classmethod
    def parse(cls, texto: str | None) -> "Periodo | None":
        texto = (texto or "").strip()
        if _ANUAL.fullmatch(texto):
            return cls("anual", int(texto))
        if match := _TRIMESTRE.fullmatch(texto):
            return cls("trimestre", _ano(match.group(2)), trimestre=int(match.group(1)))
        if match := _ACUMULADO.fullmatch(texto):
            meses = int(match.group(1))
            return cls("anual", _ano(match.group(2))) if meses == 12 else \
                cls("acumulado", _ano(match.group(2)), meses=meses)
        return None

    @property
    def rotulo(self) -> str:
        if self.tipo == "trimestre":
            return f"{self.trimestre}T{self.ano}"
        if self.tipo == "acumulado":
            return f"{self.meses}M{self.ano}"
        return str(self.ano)

    @property
    def comparavel(self) -> "Periodo":
        """O mesmo período do ano anterior."""
        return Periodo(self.tipo, self.ano - 1, self.trimestre, self.meses)

    @property
    def doze_meses(self) -> bool:
        """Cobre um ano inteiro? Só assim um múltiplo sobre o EBITDA (alavancagem) faz sentido."""
        return self.tipo == "anual"

    def _mes_final(self) -> int:
        return 3 * self.trimestre if self.tipo == "trimestre" else (self.meses or 12)

    def _citado(self, rotulo: str) -> bool:
        texto = rotulo.casefold().replace(" ", "")
        curto = str(self.ano)[2:]
        if self.tipo == "trimestre":
            marcas = (f"{self.trimestre}t{self.ano}", f"{self.trimestre}t{curto}", f"{self.trimestre}ºtrimestre",
                      f"{self.trimestre}°trimestre")
        else:
            marcas = (f"{self.meses}m{self.ano}", f"{self.meses}m{curto}", f"{self.meses}meses")
        return any(marca in texto for marca in marcas)

    def confere_coluna(self, rotulo: str) -> tuple[bool, str | None]:
        """(período confirmado pela coluna, motivo da recusa). Coluna que não cita ano nenhum não
        confirma nem recusa; coluna de outro ano recusa."""
        anos = _YEAR.findall(rotulo)
        if not anos:
            return False, None
        if str(self.ano) not in anos:
            return False, f"A coluna '{rotulo}' é de {', '.join(anos)}, não de {self.rotulo}."
        if self.tipo == "anual":
            return True, None
        data_final = f"{_FIM_DO_TRIMESTRE[self._mes_final()]}/{self.ano}"
        if self._citado(rotulo) or data_final in rotulo.replace(" ", ""):
            return True, None
        return False, (f"A coluna '{rotulo}' é de {self.ano}, mas não diz que é o {self.rotulo} (nem a data "
                       f"{data_final}). Escolha a coluna do período certo.")
