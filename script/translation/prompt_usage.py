"""파일별 Prompt 사용 기록과 deterministic 추천을 관리합니다."""

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any, Callable

from script.config.settings import PROJECT_ROOT
from script.translation.file_fingerprint import file_fingerprint
from script.translation.prompt_manager import PromptManager, PromptPreset
from script.utils.exceptions import FileProcessingError


PROMPT_USAGE_PATH = PROJECT_ROOT / "setting" / "prompt_usage.json"


@dataclass(frozen=True)
class PromptRecommendation:
    preset: PromptPreset
    reason: str
    exact_file: bool = False


class PromptUsageRegistry:
    def __init__(
        self,
        path: Path = PROMPT_USAGE_PATH,
        today_provider: Callable[[], str] | None = None,
    ) -> None:
        self.path = path
        self._today_provider = today_provider or (lambda: date.today().isoformat())

    def record_success(
        self,
        file_path: Path,
        manager: PromptManager,
        prompt: Path | PromptPreset,
        source_language: str,
        target_language: str,
        document_type: str,
        schema_fingerprint: str | None = None,
    ) -> None:
        fingerprint = file_fingerprint(file_path)
        reference = manager.reference_for(prompt)
        preset = manager.resolve_reference(reference)
        if preset is None:
            raise FileProcessingError("사용한 Prompt를 Library에서 찾을 수 없습니다.")
        record: dict[str, Any] = {
            "filename": file_path.name,
            "extension": file_path.suffix.lower(),
            "source_language": source_language,
            "target_language": target_language,
            "document_type": document_type,
            "prompt_ref": reference,
            "prompt_name": preset.name,
            "last_used_at": self._today_provider(),
        }
        if schema_fingerprint:
            record["schema_fingerprint"] = schema_fingerprint
        data = self._read_or_empty()
        data["files"][fingerprint] = record
        self._atomic_write(data)

    def get_record(self, file_path: Path) -> dict[str, Any] | None:
        try:
            record = self._read_or_empty()["files"].get(file_fingerprint(file_path))
        except FileProcessingError:
            return None
        return record.copy() if isinstance(record, dict) else None

    def list_records(self) -> dict[str, dict[str, Any]]:
        """GUI가 파일 시스템을 직접 읽지 않고 사용 이력을 조회합니다."""
        records = self._read_or_empty()["files"]
        return {
            fingerprint: record.copy()
            for fingerprint, record in records.items()
            if isinstance(fingerprint, str) and isinstance(record, dict)
        }

    def get_previous_prompt(
        self, file_path: Path, manager: PromptManager
    ) -> PromptPreset | None:
        record = self.get_record(file_path)
        if record is None or not isinstance(record.get("prompt_ref"), str):
            return None
        return manager.resolve_reference(record["prompt_ref"])

    def recommend(
        self,
        file_path: Path,
        manager: PromptManager,
        source_language: str,
        target_language: str,
        document_type: str,
        schema_fingerprint: str | None = None,
    ) -> list[PromptRecommendation]:
        output: list[PromptRecommendation] = []
        seen: set[str] = set()
        exact = self.get_record(file_path)
        if exact is not None:
            self._append_record(
                exact, manager, output, seen,
                "이 파일에 이전에 사용한 Prompt", exact_file=True,
            )

        ranked: list[tuple[int, dict[str, Any]]] = []
        for record in self._read_or_empty()["files"].values():
            if not isinstance(record, dict):
                continue
            score = self._usage_score(
                record, file_path.suffix.lower(), source_language,
                target_language, document_type, schema_fingerprint,
            )
            if score:
                ranked.append((score, record))
        for score, record in sorted(
            ranked,
            key=lambda item: (-item[0], str(item[1].get("prompt_name", "")).casefold()),
        ):
            reason = (
                "같은 Schema와 언어·문서 유형에서 사용"
                if score >= 7
                else "같은 파일 형식과 언어·문서 유형에서 사용"
            )
            self._append_record(record, manager, output, seen, reason)

        for preset in manager.find_matching_presets(
            source_language, target_language, document_type
        ):
            reference = manager.reference_for(preset)
            if reference not in seen:
                seen.add(reference)
                output.append(PromptRecommendation(preset, "Prompt Library 조건 일치"))
        return output

    @staticmethod
    def _usage_score(
        record: dict[str, Any],
        extension: str,
        source_language: str,
        target_language: str,
        document_type: str,
        schema: str | None,
    ) -> int:
        if (
            str(record.get("source_language", "")).casefold() != source_language.casefold()
            or str(record.get("target_language", "")).casefold() != target_language.casefold()
        ):
            return 0
        score = 2
        if str(record.get("document_type", "")).casefold() == document_type.casefold():
            score += 2
        if str(record.get("extension", "")).casefold() == extension.casefold():
            score += 1
        if schema and record.get("schema_fingerprint") == schema:
            score += 2
        return score

    @staticmethod
    def _append_record(
        record: dict[str, Any],
        manager: PromptManager,
        output: list[PromptRecommendation],
        seen: set[str],
        reason: str,
        exact_file: bool = False,
    ) -> None:
        reference = record.get("prompt_ref")
        if not isinstance(reference, str) or reference in seen:
            return
        preset = manager.resolve_reference(reference)
        if preset is None:
            return
        seen.add(reference)
        output.append(PromptRecommendation(preset, reason, exact_file))

    def _read_or_empty(self) -> dict[str, dict[str, Any]]:
        try:
            if not self.path.is_file():
                return {"files": {}}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or set(data) != {"files"}
                or not isinstance(data["files"], dict)
            ):
                return {"files": {}}
            return data
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"files": {}}

    def _atomic_write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(self.path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise FileProcessingError(
                f"Prompt Usage Registry를 저장할 수 없습니다: {error}"
            ) from error
