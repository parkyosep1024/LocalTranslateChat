"""QMainWindow + Sidebar + QStackedWidget 공통 셸입니다. 실제 backend에 연결됩니다."""

from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl
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
from script.translation.language_detector import LanguageDetector
from script.translation.prompt_manager import PromptManager, PromptPreset
from script.translation.translator import Translator


def _display_to_language(name: str) -> str:
    mapping = {"한국어": "Korean", "자동 감지": "Unknown"}
    return mapping.get(name, name)


def collect_detection_sample(
    file_paths: list[Path],
    selected_fields: list[str] | None = None,
    per_file_chars: int = 3_000,
    total_chars: int = 12_000,
) -> str:
    """원본 언어 감지용 sample을 실제 선택 파일에서 수집합니다.

    TXT는 실제 텍스트를, CSV/JSON은 GUI에서 선택한 field/key의 실제 값만
    사용합니다. backend를 새로 만들지 않고 기존 handler/FileLoader를 재사용합니다.
    """
    parts: list[str] = []
    remaining = total_chars
    selected = set(selected_fields or [])
    for path in file_paths:
        if remaining <= 0:
            break
        suffix = path.suffix.lower()
        try:
            if suffix == ".txt":
                text = FileLoader.read_text(path)[: min(per_file_chars, remaining)]
            elif suffix in {".csv", ".json"}:
                handler = create_handler(path)
                handler.load(path)
                available = handler.available_fields()
                use = [name for name in available if name in selected] if selected else available
                samples = handler.sample_fields()
                text = "\n".join(
                    value for name in use for value in samples.get(name, [])
                )[: min(per_file_chars, remaining)]
            else:
                continue
        except Exception:
            continue
        if text.strip():
            parts.append(text)
            remaining -= len(text)
    return "\n".join(parts)


def detect_source_language(
    display_name: str,
    file_paths: list[Path],
    selected_fields: list[str] | None = None,
) -> tuple[str, str | None]:
    """콤보 표시값에서 Translator용 source_language를 결정합니다.

    "자동 감지"면 기존 LanguageDetector를 실제 sample에 적용하고,
    직접 선택이면 그대로 사용합니다. 반환: (language, 안내문구|None)
    """
    if display_name != "자동 감지":
        return _display_to_language(display_name), None
    try:
        sample = collect_detection_sample(file_paths, selected_fields)
        detected = LanguageDetector.detect_text(sample) if sample.strip() else "Unknown"
    except Exception:
        detected = "Unknown"
    if detected == "Unknown":
        return "Unknown", "원본 언어를 감지하지 못해 Unknown으로 진행합니다."
    return detected, f"원본 언어 감지: {detected}"


def resolve_prompt_selection(
    index: int, presets: list[PromptPreset]
) -> tuple[str | None, str]:
    """번역 화면 콤보 index를 Preset에 매핑합니다.

    index 0은 항상 기본 Prompt(None)이며, 그 뒤로 presets가 이어집니다.
    범위를 벗어난 index도 기본 Prompt로 안전하게 처리합니다.
    """
    if index <= 0 or not presets:
        return None, "기본 Prompt"
    if index - 1 < len(presets):
        preset = presets[index - 1]
        return preset.prompt, preset.name
    return None, "기본 Prompt"


def resolve_fields_for_handler(
    available: list[str], selected: list[str]
) -> list[str]:
    """handler.select_fields()에 넘길 field를 결정합니다.

    선택된 것만 반환하며, 0개 선택 시 전체 fallback을 하지 않습니다.
    (호출자가 시작 전에 막아야 합니다.)
    """
    chosen = set(selected)
    return [name for name in available if name in chosen]


def find_unmatched_structured_files(
    file_paths: list[Path], selected_fields: list[str]
) -> list[Path]:
    """현재 선택 field와 겹치는 번역 대상이 없는 CSV/JSON을 반환합니다.

    파일마다 schema가 다를 수 있으므로(예: a.csv의 id/text와
    b.json의 speaker/dialogue) 첫 파일 기준으로 전체를 가정하지 않고
    번역 시작 전에 모든 구조화 파일을 검증합니다.
    전체 fallback이나 잘못된 field 자동 번역은 하지 않습니다.
    향후 파일별 field 선택(Schema Analyzer 연동)으로 확장하기 위한 검사 지점입니다.
    """
    selected = set(selected_fields or [])
    offenders: list[Path] = []
    for path in file_paths:
        if path.suffix.lower() not in {".csv", ".json"}:
            continue
        try:
            handler = create_handler(path)
            handler.load(path)
            available = handler.available_fields()
        except Exception:
            # 읽을 수 없는 파일은 시작 후 per-file 실패로 처리합니다.
            continue
        if not any(name in selected for name in available):
            offenders.append(path)
    return offenders


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

        # thread/worker 추적 변수를 먼저 생성합니다.
        # (_check_ollama_status → _launch에서 사용하므로 순서가 바뀌면 crash합니다.)
        self._threads: set[QThread] = set()
        self._workers: set[QObject] = set()
        self._chat_thread: QThread | None = None
        self._chat_worker: ChatWorker | None = None
        self._status_thread: QThread | None = None
        self._translation_thread: QThread | None = None
        self._translation_worker: TranslationWorker | None = None

        self._ollama_connected: bool | None = None
        self._ollama_model_found: bool | None = None

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

    def _launch(self, thread: QThread, worker: QObject, clear_refs: dict = None) -> None:
        """thread 실행 중 GC 방지 + 종료 후 정리 + 재시작 가능하도록 추적합니다.

        clear_refs {attribute: 객체}는 thread 종료 시 일치하는 객체만 None으로
        정리합니다. (삭제된 C++ 객체에 isRunning()을 호출하는 사고를 방지)
        """
        self._threads.add(thread)
        self._workers.add(worker)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda _t=thread: self._threads.discard(_t))
        thread.finished.connect(lambda _w=worker: self._workers.discard(_w))
        for attr, obj in (clear_refs or {}).items():
            thread.finished.connect(lambda _o=obj, _a=attr: self._clear_ref(_a, _o))

    def _clear_ref(self, attr: str, current: object) -> None:
        """종료된 thread/worker reference를 안전하게 None으로 정리합니다."""
        try:
            if getattr(self, attr, None) is current:
                setattr(self, attr, None)
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        """앱 종료 시 running thread 때문에 crash하지 않도록 정리합니다."""
        try:
            if self._translation_worker is not None:
                self._translation_worker.request_stop()
            for thread in list(self._threads):
                thread.quit()
            for thread in list(self._threads):
                thread.wait(2000)
        except Exception:
            pass
        super().closeEvent(event)

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
        if self._chat_thread is not None and self._chat_thread.isRunning():
            self.chat_page.add_error_message("이전 요청을 처리 중입니다. 잠시 후 다시 시도해주세요.")
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
        self._launch(thread, worker, {"_chat_thread": thread, "_chat_worker": worker})
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
        model = (
            self.translation_settings.model
            if self.translation_settings is not None
            else ""
        )
        thread = QThread(self)
        worker = OllamaStatusWorker(base_url, model)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_ollama_status)
        worker.finished.connect(thread.quit)
        self._launch(thread, worker, {"_status_thread": thread})
        self._status_thread = thread
        thread.start()

    def _on_ollama_status(self, result: object) -> None:
        """세 상태를 구분합니다: 연결+모델 있음 / 연결+모델 없음 / 연결 실패."""
        if isinstance(result, dict):
            connected = bool(result.get("connected", False))
            model_found = bool(result.get("model_found", False))
        else:  # 기존 bool 형태 호환
            connected = bool(result)
            model_found = bool(result)
        self._ollama_connected = connected
        self._ollama_model_found = model_found if connected else False
        if connected and model_found:
            self.sidebar.set_model_status(True, "● 로컬 모델 연결됨")
            self.translation_page.set_engine_status(True)
        elif connected:
            self.sidebar.set_model_status(True, "● Ollama 연결됨")
            self.translation_page.set_engine_status(False, "○ 모델 없음")
        else:
            self.sidebar.set_model_status(False, "○ 로컬 모델 연결 안 됨")
            self.translation_page.set_engine_status(False)

    # ---------- Prompt 목록 (번역 화면 콤보) ----------
    def _refresh_translation_prompts(self) -> None:
        try:
            self._prompt_presets = self.prompt_manager.list_presets()
        except Exception:
            self._prompt_presets = []
        # set_prompt_items()가 항상 "기본 Prompt"를 index 0에 둡니다.
        self.translation_page.set_prompt_items(
            [
                f"{p.source_language} → {p.target_language} · {p.name}"
                for p in self._prompt_presets
            ]
        )
        if self._prompt_presets:
            self.translation_page.set_recommendation(
                "저장된 Prompt에서 선택", f"기본 Prompt + {len(self._prompt_presets)}개 Preset"
            )
        else:
            self.translation_page.set_recommendation(
                "저장된 Prompt 없음", "Prompt 설정에서 새로 만드세요."
            )

    def _selected_translation_prompt(self) -> tuple[str | None, str]:
        index = self.translation_page.prompt_combo.currentIndex()
        return resolve_prompt_selection(index, self._prompt_presets)

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
        if self._ollama_connected is None:
            self.translation_page.set_status(
                "로컬 모델 상태를 확인 중입니다. 잠시 후 다시 시도해주세요."
            )
            return
        if self._ollama_connected is False:
            self.translation_page.set_status(
                "Ollama 서버에 연결할 수 없습니다. Ollama가 실행 중인지 확인해주세요."
            )
            return
        if self._ollama_connected and not self._ollama_model_found:
            self.translation_page.set_status(
                "설정된 Ollama 모델을 찾을 수 없습니다:\n"
                f"{self.translation_settings.model}"
            )
            return
        # CSV/JSON인데 field를 0개 선택했으면 전체 fallback 없이 시작을 막습니다.
        has_structured = any(
            path.suffix.lower() in {".csv", ".json"} for path in file_paths
        )
        selected_fields = self.translation_page.selected_field_names()
        if has_structured and not selected_fields:
            self.translation_page.set_status(
                "CSV/JSON 번역 대상 field를 1개 이상 선택해주세요. "
                "선택하지 않은 컬럼/Key는 번역하지 않습니다."
            )
            return
        # 서로 다른 schema의 파일이 섞여 있으면 조용히 오번역하지 않고 시작을 막습니다.
        unmatched = find_unmatched_structured_files(file_paths, selected_fields)
        if unmatched:
            names = "\n".join(f"· {path.name}" for path in unmatched)
            self.translation_page.set_status(
                "다음 파일에 선택한 field와 일치하는 번역 대상이 없습니다:\n"
                f"{names}\n번역을 시작하지 않았습니다."
            )
            return
        if self._translation_thread is not None and self._translation_thread.isRunning():
            return
        self.translation_page.set_translation_state("running")
        self.translation_page.set_progress(0, "번역 준비 중…")

        source, notice = detect_source_language(
            self.translation_page.source_combo.currentText(),
            file_paths,
            selected_fields,
        )
        if notice:
            self.translation_page.set_status(notice)
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
        self._launch(
            thread, worker,
            {"_translation_thread": thread, "_translation_worker": worker},
        )
        self._translation_thread = thread
        self._translation_worker = worker
        thread.start()

    def _build_handlers(self, file_paths: list[Path], chunk_max_chars: int) -> dict:
        """CSV/JSON은 체크된 field만, TXT는 기본 핸들러로 번역합니다.

        선택이 0개면 전체 fallback을 하지 않고 해당 파일을 건너뜁니다.
        (시작 전에 막히므로 여기는 방어용입니다.)
        """
        handlers: dict = {}
        selected = self.translation_page.selected_field_names()
        for path in file_paths:
            try:
                handler = create_handler(path, chunk_max_chars)
                handler.load(path)
                available = handler.available_fields()
                if path.suffix.lower() in {".csv", ".json"} and available:
                    use = resolve_fields_for_handler(available, selected)
                    if not use:
                        continue
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
