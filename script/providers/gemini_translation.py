"""Gemini API 기반 파일 번역 provider입니다.

Translator는 ``.generate(prompt)`` protocol만 사용하므로 LocalLLM과
교체 가능하며, placeholder 보호/복원은 Translator 내부에서 그대로 적용됩니다.
실제 HTTP 통신은 기존 GeminiClient만 재사용합니다.
"""

from script.providers.api_client import ChatMessage
from script.providers.gemini import GeminiClient


TRANSLATION_SYSTEM_PROMPT = (
    "You are a translation engine. "
    "Follow the provided translation instructions exactly. "
    "Return only the translated result."
)


class GeminiTranslationProvider:
    """Translator용 LocalLLM 호환 adapter입니다."""

    def __init__(self, client: GeminiClient) -> None:
        self.client = client

    def generate(self, prompt: str) -> str:
        messages: list[ChatMessage] = [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        return self.client.create_chat_completion(messages)
