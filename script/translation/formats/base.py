"""번역 대상과 파일 형식 Handler의 공통 계약."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TranslationUnit:
    key: str
    text: str


class FormatHandler(Protocol):
    def load(self, path: Path) -> None: ...
    def available_fields(self) -> list[str]: ...
    def select_fields(self, fields: list[str]) -> None: ...
    def extract_units(self) -> list[TranslationUnit]: ...
    def apply_translations(self, translations: dict[str, str]) -> None: ...
    def serialize(self) -> str: ...
