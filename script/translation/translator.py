"""여러 TXT 파일의 Chunk 번역 흐름을 관리합니다."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from script.config.translation_settings import DEFAULT_CHUNK_MAX_CHARS
from script.providers.local_llm import LocalLLM
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
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


def split_text_into_chunks(
    text: str,
    max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
) -> list[str]:
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

        # 한 줄 자체가 제한을 넘는 경우에만 줄 중간을 분할합니다.
        while len(line) > max_chars:
            chunks.append(line[:max_chars])
            line = line[max_chars:]

        if line:
            current += line

    if current:
        chunks.append(current)

    return chunks


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

    def translate_all(
        self,
        output_func: Callable[[str], None] = print,
    ) -> TranslationSummary:
        input_files = self.loader.list_txt_files()
        self.writer.ensure_output_dir()
        total = len(input_files)
        succeeded = 0
        skipped = 0
        failed = 0
        stopped = 0
        stopped_file: str | None = None
        was_stopped = False

        if total:
            output_func(f"총 {total}개의 TXT 파일을 발견했습니다.")

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
                continue

            output_func(f"[{index}/{total}] 번역 완료: {input_path.name}")
            succeeded += 1

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
        )

    def _translate_file(self, input_path: Path) -> str:
        source_text = self.loader.read_text(input_path)
        chunks = split_text_into_chunks(source_text, self.chunk_max_chars)
        translated_chunks: list[str] = []

        for index, chunk in enumerate(chunks):
            translated = self.local_llm.generate(
                build_translation_prompt(
                    chunk,
                    source_language=self.source_language,
                    target_language=self.target_language,
                    selected_prompt=self.selected_prompt,
                )
            )
            if not translated.endswith(("\r", "\n")):
                if chunk.endswith("\r\n"):
                    translated += "\r\n"
                elif chunk.endswith("\n"):
                    translated += "\n"
                elif chunk.endswith("\r"):
                    translated += "\r"
            translated_chunks.append(translated)
            if index < len(chunks) - 1 and self.stop_requested():
                raise TranslationStopped("사용자가 번역 중지를 요청했습니다.")

        return "".join(translated_chunks)
