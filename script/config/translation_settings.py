"""Ollama TXT 번역 기능의 설정을 관리합니다."""

from dataclasses import dataclass
import os
from pathlib import Path

from script.config.settings import PROJECT_ROOT, _read_env_file
from script.utils.exceptions import ConfigurationError


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_TIMEOUT = 300.0
DEFAULT_CHUNK_MAX_CHARS = 10_000
INPUT_DIR = PROJECT_ROOT / "setting" / "input_txt"
OUTPUT_DIR = PROJECT_ROOT / "setting" / "output_txt"


@dataclass(frozen=True)
class TranslationSettings:
    model: str
    base_url: str = DEFAULT_OLLAMA_BASE_URL
    timeout: float = DEFAULT_OLLAMA_TIMEOUT
    chunk_max_chars: int = DEFAULT_CHUNK_MAX_CHARS
    input_dir: Path = INPUT_DIR
    output_dir: Path = OUTPUT_DIR

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "TranslationSettings":
        env_path = env_file or PROJECT_ROOT / ".env"
        if env_file is None and not env_path.is_file():
            env_path = PROJECT_ROOT / "script" / ".env"
        file_values = _read_env_file(env_path)

        def get_value(name: str, default: str = "") -> str:
            return os.environ.get(name, file_values.get(name, default)).strip()

        timeout_text = get_value("OLLAMA_TIMEOUT", str(DEFAULT_OLLAMA_TIMEOUT))
        try:
            timeout = float(timeout_text)
        except ValueError as error:
            raise ConfigurationError("OLLAMA_TIMEOUT은 숫자로 설정해 주세요.") from error
        if timeout <= 0:
            raise ConfigurationError("OLLAMA_TIMEOUT은 0보다 큰 숫자여야 합니다.")

        return cls(
            model=get_value("OLLAMA_MODEL"),
            base_url=get_value("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            timeout=timeout,
        )

    def validate(self) -> None:
        if not self.model:
            raise ConfigurationError("OLLAMA_MODEL을 .env 파일에 설정해 주세요.")
        if not self.base_url:
            raise ConfigurationError("OLLAMA_BASE_URL을 .env 파일에 설정해 주세요.")
        if self.chunk_max_chars <= 0:
            raise ConfigurationError("Chunk 최대 문자 수는 0보다 커야 합니다.")
