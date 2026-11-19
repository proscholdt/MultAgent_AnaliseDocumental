"""O documento indexado como o domínio o enxerga: metadados e chunks, sem saber de onde vieram."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IndexedDocument:
    doc_id: str
    file_name: str
    company: str | None
    period: str | None
    doc_type: str | None
    page_count: int | None
    metadata_source: dict[str, Any]
    # "Impressão" do índice: muda quando o documento é reindexado. O que foi extraído com uma
    # impressão não vale para outra, porque chunk_index e texto podem ter mudado.
    fingerprint: str
    chunks: tuple[dict[str, Any], ...]

    def chunk(self, index: int) -> dict[str, Any] | None:
        return self.chunks[index] if 0 <= index < len(self.chunks) else None

    def page(self, page: int) -> list[dict[str, Any]]:
        return [chunk for chunk in self.chunks if chunk["page_start"] <= page <= chunk["page_end"]]


def fingerprint(meta: dict[str, Any]) -> str:
    return f"{meta.get('indexed_at')}|{meta.get('chunk_count')}"
