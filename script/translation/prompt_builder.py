"""TXT 샘플을 외부 AI에 전달해 Translation Prompt Draft를 만듭니다."""

from pathlib import Path
from typing import Protocol

from script.providers.api_client import ChatMessage
from script.providers.gemini import GeminiClient
from script.translation.file_loader import FileLoader
from script.utils.exceptions import FileProcessingError, InvalidResponseError


DEFAULT_PROMPT_SAMPLE_CHARS_PER_FILE = 3_000
DEFAULT_PROMPT_TOTAL_SAMPLE_CHARS = 12_000


class PromptAI(Protocol):
    def generate(self, prompt: str) -> str:
        """완성된 Translation Prompt를 반환합니다."""


class GeminiPromptAI:
    def __init__(self, client: GeminiClient) -> None:
        self.client = client

    def generate(self, prompt: str) -> str:
        messages: list[ChatMessage] = [
            {
                "role": "system",
                "content": (
                    "You design translation system prompts. Return only the complete "
                    "prompt that will be used by another translation model."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        return self.client.create_chat_completion(messages)


class PromptBuilder:
    def __init__(
        self,
        prompt_ai: PromptAI,
        chars_per_file: int = DEFAULT_PROMPT_SAMPLE_CHARS_PER_FILE,
        total_chars: int = DEFAULT_PROMPT_TOTAL_SAMPLE_CHARS,
    ) -> None:
        if chars_per_file <= 0 or total_chars <= 0:
            raise ValueError("Prompt 샘플 크기는 0보다 커야 합니다.")
        self.prompt_ai = prompt_ai
        self.chars_per_file = chars_per_file
        self.total_chars = total_chars

    def collect_samples(self, files: list[Path]) -> str:
        blocks: list[str] = []
        remaining = self.total_chars

        for path in files:
            if remaining <= 0:
                break
            try:
                text = FileLoader.read_text(path)
            except FileProcessingError:
                continue

            separator = "\n\n" if blocks else ""
            marker = f"[파일: {path.name}]\n"
            available = remaining - len(separator) - len(marker)
            if available <= 0:
                break
            sample = text[: min(self.chars_per_file, available)]
            if not sample:
                continue
            block = marker + sample
            blocks.append(block)
            remaining -= len(separator) + len(block)

        return "\n\n".join(blocks)

    def create_draft(
        self,
        files: list[Path],
        source_language: str,
        target_language: str,
        document_type: str,
    ) -> str:
        samples = self.collect_samples(files)
        if not samples:
            raise FileProcessingError("Prompt를 작성할 TXT 샘플이 없습니다.")

        request = f"""다음 문서 샘플을 분석하여 실제 번역에 사용할 시스템 Prompt를 작성하세요.

원본 언어: {source_language}
목표 언어: {target_language}
사용 목적 또는 문서 유형: {document_type}

요구사항:
- 원문의 의미와 말투를 유지할 것
- Placeholder와 코드 형태의 문자열을 보존할 것
- 줄바꿈 구조를 최대한 유지할 것
- 번역문 외의 설명을 출력하지 않도록 지시할 것
- 샘플을 직접 번역하지 말고 완성된 번역 규칙 Prompt만 반환할 것

[문서 샘플]
{samples}"""
        return self._generate(request)

    def revise_draft(
        self,
        current_prompt: str,
        user_request: str,
        files: list[Path],
        source_language: str,
        target_language: str,
        document_type: str,
    ) -> str:
        if not user_request.strip():
            raise ValueError("Prompt 수정 요구사항을 입력해 주세요.")
        samples = self.collect_samples(files)
        request = f"""기존 Translation Prompt를 사용자 요구사항에 맞게 다시 작성하세요.
수정된 문장만 제시하지 말고 실제 번역에 사용할 완성된 Prompt 전체만 반환하세요.

원본 언어: {source_language}
목표 언어: {target_language}
사용 목적 또는 문서 유형: {document_type}

[기존 Prompt]
{current_prompt}

[사용자 수정 요구사항]
{user_request}

[문서 샘플]
{samples}"""
        return self._generate(request)

    def _generate(self, request: str) -> str:
        draft = self.prompt_ai.generate(request)
        if not isinstance(draft, str) or not draft.strip():
            raise InvalidResponseError("Prompt 작성 AI가 빈 Draft를 반환했습니다.")
        return draft.strip()
