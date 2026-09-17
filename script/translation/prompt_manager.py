"""Translation Prompt Preset을 JSON 파일로 관리합니다."""

from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
import re
from typing import Callable

from script.config.settings import PROJECT_ROOT
from script.utils.exceptions import PromptPresetError


PRESETS_DIR = PROJECT_ROOT / "prompts" / "presets"
REQUIRED_FIELDS = {
    "name",
    "source_language",
    "target_language",
    "document_type",
    "prompt",
    "created_by",
    "created_at",
    "updated_at",
}


@dataclass(frozen=True)
class PromptPreset:
    name: str
    source_language: str
    target_language: str
    document_type: str
    prompt: str
    created_by: str
    created_at: str
    updated_at: str


class PromptManager:
    def __init__(
        self,
        presets_dir: Path = PRESETS_DIR,
        today_provider: Callable[[], str] | None = None,
    ) -> None:
        self.presets_dir = presets_dir
        self._today_provider = today_provider or (lambda: date.today().isoformat())

    def ensure_presets_dir(self) -> None:
        self.presets_dir.mkdir(parents=True, exist_ok=True)

    def create_preset(
        self,
        name: str,
        source_language: str,
        target_language: str,
        document_type: str,
        prompt: str,
        created_by: str = "AI",
    ) -> PromptPreset:
        today = self._today_provider()
        preset = PromptPreset(
            name=name.strip(),
            source_language=source_language.strip(),
            target_language=target_language.strip(),
            document_type=document_type.strip(),
            prompt=prompt.strip(),
            created_by=created_by.strip(),
            created_at=today,
            updated_at=today,
        )
        self.validate(preset)
        return preset

    def list_presets(self) -> list[PromptPreset]:
        self.ensure_presets_dir()
        presets = [self.load(path) for path in self.presets_dir.glob("*.json")]
        return sorted(presets, key=lambda preset: preset.name.casefold())

    def find_by_name(self, name: str) -> PromptPreset | None:
        target = name.strip().casefold()
        for preset in self.list_presets():
            if preset.name.casefold() == target:
                return preset
        return None

    def save(self, preset: PromptPreset, overwrite: bool = False) -> Path:
        self.validate(preset)
        self.ensure_presets_dir()
        existing = self.find_by_name(preset.name)
        if existing is not None and not overwrite:
            raise PromptPresetError(
                f"같은 이름의 Prompt Preset이 이미 있습니다: {preset.name}"
            )

        if existing is not None:
            preset = PromptPreset(
                **{
                    **asdict(preset),
                    "created_at": existing.created_at,
                    "updated_at": self._today_provider(),
                }
            )

        path = self.presets_dir / f"{self._safe_filename(preset.name)}.json"
        temporary_path = path.with_suffix(".json.tmp")
        try:
            with temporary_path.open("w", encoding="utf-8", newline="") as file:
                json.dump(asdict(preset), file, ensure_ascii=False, indent=2)
                file.write("\n")
            temporary_path.replace(path)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise PromptPresetError(f"Prompt Preset을 저장할 수 없습니다: {error}") from error
        return path

    def load(self, path: Path) -> PromptPreset:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PromptPresetError(
                f"Prompt Preset JSON을 읽을 수 없습니다: {path.name}"
            ) from error

        if not isinstance(data, dict) or not REQUIRED_FIELDS.issubset(data):
            raise PromptPresetError(
                f"Prompt Preset 필수 항목이 없습니다: {path.name}"
            )
        try:
            preset = PromptPreset(**{name: data[name] for name in REQUIRED_FIELDS})
        except TypeError as error:
            raise PromptPresetError(
                f"Prompt Preset 형식이 올바르지 않습니다: {path.name}"
            ) from error
        self.validate(preset)
        return preset

    @staticmethod
    def validate(preset: PromptPreset) -> None:
        required_values = {
            "이름": preset.name,
            "원본 언어": preset.source_language,
            "목표 언어": preset.target_language,
            "문서 유형": preset.document_type,
            "Prompt": preset.prompt,
            "생성 주체": preset.created_by,
            "생성일": preset.created_at,
            "수정일": preset.updated_at,
        }
        for label, value in required_values.items():
            if not isinstance(value, str) or not value.strip():
                raise PromptPresetError(f"Prompt Preset의 {label} 항목이 비어 있습니다.")

    @staticmethod
    def _safe_filename(name: str) -> str:
        filename = re.sub(r"[^\w.-]+", "_", name.strip(), flags=re.UNICODE)
        filename = filename.strip("._")
        if not filename:
            raise PromptPresetError("파일명으로 사용할 수 있는 Preset 이름이 필요합니다.")
        return filename.lower()
