"""QMainWindow + Sidebar + QStackedWidget 공통 셸입니다. 실제 backend에 연결됩니다."""

from pathlib import Path

from PySide6.QtCore import QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from script.chat.chat_engine import ChatEngine
from script.config.settings import Settings
from script.config.translation_settings import TranslationSettings
from script.gui.pages.chat_page import ChatPage
from script.gui.pages.prompt_page import PromptPage
from script.gui.pages.translation_page import TranslationPage
from script.gui.widgets.sidebar import Sidebar
from script.gui.workers import ChatWorker, OllamaStatusWorker, TranslationWorker
from script.providers.gemini import GeminiClient
from script.providers.local_llm import LocalLLM
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
from script.translation.formats import create_handler
from script.translation.prompt_manager import PromptManager, PromptPreset
from script.translation.translator import Translator


def _display_to_language(name: str) -> str:
    mapping = {"한국어": "Korean", "자동 감지": "Unknown"}
    return mapping.get(name, name)


class MainWindow(QMainWindow):
    """1200x800 기준 데스크톱 셸입니다. OS 네이티브 타이틀바를 사용합니다."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Local Translate Chat")
        self.resize(1200, 800)
        self.setMinimumSize(1024, 640)

        self.chat_engine: ChatEngine | None = self._create_chat_engine()
        self.prompt_manager = PromptManager()
        self.translation_settings: TranslationSettings | None = self._load_translation_settings()
        self._prompt_presets: list[PromptPreset] = []

        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.chat_page = ChatPage()
        self.translation_page = TranslationPage()
        self.prompt_page = PromptPage(manager=self.prompt_manager)

        self.stack.addWidget(self.chat_page)  # 0
        self.stack.addWidget(self.translation_page)  # 1
        self.stack.addWidget(self.prompt_page)  # 2

        self.sidebar.page_requested.connect(self.set_page)
        self.set_page(Sidebar.TRANSLATION)

        # Chat 연결
        model_name = self._chat_model_name()
        self.chat_page.set_model_info(model_name)
        self.chat_page.send_requested.connect(self._on_chat_send)
        self.chat_page.new_chat_requested.connect(self._on_new_chat)
        self.chat_page.clear_requested.connect(self._on_chat_cleared)

        # 번역 연결
        self.translation_page.start_requested.connect(self._on_translation_start)
        self.translation_page.stop_requested.connect(self._on_translation_stop)
        self.translation_page.open_output_requested.connect(self._on_open_output)
        self.translation_page.files_changed.connect(self._on_files_changed)
        self._refresh_translation_prompts()

        # Ollama 상태 백그라운드 확인 (GUI 시작을 막지 않음)
        self._check_ollama_status()

        self._chat_thread: QThread | None = None
        self._chat_worker: ChatWorker | None = None
        self._status_thread: QThread | None = None
        self._translation_thread: QThread | None = None
        self._translation_worker: TranslationWorker | None = None

    # ---------- 페이지 ----------
    def set_page(self, index: int) -> None:
        if index == Sidebar.TRANSLATION:
            self._refresh_translation_prompts()
        self.stack.setCurrentIndex(index)
        self.sidebar.set_active(index)

    # ---------- Chat backend ----------
    def _create_chat_engine(self) -> ChatEngine | None:
        try:
            settings = Settings.from_env()
            settings.validate()
            return ChatEngine(GeminiClient(settings))
        except Exception:
            return None

    def _chat_model_name(self) -> str:
        if self.chat_engine is None:
            return "-"
        return getattr(self.chat_engine.client.settings, "model", "-") or "-"

    def _on_new_chat(self) -> None:
        if self.chat_engine is not None:
            try:
                self.chat_engine.clear_history()
            except Exception:
                pass
        self.chat_page.clear_messages()

    def _on_chat_cleared(self) -> None:
        if self.chat_engine is not None:
            try:
                self.chat_engine.clear_history()
            except Exception:
                pass

    def _on_chat_send(self, text: str) -> None:
        if self.chat_engine is None:
            self.chat_page.add_error_message(
                "Gemini API 설정이 없습니다. .env의 GEMINI_API_KEY / GEMINI_MODEL을 확인해주세요."
            )
            return
        self.chat_page.set_sending(True)
        thread = QThread(self)
        worker = ChatWorker(self.chat_engine, text)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_chat_answer)
        worker.failed.connect(self._on_chat_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._chat_thread = thread
        self._chat_worker = worker
        thread.start()

    def _on_chat_answer(self, answer: str) -> None:
        self.chat_page.add_ai_message(answer)
        self.chat_page.set_sending(False)

    def _on_chat_failed(self, error: str) -> None:
        self.chat_page.add_error_message(f"요청 실패: {error}")
        self.chat_page.set_sending(False)

    # ---------- Ollama 상태 ----------
    def _load_translation_settings(self) -> TranslationSettings | None:
        try:
            settings = TranslationSettings.from_env()
            settings.validate()
            return settings
        except Exception:
            return None

    def _check_ollama_status(self) -> None:
        base_url = (
            self.translation_settings.base_url
            if self.translation_settings is not None
            else "http://localhost:11434"
        )
        thread = QThread(self)
        worker = OllamaStatusWorker(base_url)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_ollama_status)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._status_thread = thread
        thread.start()

    def _on_ollama_status(self, connected: bool) -> None:
        self.sidebar.set_model_status(
            connected, "● 로컬 모델 연결됨" if connected else "○ 로컬 모델 연결 안 됨"
        )
        self.translation_page.set_engine_status(connected)

    # ---------- Prompt 목록 (번역 화면 콤보) ----------
    def _refresh_translation_prompts(self) -> None:
        try:
            self._prompt_presets = self.prompt_manager.list_presets()
        except Exception:
            self._prompt_presets = []
        if self._prompt_presets:
            items = [
                f"{p.source_language} → {p.target_language} · {p.name}"
                for p in self._prompt_presets
            ]
            self.translation_page.set_prompt_items(items)
            self.translation_page.set_recommendation(
                "저장된 Prompt에서 선택", f"총 {len(self._prompt_presets)}개 Preset"
            )
        else:
            self.translation_page.set_prompt_items([])
            self.translation_page.set_recommendation(
                "저장된 Prompt 없음", "Prompt 설정에서 새로 만드세요."
            )

    def _selected_translation_prompt(self) -> tuple[str | None, str]:
        if not self._prompt_presets:
            return None, "기본 Prompt"
        index = self.translation_page.prompt_combo.currentIndex()
        if 0 <= index < len(self._prompt_presets):
            preset = self._prompt_presets[index]
            return preset.prompt, preset.name
        return None, "기본 Prompt"

    # ---------- 번역 실행 ----------
    def _on_files_changed(self, paths: object) -> None:
        file_paths = list(paths) if isinstance(paths, list) else []
        self.translation_page.clear_fields()
        for path in file_paths:
            if isinstance(path, Path) and path.suffix.lower() in {".csv", ".json"}:
                fields = self._inspect_fields(path)
                if fields:
                    self.translation_page.set_fields(fields, [name for name, _ in fields])
                break

    def _inspect_fields(self, path: Path) -> list[tuple[str, str]]:
        try:
            handler = create_handler(path)
            handler.load(path)
            available = handler.available_fields()
            samples = handler.sample_fields()
            result = []
            for name in available:
                values = samples.get(name, [])
                result.append((name, " · ".join(values[:2]) if values else "-"))
            return result
        except Exception:
            return []

    def _on_translation_start(self) -> None:
        if self.translation_settings is None:
            self.translation_page.set_status("OLLAMA_MODEL 등 번역 설정을 .env에 입력해주세요.")
            return
        file_paths = self.translation_page.selected_paths()
        if not file_paths:
            self.translation_page.set_status("번역할 파일을 먼저 추가해주세요.")
            return
        if self._translation_thread is not None and self._translation_thread.isRunning():
            return
        self.translation_page.set_translation_state("running")
        self.translation_page.set_progress(0, "번역 준비 중…")

        source = _display_to_language(self.translation_page.source_combo.currentText())
        target = _display_to_language(self.translation_page.target_combo.currentText())
        prompt_text, prompt_name = self._selected_translation_prompt()
        settings = self.translation_settings

        def factory(stop_requested=None, on_progress=None, file_paths=None):
            handlers = self._build_handlers(file_paths or [], settings.chunk_max_chars)
            return Translator(
                loader=FileLoader(settings.input_dir),
                local_llm=LocalLLM(settings),
                writer=FileWriter(settings.output_dir),
                chunk_max_chars=settings.chunk_max_chars,
                source_language=source,
                target_language=target,
                selected_prompt=prompt_text,
                prompt_name=prompt_name,
                stop_requested=stop_requested,
                handlers=handlers,
                file_paths=list(file_paths or []),
                on_progress=on_progress,
            )

        thread = QThread(self)
        worker = TranslationWorker(factory, file_paths)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(lambda value: self.translation_page.set_progress(value))
        worker.current_file.connect(
            lambda name: self.translation_page.set_current_file(name)
        )
        worker.status.connect(self.translation_page.set_status)
        worker.finished.connect(self._on_translation_finished)
        worker.failed.connect(self._on_translation_failed)
        worker.stopped.connect(self._on_translation_stopped)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.stopped.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._translation_thread = thread
        self._translation_worker = worker
        thread.start()

    def _build_handlers(self, file_paths: list[Path], chunk_max_chars: int) -> dict:
        """CSV/JSON은 체크된 field만, TXT는 기본 핸들러로 번역합니다."""
        handlers: dict = {}
        selected = set(self.translation_page.selected_field_names())
        for path in file_paths:
            try:
                handler = create_handler(path, chunk_max_chars)
                handler.load(path)
                available = handler.available_fields()
                if path.suffix.lower() in {".csv", ".json"} and available:
                    use = [name for name in available if name in selected] or available
                    handler.select_fields(use)
                handlers[path] = handler
            except Exception:
                continue
        return handlers

    def _on_translation_finished(self, summary: object) -> None:
        self.translation_page.set_translation_state("done")
        succeeded = getattr(summary, "succeeded", "-")
        failed = getattr(summary, "failed", "-")
        self.translation_page.set_progress(100, f"완료 · 성공 {succeeded} / 실패 {failed}")
        self.translation_page.set_current_file("-")
        self._translation_worker = None

    def _on_translation_failed(self, error: str) -> None:
        self.translation_page.set_translation_state("stopped")
        self.translation_page.set_status(f"번역 실패: {error}")
        self._translation_worker = None

    def _on_translation_stopped(self) -> None:
        self.translation_page.set_translation_state("stopped")
        self.translation_page.set_status("사용자 요청으로 중지되었습니다.")
        self._translation_worker = None

    def _on_translation_stop(self) -> None:
        if self._translation_worker is not None:
            self._translation_worker.request_stop()
            self.translation_page.set_status("중지 요청 중…")

    def _on_open_output(self) -> None:
        output_dir = (
            self.translation_settings.output_dir
            if self.translation_settings is not None
            else Path("setting/output")
        )
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_dir.resolve())))
        except Exception:
            pass
