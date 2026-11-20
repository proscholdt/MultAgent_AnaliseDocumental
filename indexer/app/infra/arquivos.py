"""Arquivos em disco, sempre sob storage/{user_id}/{doc_id}/.

O nome enviado pelo cliente nunca vira caminho: o diretório é derivado do user_id
(do token) e do doc_id (hash do conteúdo), então não há como escapar da pasta.
"""
import hashlib
import io
import re
import shutil
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, WrongPasswordError

from app.config import get_settings

_SAFE_ID = re.compile(r"^[0-9a-fA-F-]{1,64}$")
_SAFE_PARSER = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class InvalidPdf(ValueError):
    """PDF recusado na validação de upload."""


def compute_doc_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def doc_dir(user_id: str, doc_id: str) -> Path:
    if not _SAFE_ID.match(user_id) or not _SAFE_ID.match(doc_id):
        raise ValueError("Identificador inválido.")
    return get_settings().storage_dir / user_id / doc_id


def upload_path(user_id: str, doc_id: str) -> Path:
    return doc_dir(user_id, doc_id) / "upload.pdf"


def parse_dir(user_id: str, doc_id: str, parser: str) -> Path:
    """Pasta com o que um parser produziu para o documento: parsed.json, os recortes de figura
    e as transcrições delas. Cada parser tem a sua, porque recortam as figuras de jeitos
    diferentes; assim trocar de parser e voltar não refaz o parse."""
    if not _SAFE_PARSER.match(parser):
        raise ValueError("Parser inválido.")
    return doc_dir(user_id, doc_id) / "parse" / parser


def _variante(nome: str, variante: str | None) -> str:
    """O arquivo de um domínio que muda o resultado (`parsed-laudos.json`); sem variante, o de sempre."""
    if variante is None:
        return f"{nome}.json"
    if not _SAFE_PARSER.match(variante):
        raise ValueError("Variante inválida.")
    return f"{nome}-{variante}.json"


def parsed_path(user_id: str, doc_id: str, parser: str, variante: str | None = None) -> Path:
    return parse_dir(user_id, doc_id, parser) / _variante("parsed", variante)


def figures_cache_path(user_id: str, doc_id: str, parser: str, variante: str | None = None) -> Path:
    return parse_dir(user_id, doc_id, parser) / _variante("figures", variante)


def metadata_cache_path(user_id: str, doc_id: str) -> Path:
    """Resposta do LLM sobre empresa, período e tipo. Vale para os dois parsers: ela sai do
    texto nativo das primeiras páginas, que não depende de quem fez o parse."""
    return doc_dir(user_id, doc_id) / "metadata.json"


def validate_pdf(data: bytes) -> int:
    """Confere assinatura, tamanho e número de páginas. Devolve o total de páginas."""
    settings = get_settings()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise InvalidPdf(f"Arquivo maior que {settings.max_upload_mb} MB.")
    # A assinatura pode vir depois de alguns bytes de lixo; o padrão tolera até 1024.
    if b"%PDF-" not in data[:1024]:
        raise InvalidPdf("O arquivo não é um PDF (assinatura %PDF- ausente).")
    try:
        # PDF só com restrição de permissões (senha de usuário vazia) abre normalmente:
        # o pypdf tenta a senha vazia sozinho. Só é recusado o que exige senha para ler.
        reader = PdfReader(io.BytesIO(data))
        pages = len(reader.pages)
    except (FileNotDecryptedError, WrongPasswordError):
        raise InvalidPdf("PDF protegido por senha de abertura não é aceito.") from None
    except Exception as exc:
        raise InvalidPdf(f"PDF corrompido ou ilegível: {exc}") from exc
    if pages == 0:
        raise InvalidPdf("PDF sem páginas.")
    if pages > settings.max_pages:
        raise InvalidPdf(f"PDF com {pages} páginas; o limite é {settings.max_pages}.")
    return pages


def save_upload(user_id: str, doc_id: str, data: bytes) -> Path:
    path = upload_path(user_id, doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def delete_document_files(user_id: str, doc_id: str) -> None:
    shutil.rmtree(doc_dir(user_id, doc_id), ignore_errors=True)


def clear_derived_files(user_id: str, doc_id: str) -> None:
    """Apaga o que foi derivado do PDF (parse de todos os parsers, figuras, metadados),
    mantendo o upload."""
    metadata_cache_path(user_id, doc_id).unlink(missing_ok=True)
    shutil.rmtree(doc_dir(user_id, doc_id) / "parse", ignore_errors=True)
