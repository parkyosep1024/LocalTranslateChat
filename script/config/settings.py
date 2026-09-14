"""환경변수와 .env 파일에서 챗봇 설정을 읽습니다."""

from dataclasses import dataclass
import os
from pathlib import Path

from script.utils.exceptions import ConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_API_URL = "https://generativelanguage.googleapis.com/v1beta"


def _read_env_file(path: Path) -> dict[str, str]:
    """간단한 KEY=VALUE 형식의 .env 파일을 읽습니다.

    실제 운영체제 환경변수는 ``Settings.from_env``에서 이 값보다 우선합니다.
    """

    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value

    return values


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    api_url: str = DEFAULT_API_URL
    timeout: float = 30.0

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        env_path = env_file or PROJECT_ROOT / ".env"
        if env_file is None and not env_path.is_file():
            # 기존 스캐폴드에 있던 script/.env도 이전 호환용으로 지원합니다.
            env_path = PROJECT_ROOT / "script" / ".env"
        file_values = _read_env_file(env_path)

        def get_value(name: str, default: str = "") -> str:
            return os.environ.get(name, file_values.get(name, default)).strip()

        def get_compatible_value(primary: str, legacy: str) -> str:
            return get_value(primary) or get_value(legacy)

        timeout_text = get_compatible_value("GEMINI_TIMEOUT", "LLM_TIMEOUT") or "30"
        try:
            timeout = float(timeout_text)
        except ValueError as error:
            raise ConfigurationError("GEMINI_TIMEOUT은 숫자로 설정해 주세요.") from error
        if timeout <= 0:
            raise ConfigurationError("GEMINI_TIMEOUT은 0보다 큰 숫자여야 합니다.")

        return cls(
            api_key=get_compatible_value("GEMINI_API_KEY", "LLM_API_KEY"),
            model=get_compatible_value("GEMINI_MODEL", "LLM_MODEL"),
            api_url=(
                get_compatible_value("GEMINI_API_URL", "LLM_API_URL")
                or DEFAULT_API_URL
            ),
            timeout=timeout,
        )

    def validate(self) -> None:
        missing = []
        if not self.api_key:
            missing.append("GEMINI_API_KEY")
        if not self.model:
            missing.append("GEMINI_MODEL")
        if not self.api_url:
            missing.append("GEMINI_API_URL")
        if missing:
            names = ", ".join(missing)
            raise ConfigurationError(
                f"필수 설정이 없습니다: {names}. .env 파일을 확인해 주세요."
            )
