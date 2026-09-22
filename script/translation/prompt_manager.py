"""계층형 Translation Prompt Library를 안전하게 관리합니다."""

from dataclasses import asdict, dataclass, replace
from datetime import date
import json
from pathlib import Path
import re
from typing import Any, Callable

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

    def list_presets(
        self,
        source_language: str | None = None,
        target_language: str | None = None,
        document_type: str | None = None,
    ) -> list[PromptPreset]:
        self.ensure_presets_dir()
        presets = [self.load(path) for path in self.presets_dir.rglob("*.json")]
        presets = [
            preset
            for preset in presets
            if self._matches(preset, source_language, target_language, document_type)
        ]
        return sorted(
            presets,
            key=lambda preset: (
                preset.source_language.casefold(),
                preset.target_language.casefold(),
                preset.document_type.casefold(),
                preset.name.casefold(),
            ),
        )

    def list_languages(self) -> list[str]:
        languages = {
            language
            for preset in self.list_presets()
            for language in (preset.source_language, preset.target_language)
        }
        return sorted(languages, key=str.casefold)

    def list_by_source_language(self, source_language: str) -> list[PromptPreset]:
        return self.list_presets(source_language=source_language)

    def list_by_language_pair(
        self, source_language: str, target_language: str
    ) -> list[PromptPreset]:
        return self.list_presets(source_language, target_language)

    def list_by_document_type(
        self,
        document_type: str,
        source_language: str | None = None,
        target_language: str | None = None,
    ) -> list[PromptPreset]:
        return self.list_presets(source_language, target_language, document_type)

    def find_by_name(
        self,
        name: str,
        source_language: str | None = None,
        target_language: str | None = None,
        document_type: str | None = None,
    ) -> PromptPreset | None:
        target = name.strip().casefold()
        return next(
            (
                preset
                for preset in self.list_presets(
                    source_language, target_language, document_type
                )
                if preset.name.casefold() == target
            ),
            None,
        )

    def find_matching_presets(
        self,
        source_language: str,
        target_language: str | None = None,
        document_type: str | None = None,
        exact: bool = False,
    ) -> list[PromptPreset]:
        """정확 일치부터 언어 쌍, 원본 언어 순으로 중복 없이 반환합니다."""
        source = source_language.casefold()
        target = target_language.casefold() if target_language else None
        doc = document_type.casefold() if document_type else None
        all_presets = self.list_presets()
        if exact:
            return [
                preset
                for preset in all_presets
                if preset.source_language.casefold() == source
                and (target is None or preset.target_language.casefold() == target)
                and (doc is None or preset.document_type.casefold() == doc)
            ]

        def score(preset: PromptPreset) -> int:
            if preset.source_language.casefold() != source:
                return 0
            pair = target is not None and preset.target_language.casefold() == target
            type_match = doc is not None and preset.document_type.casefold() == doc
            return 3 if pair and type_match else 2 if pair else 1

        matches = [(score(preset), preset) for preset in all_presets]
        return [
            preset
            for rank, preset in sorted(
                (item for item in matches if item[0]),
                key=lambda item: (-item[0], item[1].name.casefold()),
            )
        ]

    def save(self, preset: PromptPreset, overwrite: bool = False) -> Path:
        self.validate(preset)
        existing_path = self.path_for_preset(preset)
        if existing_path is not None and not overwrite:
            raise PromptPresetError(
                f"같은 이름의 Prompt Preset이 이미 있습니다: {preset.name}"
            )
        if existing_path is not None:
            existing = self.load(existing_path)
            preset = replace(
                preset,
                created_at=existing.created_at,
                updated_at=self._today_provider(),
            )
        destination = self._library_path(preset)
        if destination.exists() and destination != existing_path:
            raise PromptPresetError(
                f"동일한 저장 파일명을 사용하는 Prompt Preset이 있습니다: {preset.name}"
            )
        self._atomic_write(destination, asdict(preset))
        # 사용자가 overwrite한 legacy 파일은 새 구조 저장 성공 후에만 제거합니다.
        if existing_path is not None and existing_path != destination:
            self._safe_unlink(existing_path)
        return destination

    def load(self, path: Path) -> PromptPreset:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PromptPresetError(
                f"Prompt Preset JSON을 읽을 수 없습니다: {path.name}"
            ) from error
        if not isinstance(data, dict) or not REQUIRED_FIELDS.issubset(data):
            raise PromptPresetError(f"Prompt Preset 필수 항목이 없습니다: {path.name}")
        try:
            preset = PromptPreset(**{name: data[name] for name in REQUIRED_FIELDS})
        except TypeError as error:
            raise PromptPresetError(
                f"Prompt Preset 형식이 올바르지 않습니다: {path.name}"
            ) from error
        self.validate(preset)
        return preset

    def update(
        self,
        target: Path | PromptPreset,
        **changes: Any,
    ) -> tuple[PromptPreset, Path]:
        old_path = self._resolve_target(target)
        old = self.load(old_path)
        allowed = {
            "name", "source_language", "target_language", "document_type",
            "prompt", "created_by",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise PromptPresetError(f"수정할 수 없는 항목입니다: {', '.join(sorted(unknown))}")
        updated = replace(
            old,
            **changes,
            created_at=old.created_at,
            updated_at=self._today_provider(),
        )
        self.validate(updated)
        new_path = self._library_path(updated)
        collision = self.path_for_preset(updated)
        if collision is not None and collision != old_path:
            raise PromptPresetError(
                f"같은 이름의 Prompt Preset이 이미 있습니다: {updated.name}"
            )
        if new_path.exists() and new_path not in {old_path, collision}:
            raise PromptPresetError(
                f"동일한 저장 파일명을 사용하는 Prompt Preset이 있습니다: {updated.name}"
            )
        self._atomic_write(new_path, asdict(updated))
        if new_path != old_path:
            self._safe_unlink(old_path)
        return updated, new_path

    def delete(self, target: Path | PromptPreset) -> bool:
        path = self._resolve_target(target)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise PromptPresetError(f"Prompt Preset을 삭제할 수 없습니다: {error}") from error
        self._remove_empty_parents(path.parent)
        return True

    def import_txt(
        self,
        path: Path,
        name: str,
        source_language: str,
        target_language: str,
        document_type: str,
        created_by: str = "Imported",
        overwrite: bool = False,
    ) -> Path:
        if path.suffix.lower() != ".txt":
            raise PromptPresetError("TXT Prompt 파일만 가져올 수 있습니다.")
        try:
            prompt = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise PromptPresetError(f"TXT Prompt를 읽을 수 없습니다: {error}") from error
        return self.save(
            self.create_preset(
                name, source_language, target_language, document_type, prompt, created_by
            ),
            overwrite=overwrite,
        )

    def import_json(self, path: Path, overwrite: bool = False) -> Path:
        if path.suffix.lower() != ".json":
            raise PromptPresetError("JSON Prompt Preset 파일만 가져올 수 있습니다.")
        return self.save(self.load(path), overwrite=overwrite)

    def export_txt(self, target: Path | PromptPreset, destination: Path) -> Path:
        preset = self.load(self._resolve_target(target))
        self._atomic_write_text(destination, preset.prompt)
        return destination

    def export_json(self, target: Path | PromptPreset, destination: Path) -> Path:
        preset = self.load(self._resolve_target(target))
        self._atomic_write(destination, asdict(preset))
        return destination

    def path_for_preset(self, preset: PromptPreset) -> Path | None:
        for path in self._preset_paths():
            try:
                candidate = self.load(path)
            except PromptPresetError:
                continue
            if (
                candidate.name.casefold() == preset.name.casefold()
                and candidate.source_language.casefold()
                == preset.source_language.casefold()
                and candidate.target_language.casefold()
                == preset.target_language.casefold()
                and candidate.document_type.casefold()
                == preset.document_type.casefold()
            ):
                return path
        return None

    def reference_for(self, target: Path | PromptPreset) -> str:
        path = self._resolve_target(target).resolve()
        root = self.presets_dir.resolve()
        try:
            return path.relative_to(root).as_posix()
        except ValueError as error:
            raise PromptPresetError("Prompt가 Library 외부에 있습니다.") from error

    def resolve_reference(self, reference: str) -> PromptPreset | None:
        candidate = (self.presets_dir / Path(reference)).resolve()
        try:
            candidate.relative_to(self.presets_dir.resolve())
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        try:
            return self.load(candidate)
        except PromptPresetError:
            return None

    def _library_path(self, preset: PromptPreset) -> Path:
        return (
            self.presets_dir
            / self._safe_component(preset.source_language)
            / self._safe_component(preset.target_language)
            / self._safe_component(preset.document_type)
            / f"{self._safe_filename(preset.name)}.json"
        )

    def _resolve_target(self, target: Path | PromptPreset) -> Path:
        if isinstance(target, Path):
            path = target
        else:
            path = self.path_for_preset(target)
            if path is None:
                raise PromptPresetError(f"Prompt Preset을 찾을 수 없습니다: {target.name}")
        resolved = path.resolve()
        try:
            resolved.relative_to(self.presets_dir.resolve())
        except ValueError as error:
            raise PromptPresetError("Prompt Library 외부 경로는 사용할 수 없습니다.") from error
        return path

    def _preset_paths(self) -> list[Path]:
        if not self.presets_dir.is_dir():
            return []
        return sorted(self.presets_dir.rglob("*.json"), key=lambda path: path.as_posix().casefold())

    @staticmethod
    def _matches(
        preset: PromptPreset,
        source_language: str | None,
        target_language: str | None,
        document_type: str | None,
    ) -> bool:
        pairs = (
            (preset.source_language, source_language),
            (preset.target_language, target_language),
            (preset.document_type, document_type),
        )
        return all(expected is None or actual.casefold() == expected.casefold() for actual, expected in pairs)

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

    @staticmethod
    def _safe_component(value: str) -> str:
        component = re.sub(r"[^\w.-]+", "_", value.strip(), flags=re.UNICODE)
        component = component.strip("._")
        if not component:
            raise PromptPresetError("Prompt Library 경로에 사용할 metadata가 필요합니다.")
        return component

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise PromptPresetError(f"Prompt Preset을 저장할 수 없습니다: {error}") from error

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_text(content, encoding="utf-8", newline="")
            temporary.replace(path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise PromptPresetError(f"Prompt를 내보낼 수 없습니다: {error}") from error

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink()
        except OSError as error:
            raise PromptPresetError(
                f"새 Prompt는 저장했지만 기존 Prompt를 삭제할 수 없습니다: {error}"
            ) from error

    def _remove_empty_parents(self, directory: Path) -> None:
        root = self.presets_dir.resolve()
        current = directory.resolve()
        while current != root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
