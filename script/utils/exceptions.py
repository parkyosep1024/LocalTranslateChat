"""챗봇에서 사용자에게 안내할 수 있는 오류 모음입니다."""


class ChatbotError(Exception):
    """챗봇 실행 중 예상 가능한 오류의 기본 클래스입니다."""


class ConfigurationError(ChatbotError):
    """필수 환경변수가 없거나 올바르지 않을 때 발생합니다."""


class APIRequestError(ChatbotError):
    """외부 LLM API 요청이 실패했을 때 발생합니다."""


class InvalidResponseError(ChatbotError):
    """외부 LLM API 응답에서 답변을 읽을 수 없을 때 발생합니다."""


class LocalLLMError(ChatbotError):
    """Ollama Local LLM 요청이 실패했을 때 발생합니다."""


class FileProcessingError(ChatbotError):
    """번역 파일을 읽거나 저장하지 못했을 때 발생합니다."""


class PromptPresetError(ChatbotError):
    """Prompt Preset을 읽거나 저장하지 못했을 때 발생합니다."""


class TranslationStopped(ChatbotError):
    """사용자가 Chunk 사이에서 번역 중지를 요청했을 때 발생합니다."""
