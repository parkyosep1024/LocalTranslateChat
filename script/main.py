"""외부 LLM API 기반 CLI 챗봇 실행 진입점입니다."""

from collections.abc import Callable
from pathlib import Path
import sys


# ``python script/main.py``로 직접 실행한 경우에도 script 패키지를 찾게 합니다.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from script.chat.chat_engine import ChatEngine
from script.config.settings import Settings
from script.providers.gemini import GeminiClient
from script.utils.exceptions import ChatbotError, ConfigurationError


def create_chat_engine() -> ChatEngine:
    settings = Settings.from_env()
    settings.validate()
    return ChatEngine(GeminiClient(settings))


def run_chat(
    engine: ChatEngine | None = None,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> int:
    if engine is None:
        try:
            engine = create_chat_engine()
        except ConfigurationError as error:
            output_func(f"설정 오류: {error}")
            return 1

    output_func("AI Chatbot")
    output_func("종료하려면 /exit 입력, 대화 초기화는 /clear 입력")

    while True:
        try:
            user_message = input_func("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            output_func("\n챗봇을 종료합니다.")
            return 0

        command = user_message.lower()
        if command in {"/exit", "exit"}:
            output_func("챗봇을 종료합니다.")
            return 0
        if command == "/clear":
            engine.clear_history()
            output_func("대화 기록을 초기화했습니다.")
            continue
        if not user_message:
            continue

        try:
            answer = engine.chat(user_message)
            output_func(f"AI: {answer}")
        except ChatbotError as error:
            output_func(f"오류: {error}")


def main() -> int:
    return run_chat()


if __name__ == "__main__":
    raise SystemExit(main())
