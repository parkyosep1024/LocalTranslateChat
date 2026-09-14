"""대화 기록과 외부 LLM 호출을 연결합니다."""

from script.chat.history import ConversationHistory
from script.chat.prompt import DEFAULT_SYSTEM_PROMPT
from script.providers.api_client import ChatMessage
from script.providers.gemini import GeminiClient


class ChatEngine:
    def __init__(
        self,
        client: GeminiClient,
        history: ConversationHistory | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.client = client
        self.history = history if history is not None else ConversationHistory()
        self.system_prompt = system_prompt

    def chat(self, user_message: str) -> str:
        message = user_message.strip()
        if not message:
            raise ValueError("메시지를 입력해 주세요.")

        messages: list[ChatMessage] = [
            {"role": "system", "content": self.system_prompt}
        ]
        messages.extend(self.history.get_messages())
        messages.append({"role": "user", "content": message})

        # 요청이 성공한 경우에만 이번 대화를 기록합니다.
        answer = self.client.create_chat_completion(messages)
        self.history.add_user_message(message)
        self.history.add_assistant_message(answer)
        return answer

    def clear_history(self) -> None:
        self.history.clear()
