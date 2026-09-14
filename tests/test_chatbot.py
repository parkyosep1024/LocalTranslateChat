import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from script.chat.chat_engine import ChatEngine
from script.chat.history import ConversationHistory
from script.config.settings import Settings
from script.main import run_chat
from script.providers.gemini import GeminiClient
from script.utils.exceptions import (
    APIRequestError,
    ConfigurationError,
    InvalidResponseError,
)


class FakeResponse:
    def __init__(self, data: dict) -> None:
        self._body = json.dumps(data).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class RecordingClient:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)
        self.requests: list[list[dict[str, str]]] = []

    def create_chat_completion(self, messages: list[dict[str, str]]) -> str:
        self.requests.append([message.copy() for message in messages])
        return next(self.answers)


class FailingClient:
    def create_chat_completion(self, _messages: list[dict[str, str]]) -> str:
        raise APIRequestError("테스트 요청 실패")


class SettingsTests(unittest.TestCase):
    def test_reads_api_settings_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "GEMINI_API_KEY=test-key\nGEMINI_MODEL=gemini-test\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = Settings.from_env(env_file)

        self.assertEqual(settings.api_key, "test-key")
        self.assertEqual(settings.model, "gemini-test")

    def test_missing_required_settings_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {}, clear=True):
                settings = Settings.from_env(root / ".env")

        with self.assertRaisesRegex(
            ConfigurationError, "GEMINI_API_KEY, GEMINI_MODEL"
        ):
            settings.validate()


class ConversationTests(unittest.TestCase):
    def test_history_is_sent_with_next_message(self) -> None:
        client = RecordingClient(["TCP 설명", "쉬운 설명"])
        engine = ChatEngine(client)  # type: ignore[arg-type]

        engine.chat("TCP가 뭐야?")
        engine.chat("그거 쉽게 설명해줘")

        second_request = client.requests[1]
        self.assertEqual(
            second_request[1:],
            [
                {"role": "user", "content": "TCP가 뭐야?"},
                {"role": "assistant", "content": "TCP 설명"},
                {"role": "user", "content": "그거 쉽게 설명해줘"},
            ],
        )

    def test_failed_request_is_not_saved(self) -> None:
        history = ConversationHistory()
        engine = ChatEngine(FailingClient(), history)  # type: ignore[arg-type]

        with self.assertRaises(APIRequestError):
            engine.chat("실패할 질문")

        self.assertEqual(history.get_messages(), [])

    def test_general_ai_requests_use_the_same_chat_path(self) -> None:
        requests = [
            "파이썬에서 리스트와 튜플의 차이를 알려줘.",
            "HTTP가 무엇인지 초보자도 이해할 수 있게 설명해줘.",
            "다음 글을 세 문장으로 요약해줘. 테스트 글",
            '"Welcome to the game."을 한국어로 번역해줘.',
        ]
        client = RecordingClient(["답변"] * len(requests))
        engine = ChatEngine(client)  # type: ignore[arg-type]

        for message in requests:
            engine.chat(message)

        forwarded_messages = [request[-1]["content"] for request in client.requests]
        self.assertEqual(forwarded_messages, requests)


class GeminiTests(unittest.TestCase):
    def test_extracts_answer_and_converts_chat_messages(self) -> None:
        captured_body: dict = {}
        captured_url = ""
        captured_api_key = ""

        def opener(request, **_kwargs):
            nonlocal captured_api_key, captured_url
            captured_url = request.full_url
            captured_api_key = request.get_header("X-goog-api-key")
            captured_body.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse(
                {
                    "candidates": [
                        {"content": {"parts": [{"text": " 테스트 답변 "}]}}
                    ]
                }
            )

        settings = Settings(api_key="test-key", model="gemini-test")
        client = GeminiClient(settings, opener=opener)
        answer = client.create_chat_completion(
            [
                {"role": "system", "content": "도우미"},
                {"role": "user", "content": "질문"},
                {"role": "assistant", "content": "이전 답변"},
            ]
        )

        self.assertEqual(answer, "테스트 답변")
        self.assertTrue(captured_url.endswith("/models/gemini-test:generateContent"))
        self.assertEqual(captured_api_key, "test-key")
        self.assertEqual(captured_body["systemInstruction"]["parts"][0]["text"], "도우미")
        self.assertEqual(captured_body["contents"][0]["role"], "user")
        self.assertEqual(captured_body["contents"][1]["role"], "model")

    def test_empty_answer_raises_readable_error(self) -> None:
        client = GeminiClient(
            Settings(api_key="test-key", model="gemini-test"),
            opener=lambda *_args, **_kwargs: FakeResponse(
                {"candidates": [{"content": {"parts": [{"text": "  "}]}}]}
            ),
        )

        with self.assertRaisesRegex(InvalidResponseError, "빈 답변"):
            client.create_chat_completion([])

    def test_malformed_response_raises_readable_error(self) -> None:
        client = GeminiClient(
            Settings(api_key="test-key", model="gemini-test"),
            opener=lambda *_args, **_kwargs: FakeResponse({"unexpected": True}),
        )

        with self.assertRaisesRegex(InvalidResponseError, "답변을 찾을 수 없습니다"):
            client.create_chat_completion([])

    def test_network_error_is_converted_to_api_error(self) -> None:
        def failing_opener(*_args, **_kwargs):
            raise URLError("test network failure")

        client = GeminiClient(
            Settings(api_key="test-key", model="gemini-test"),
            opener=failing_opener,
        )

        with self.assertRaisesRegex(APIRequestError, "연결할 수 없습니다"):
            client.create_chat_completion([])


class CliTests(unittest.TestCase):
    def test_clear_and_exit_commands(self) -> None:
        engine = ChatEngine(RecordingClient(["답변"]))  # type: ignore[arg-type]
        inputs = iter(["안녕", "/clear", "/exit"])
        outputs: list[str] = []

        exit_code = run_chat(
            engine,
            input_func=lambda _prompt: next(inputs),
            output_func=outputs.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(engine.history), 0)
        self.assertTrue(any(line == "AI: 답변" for line in outputs))

    def test_api_failure_does_not_stop_input_loop(self) -> None:
        inputs = iter(["질문", "/exit"])
        outputs: list[str] = []

        exit_code = run_chat(
            ChatEngine(FailingClient()),  # type: ignore[arg-type]
            input_func=lambda _prompt: next(inputs),
            output_func=outputs.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(any(line.startswith("오류:") for line in outputs))


if __name__ == "__main__":
    unittest.main()
