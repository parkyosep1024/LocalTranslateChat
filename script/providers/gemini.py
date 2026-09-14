"""Google Gemini GenerateContent API 클라이언트입니다."""

import json
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from script.config.settings import Settings
from script.providers.api_client import ChatMessage
from script.utils.exceptions import APIRequestError, InvalidResponseError


class GeminiClient:
    def __init__(
        self,
        settings: Settings,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.settings = settings
        self._opener = opener

    def create_chat_completion(self, messages: list[ChatMessage]) -> str:
        self.settings.validate()

        request_body = self._build_request_body(messages)
        body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        model = self.settings.model.removeprefix("models/")
        model_path = quote(model, safe="-._")
        api_url = (
            f"{self.settings.api_url.rstrip('/')}/models/"
            f"{model_path}:generateContent"
        )
        request = Request(
            api_url,
            data=body,
            headers={
                "x-goog-api-key": self.settings.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with self._opener(request, timeout=self.settings.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = self._read_error_detail(error)
            message = f"Gemini API 요청이 실패했습니다 (HTTP {error.code})"
            if detail:
                message += f": {detail}"
            raise APIRequestError(message) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            raise APIRequestError(f"Gemini API에 연결할 수 없습니다: {error}") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidResponseError(
                "Gemini API가 올바른 JSON 응답을 보내지 않았습니다."
            ) from error
        except OSError as error:
            raise APIRequestError(
                f"Gemini API 통신 중 오류가 발생했습니다: {error}"
            ) from error

        return self._extract_answer(response_data)

    @staticmethod
    def _build_request_body(messages: list[ChatMessage]) -> dict[str, Any]:
        system_messages: list[str] = []
        contents: list[dict[str, Any]] = []

        for message in messages:
            role = message["role"]
            text = message["content"]
            if role == "system":
                system_messages.append(text)
                continue
            if role == "assistant":
                role = "model"
            if role not in {"user", "model"}:
                raise InvalidResponseError(f"지원하지 않는 메시지 역할입니다: {role}")
            contents.append({"role": role, "parts": [{"text": text}]})

        request_body: dict[str, Any] = {"contents": contents}
        if system_messages:
            request_body["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_messages)}]
            }
        return request_body

    @staticmethod
    def _extract_answer(response_data: Any) -> str:
        try:
            parts = response_data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as error:
            block_reason = GeminiClient._get_block_reason(response_data)
            if block_reason:
                raise InvalidResponseError(
                    f"Gemini가 요청을 차단했습니다: {block_reason}"
                ) from error
            raise InvalidResponseError(
                "Gemini API 응답에서 AI 답변을 찾을 수 없습니다."
            ) from error

        if not isinstance(parts, list):
            raise InvalidResponseError("Gemini API 답변 형식이 올바르지 않습니다.")

        texts = [
            part["text"]
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        answer = "".join(texts).strip()
        if not answer:
            raise InvalidResponseError("Gemini API가 빈 답변을 반환했습니다.")
        return answer

    @staticmethod
    def _get_block_reason(response_data: Any) -> str:
        if not isinstance(response_data, dict):
            return ""
        feedback = response_data.get("promptFeedback", {})
        if not isinstance(feedback, dict):
            return ""
        return str(feedback.get("blockReason", "")).strip()

    @staticmethod
    def _read_error_detail(error: HTTPError) -> str:
        try:
            data = json.loads(error.read().decode("utf-8"))
            detail = data.get("error", {}).get("message", "")
            return str(detail).strip()[:300]
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return ""
