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


class OllamaStatusWorker(QObject):
    """Ollama /api/tags를 가볍게 확인하는 Worker입니다."""

    finished = Signal(bool)

    def __init__(self, base_url: str, timeout: float = 3.0) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def run(self) -> None:
        import json
        from urllib.request import Request, urlopen

        try:
            request = Request(f"{self._base_url}/api/tags", method="GET")
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            ok = isinstance(payload, dict)
        except Exception:
            ok = False
        self.finished.emit(ok)


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
            self.failed.emit(str(error))
            return
        if getattr(summary, "was_stopped", False):
            self.stopped.emit()
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
