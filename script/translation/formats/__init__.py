"""지원하는 세 가지 파일 형식만 선택합니다."""

from pathlib import Path

from script.translation.formats.base import FormatHandler, TranslationUnit
from script.translation.formats.csv_handler import CsvHandler
from script.translation.formats.json_handler import JsonHandler
from script.translation.formats.txt_handler import TxtHandler

SUPPORTED_EXTENSIONS = {".txt", ".csv", ".json"}


def create_handler(path: Path, chunk_max_chars: int = 10_000) -> FormatHandler:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return TxtHandler(chunk_max_chars)
    if suffix == ".csv":
        return CsvHandler()
    if suffix == ".json":
        return JsonHandler()
    raise ValueError(f"지원하지 않는 파일 형식: {suffix}")


__all__ = ["FormatHandler", "TranslationUnit", "SUPPORTED_EXTENSIONS", "create_handler"]
