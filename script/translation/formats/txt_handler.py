"""TXT 청크 처리. 기존 줄 경계 우선 분할 정책을 유지합니다."""

from pathlib import Path

from script.config.translation_settings import DEFAULT_CHUNK_MAX_CHARS
from script.translation.file_loader import FileLoader
from script.translation.formats.base import TranslationUnit
from script.translation.placeholder import PLACEHOLDER_PATTERN


def split_text_into_chunks(text: str, max_chars: int = DEFAULT_CHUNK_MAX_CHARS) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars는 0보다 커야 합니다.")
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    for original_line in text.splitlines(keepends=True):
        line = original_line
        if current and len(current) + len(line) > max_chars:
            chunks.append(current)
            current = ""
        while len(line) > max_chars:
            cut = max_chars
            for match in PLACEHOLDER_PATTERN.finditer(line):
                if match.start() < cut < match.end():
                    if match.start() == 0:
                        raise ValueError("Placeholder 길이가 TXT Chunk 제한을 초과합니다.")
                    cut = match.start()
                    break
            chunks.append(line[:cut])
            line = line[cut:]
        if line:
            current += line
    if current:
        chunks.append(current)
    return chunks


class TxtHandler:
    def __init__(self, max_chars: int = DEFAULT_CHUNK_MAX_CHARS) -> None:
        self.max_chars = max_chars
        self.chunks: list[str] = []
        self.translations: dict[str, str] = {}

    def load(self, path: Path) -> None:
        self.chunks = split_text_into_chunks(FileLoader.read_text(path), self.max_chars)
        self.translations = {}

    def available_fields(self) -> list[str]:
        return []

    def select_fields(self, fields: list[str]) -> None:
        if fields:
            raise ValueError("TXT에는 선택할 컬럼이나 Key가 없습니다.")

    def extract_units(self) -> list[TranslationUnit]:
        return [TranslationUnit(f"chunk_{i}", chunk) for i, chunk in enumerate(self.chunks)]

    def apply_translations(self, translations: dict[str, str]) -> None:
        for i, chunk in enumerate(self.chunks):
            key = f"chunk_{i}"
            if key not in translations:
                raise ValueError(f"번역 결과가 없습니다: {key}")
            translated = translations[key]
            if not translated.endswith(("\r", "\n")):
                if chunk.endswith("\r\n"):
                    translated += "\r\n"
                elif chunk.endswith("\n"):
                    translated += "\n"
                elif chunk.endswith("\r"):
                    translated += "\r"
            self.translations[key] = translated

    def serialize(self) -> str:
        return "".join(self.translations[f"chunk_{i}"] for i in range(len(self.chunks)))
