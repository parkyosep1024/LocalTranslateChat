"""파일 형식에 관계없이 TranslationUnit을 순차 번역합니다."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from script.config.translation_settings import DEFAULT_CHUNK_MAX_CHARS
from script.providers.local_llm import LocalLLM
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
from script.translation.formats import FormatHandler, create_handler
from script.translation.formats.txt_handler import split_text_into_chunks
from script.translation.placeholder import protect_placeholders, restore_placeholders
from script.utils.exceptions import TranslationStopped


BASIC_TRANSLATION_PROMPT = """다음 텍스트를 {target_language}(으)로 자연스럽게 번역하세요.
원문의 줄바꿈과 형식을 최대한 유지하세요.
설명이나 부가 문장을 추가하지 말고 번역 결과만 출력하세요.
"""


@dataclass(frozen=True)
class TranslationSummary:
    total: int = 0
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: int = 0
    stopped_file: str | None = None
    was_stopped: bool = False
    by_format: dict[str, tuple[int, int]] = field(default_factory=dict)


def build_translation_prompt(
    source_text: str,
    source_language: str = "Unknown",
    target_language: str = "Korean",
    selected_prompt: str | None = None,
) -> str:
    rules = selected_prompt.strip() if selected_prompt else BASIC_TRANSLATION_PROMPT.format(
        target_language=target_language
    ).strip()
    return (
        f"{rules}\n\n"
        f"원본 언어: {source_language}\n"
        f"목표 언어: {target_language}\n\n"
        f"[원문]\n{source_text}"
    )


class Translator:
    def __init__(
        self,
        loader: FileLoader,
        local_llm: LocalLLM,
        writer: FileWriter,
        chunk_max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
        source_language: str = "Unknown",
        target_language: str = "Korean",
        selected_prompt: str | None = None,
        prompt_name: str = "기본 Prompt",
        stop_requested: Callable[[], bool] | None = None,
        handlers: dict[Path, FormatHandler] | None = None,
        file_paths: list[Path] | None = None,
        on_file_succeeded: Callable[[Path], None] | None = None,
    ) -> None:
        self.loader = loader
        self.local_llm = local_llm
        self.writer = writer
        self.chunk_max_chars = chunk_max_chars
        self.source_language = source_language
        self.target_language = target_language
        self.selected_prompt = selected_prompt
        self.prompt_name = prompt_name
        self.stop_requested = stop_requested or (lambda: False)
        self.handlers = handlers or {}
        self.file_paths = file_paths
        self.on_file_succeeded = on_file_succeeded

    def translate_all(
        self,
        output_func: Callable[[str], None] = print,
    ) -> TranslationSummary:
        input_files = self.file_paths if self.file_paths is not None else self.loader.list_supported_files()
        self.writer.ensure_output_dir()
        total = len(input_files)
        succeeded = 0
        skipped = 0
        failed = 0
        stopped = 0
        stopped_file: str | None = None
        was_stopped = False
        counts: dict[str, list[int]] = {ext: [0, 0] for ext in (".txt", ".csv", ".json")}

        if total:
            output_func(f"총 {total}개의 번역 가능한 파일을 발견했습니다.")

        for index, input_path in enumerate(input_files, start=1):
            if self.writer.exists_for(input_path):
                output_func(
                    f"[{index}/{total}] 건너뜀: {input_path.name} (결과 파일 존재)"
                )
                skipped += 1
                continue

            output_func(f"[{index}/{total}] 번역 시작: {input_path.name}")
            try:
                translated_text = self._translate_file(input_path)
                self.writer.write(input_path, translated_text)
            except TranslationStopped:
                output_func("번역이 사용자 요청으로 중지되었습니다.")
                output_func(f"중지 파일: {input_path.name}")
                stopped = 1
                stopped_file = input_path.name
                was_stopped = True
                break
            except Exception as error:
                output_func(f"[{index}/{total}] 번역 실패: {input_path.name}")
                output_func(f"원인: {error}")
                failed += 1
                counts[input_path.suffix.lower()][1] += 1
                continue

            output_func(f"[{index}/{total}] 번역 완료: {input_path.name}")
            succeeded += 1
            counts[input_path.suffix.lower()][0] += 1
            if self.on_file_succeeded is not None:
                try:
                    self.on_file_succeeded(input_path)
                except Exception as error:
                    output_func(f"Prompt 사용 기록 저장 실패: {error}")

            if index < total and self.stop_requested():
                output_func("번역이 사용자 요청으로 중지되었습니다.")
                was_stopped = True
                break

        return TranslationSummary(
            total=total,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            stopped=stopped,
            stopped_file=stopped_file,
            was_stopped=was_stopped,
            by_format={ext: tuple(value) for ext, value in counts.items()},
        )

    def _translate_file(self, input_path: Path) -> str:
        handler = self.handlers.get(input_path)
        if handler is None:
            handler = create_handler(input_path, self.chunk_max_chars)
            handler.load(input_path)
        units = handler.extract_units()
        if not units:
            raise ValueError("번역 대상 텍스트가 없습니다.")
        translations: dict[str, str] = {}
        for index, unit in enumerate(units):
            protected, placeholders = protect_placeholders(unit.text)
            translated = self.local_llm.generate(
                build_translation_prompt(
                    protected,
                    source_language=self.source_language,
                    target_language=self.target_language,
                    selected_prompt=self.selected_prompt,
                )
            )
            translations[unit.key] = restore_placeholders(translated, placeholders)
            if index < len(units) - 1 and self.stop_requested():
                raise TranslationStopped("사용자가 번역 중지를 요청했습니다.")
        handler.apply_translations(translations)
        return handler.serialize()
