"""QThread에서 실행되는 실제 backend Worker 모음입니다.

main thread에서 API/번역 요청을 직접 실행하지 않기 위해 사용합니다.
기존 backend 클래스(ChatEngine, Translator 등)를 재사용하며,
Worker는 signal 전달과 stop flag 연결만 담당합니다.
"""

from pathlib import Path
import threading

from PySide6.QtCore import QObject, Signal

from script.config.translation_settings import TranslationSettings


class ChatWorker(QObject):
    """Gemini ChatEngine.chat 비동기 실행용 Worker입니다."""

    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, engine, message: str) -> None:
        super().__init__()
        self._engine = engine
        self._message = message

    def run(self) -> None:
        try:
            answer = self._engine.chat(self._message)
        except Exception as error:  # GUI 프로세스를 죽이지 않고 signal로 전달합니다.
            self.failed.emit(str(error))
        else:
            self.finished.emit(answer)


def ollama_model_found(payload: object, model: str) -> bool:
    """`/api/tags` 응답에 설정된 모델이 설치되어 있는지 확인합니다.

    태그(`:latest` 등)가 생략된 설정값은 같은 base 이름이면 존재로 인정합니다.
    태그까지 지정된 경우에는 정확히 일치해야 합니다.
    """
    if not isinstance(payload, dict) or not model or not model.strip():
        return False
    models = payload.get("models")
    if not isinstance(models, list):
        return False
    want = model.strip()
    want_base = want.split(":")[0].casefold()
    want_has_tag = ":" in want
    for entry in models:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name == want:
            return True
        if not want_has_tag and name.split(":")[0].casefold() == want_base:
            return True
    return False


def fetch_ollama_status(
    base_url: str,
    model: str = "",
    timeout: float = 3.0,
    opener=None,
) -> dict:
    """Ollama 서버 연결과 설정 모델 존재 여부를 함께 확인합니다.

    반환: {"connected": bool, "model_found": bool}
    어떤 실패에서도 예외를 던지지 않고 dict만 반환합니다.
    """
    import json
    from urllib.request import Request, urlopen

    opener = opener or urlopen
    try:
        request = Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {"connected": False, "model_found": False}
    if not isinstance(payload, dict):
        return {"connected": False, "model_found": False}
    return {"connected": True, "model_found": ollama_model_found(payload, model)}


class OllamaStatusWorker(QObject):
    """Ollama /api/tags를 가볍게 확인하는 Worker입니다."""

    finished = Signal(object)  # {"connected": bool, "model_found": bool}

    def __init__(self, base_url: str, model: str = "", timeout: float = 3.0) -> None:
        super().__init__()
        self._base_url = base_url
        self._model = model
        self._timeout = timeout

    def run(self) -> None:
        self.finished.emit(fetch_ollama_status(self._base_url, self._model, self._timeout))


class TranslationWorker(QObject):
    """Translator.translate_all 실행용 Worker입니다."""

    progress = Signal(int)
    current_file = Signal(str)
    status = Signal(str)
    finished = Signal(object)  # TranslationSummary
    failed = Signal(str)
    stopped = Signal()

    def __init__(
        self,
        translator_factory,
        file_paths: list[Path],
    ) -> None:
        super().__init__()
        self._translator_factory = translator_factory
        self._file_paths = list(file_paths)
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def run(self) -> None:
        try:
            translator = self._translator_factory(
                stop_requested=self.stop_requested,
                on_progress=self._on_progress,
                file_paths=self._file_paths,
            )
            summary = translator.translate_all(output_func=lambda _msg: None)
        except Exception as error:
            # 예외 종료: failed만 emit하고 finished/stopped는 emit하지 않습니다.
            self.failed.emit(str(error))
            return
        if getattr(summary, "was_stopped", False):
            # 사용자 중지: stopped만 emit하고 finished는 emit하지 않습니다.
            # (둘 다 emit하면 GUI가 "중지됨" 위로 "완료 100%"를 덮어씁니다.)
            self.stopped.emit()
            return
        self.finished.emit(summary)

    def _on_progress(
        self,
        file_path: Path,
        current_unit: int,
        total_units: int,
        file_index: int,
        total_files: int,
    ) -> None:
        # 가짜 남은 시간은 계산하지 않고 실제 값만 전달합니다.
        if total_units > 0:
            file_ratio = current_unit / total_units
            overall = int(((file_index - 1 + file_ratio) / max(total_files, 1)) * 100)
        else:
            overall = int((file_index / max(total_files, 1)) * 100)
        self.current_file.emit(file_path.name)
        self.progress.emit(max(0, min(100, overall)))
        self.status.emit(f"[{file_index}/{total_files}] {file_path.name} · {current_unit}/{total_units} 단위")


def default_translation_settings() -> TranslationSettings | None:
    """TranslationSettings 로드 실패 시 None을 반환합니다."""
    try:
        settings = TranslationSettings.from_env()
        settings.validate()
        return settings
    except Exception:
        return None
