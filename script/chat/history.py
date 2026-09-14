"""프로그램 실행 중의 대화 기록을 메모리에 보관합니다."""


class ConversationHistory:
    def __init__(self) -> None:
        self._messages: list[dict[str, str]] = []

    def add_user_message(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def get_messages(self) -> list[dict[str, str]]:
        # 호출한 코드가 내부 기록을 실수로 변경하지 않도록 복사본을 반환합니다.
        return [message.copy() for message in self._messages]

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)
