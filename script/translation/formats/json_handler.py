"""선택한 JSON Key의 문자열 값만 번역합니다."""

import json
from pathlib import Path
from typing import Any

from script.translation.formats.base import TranslationUnit
from script.utils.exceptions import FileProcessingError


class JsonHandler:
    def __init__(self) -> None:
        self.data: Any = None
        self.fields: list[str] = []
        self.locations: dict[str, tuple[str | int, ...]] = {}

    def load(self, path: Path) -> None:
        try:
            self.data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, OSError, json.JSONDecodeError) as error:
            raise FileProcessingError(f"JSON 파일을 읽거나 파싱할 수 없습니다: {error}") from error
        if not isinstance(self.data, (dict, list)):
            raise FileProcessingError("JSON root는 object 또는 list여야 합니다.")
        self.fields = []
        self.locations = {}

    def _walk(self, value: Any, path: tuple[str | int, ...] = ()):
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, key)
                if isinstance(child, str):
                    yield key, child_path, child
                else:
                    yield from self._walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_path = (*path, index)
                if isinstance(child, str) and path and isinstance(path[-1], str):
                    yield path[-1], child_path, child
                else:
                    yield from self._walk(child, child_path)

    def available_fields(self) -> list[str]:
        return list(dict.fromkeys(key for key, _, _ in self._walk(self.data)))

    def select_fields(self, fields: list[str]) -> None:
        available = self.available_fields()
        if not fields or any(field not in available for field in fields):
            raise ValueError("선택한 JSON Key가 없거나 유효하지 않습니다.")
        self.fields = list(dict.fromkeys(fields))

    def extract_units(self) -> list[TranslationUnit]:
        self.locations = {}
        units: list[TranslationUnit] = []
        for field, path, value in self._walk(self.data):
            if field not in self.fields or not value.strip():
                continue
            key = ".".join(
                str(part).replace(chr(92), chr(92) * 2).replace(".", chr(92) + ".")
                if isinstance(part, str) else str(part)
                for part in path
            )
            if key in self.locations:
                raise FileProcessingError(f"JSON 경로 충돌: {key}")
            self.locations[key] = path
            units.append(TranslationUnit(key, value))
        return units

    def apply_translations(self, translations: dict[str, str]) -> None:
        for key, path in self.locations.items():
            if key not in translations:
                raise ValueError(f"번역 결과가 없습니다: {key}")
            parent = self.data
            for part in path[:-1]:
                parent = parent[part]
            parent[path[-1]] = translations[key]

    def serialize(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
