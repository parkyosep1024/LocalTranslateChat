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

    def replace_messages(self, messages: list[dict[str, str]]) -> None:
        """저장된 대화를 복원할 때 전체 기록을 교체합니다.

        user/assistant 역할과 문자열 content만 허용하고,
        그 외 형식은 ValueError로 거부합니다.
        """
        replaced: list[dict[str, str]] = []
        for message in messages:
            if (
                not isinstance(message, dict)
                or message.get("role") not in {"user", "assistant"}
                or not isinstance(message.get("content"), str)
            ):
                raise ValueError("복원할 수 없는 메시지 형식입니다.")
            replaced.append({"role": message["role"], "content": message["content"]})
        self._messages = replaced

    def __len__(self) -> int:
        return len(self._messages)
