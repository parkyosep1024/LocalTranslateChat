"""Ollama 로컬 Generate API 클라이언트입니다."""

import json
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from script.config.translation_settings import TranslationSettings
from script.utils.exceptions import (
    InvalidResponseError,
    LocalLLMError,
)


class LocalLLM:
    def __init__(
        self,
        settings: TranslationSettings,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.settings = settings
        self._opener = opener

    def generate(self, prompt: str) -> str:
        self.settings.validate()
        api_url = f"{self.settings.base_url.rstrip('/')}/api/generate"
        body = json.dumps(
            {
                "model": self.settings.model,
                "prompt": prompt,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        try:
            request = Request(
                api_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self._opener(request, timeout=self.settings.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = self._read_error_detail(error)
            if error.code == 404 and "model" in detail.lower():
                raise LocalLLMError(
                    f"설정한 Ollama 모델을 찾을 수 없습니다: {self.settings.model}. "
                    f"ollama pull {self.settings.model} 명령으로 설치해 주세요."
                ) from error
            message = f"Ollama API 요청이 실패했습니다 (HTTP {error.code})"
            if detail:
                message += f": {detail}"
            raise LocalLLMError(message) from error
        except (TimeoutError, socket.timeout) as error:
            raise LocalLLMError("Ollama 서버 응답 시간 초과") from error
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise LocalLLMError("Ollama 서버 응답 시간 초과") from error
            raise LocalLLMError(
                "Ollama 서버에 연결할 수 없습니다. Ollama가 실행 중인지 확인해 주세요."
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidResponseError(
                "Ollama API가 올바른 JSON 응답을 보내지 않았습니다."
            ) from error
        except ValueError as error:
            raise LocalLLMError("Ollama API 주소가 올바르지 않습니다.") from error
        except OSError as error:
            raise LocalLLMError(
                f"Ollama 통신 중 오류가 발생했습니다: {error}"
            ) from error

        return self._extract_response(response_data)

    @staticmethod
    def _extract_response(response_data: Any) -> str:
        if not isinstance(response_data, dict) or "response" not in response_data:
            raise InvalidResponseError(
                "Ollama API 응답에서 번역 결과를 찾을 수 없습니다."
            )

        translated_text = response_data["response"]
        if not isinstance(translated_text, str):
            raise InvalidResponseError("Ollama API 응답 형식이 올바르지 않습니다.")

        if not translated_text.strip():
            raise InvalidResponseError("Ollama API가 빈 번역 결과를 반환했습니다.")
        return translated_text

    @staticmethod
    def _read_error_detail(error: HTTPError) -> str:
        try:
            data = json.loads(error.read().decode("utf-8"))
            detail = data.get("error", "")
            if isinstance(detail, dict):
                detail = detail.get("message", "")
            return str(detail).strip()[:300]
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return ""
