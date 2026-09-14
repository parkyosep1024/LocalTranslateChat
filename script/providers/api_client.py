"""외부 LLM 클라이언트에서 공통으로 사용하는 메시지 타입입니다."""

from typing import TypedDict


class ChatMessage(TypedDict):
    role: str
    content: str
