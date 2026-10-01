"""QMainWindow + Sidebar + QStackedWidget 공통 셸입니다. 실제 backend에 연결됩니다."""

from pathlib import Path
import time

from PySide6.QtCore import QObject, QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from script.chat.chat_engine import ChatEngine
from script.chat.session_store import ChatSessionStore
from script.config.settings import Settings
from script.config.translation_settings import INPUT_DIR, TranslationSettings
from script.gui.pages.chat_page import ChatPage
from script.gui.pages.prompt_page import PromptPage
from script.gui.pages.translation_page import TranslationPage
from script.gui.widgets.sidebar import Sidebar
from script.gui.workers import (
    ChatWorker,
    OllamaStatusWorker,
    PromptDraftWorker,
    SchemaAnalysisWorker,
    TranslationWorker,
)
from script.providers.gemini import GeminiClient
from script.providers.gemini_translation import GeminiTranslationProvider
from script.providers.local_llm import LocalLLM
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
from script.translation.formats import create_handler
from script.translation.language_detector import LanguageDetector
from script.translation.prompt_builder import GeminiPromptAI, PromptBuilder
from script.translation.prompt_manager import PromptManager, PromptPreset
from script.translation.schema_analyzer import (
    GeminiSchemaAI,
    SchemaAnalyzer,
    SchemaCache,
)
from script.translation.translator import BASIC_TRANSLATION_PROMPT, Translator
from script.utils.exceptions import ChatbotError


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


def validate_per_file_selections(
    file_paths: list[Path], selections: dict[Path, list[str]]
) -> list[Path]:
    """번역 대상이 비어 있는 CSV/JSON 파일을 반환합니다.

    파일마다 schema가 다를 수 있으므로(예: a.csv의 id/text와
    b.json의 speaker/dialogue) 파일별 선택값을 검사합니다.
    전체 fallback이나 잘못된 field 자동 번역은 하지 않습니다.
    TXT는 검사 대상이 아닙니다.
    """
    missing: list[Path] = []
    for path in file_paths:
        if path.suffix.lower() not in {".csv", ".json"}:
            continue
        if not selections.get(path):
            missing.append(path)
    return missing


def resolve_prompt_view(
    index: int,
    presets: list[PromptPreset],
    target_language: str = "Korean",
) -> tuple[str, str, str]:
    """Prompt 내용 확인 다이얼로그용 (제목, 부제, 본문)을 반환합니다.

    index 0(기본 Prompt)이면 BASIC_TRANSLATION_PROMPT를,
    그 외에는 선택한 Preset의 실제 prompt를 보여줍니다.
    선택 상태는 바꾸지 않습니다.
    """
    text, name = resolve_prompt_selection(index, presets)
    if text is None:
        target = _display_to_language(target_language)
        body = BASIC_TRANSLATION_PROMPT.format(target_language=target).strip()
        return ("기본 Prompt", f"Translator 기본 번역 규칙 · 목표 언어 {target}", body)
    preset = presets[index - 1] if 0 < index <= len(presets) else None
    if preset is None:
        return ("기본 Prompt", "Translator 기본 번역 규칙", name)
    subtitle = (
        f"{preset.source_language} → {preset.target_language} / {preset.document_type}"
    )
    return (preset.name, subtitle, preset.prompt)


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
        self.session_store = ChatSessionStore()
        self._current_session_id: str | None = None
        self._gemini_available = self._check_gemini_available()

        # thread/worker 추적 변수를 먼저 생성합니다.
        # (_check_ollama_status → _launch에서 사용하므로 순서가 바뀌면 crash합니다.)
        self._threads: set[QThread] = set()
        self._workers: set[QObject] = set()
        self._chat_thread: QThread | None = None
        self._chat_worker: ChatWorker | None = None
        self._status_thread: QThread | None = None
        self._translation_thread: QThread | None = None
        self._translation_worker: TranslationWorker | None = None
        self._prompt_thread: QThread | None = None
        self._prompt_worker: PromptDraftWorker | None = None

        self._ollama_connected: bool | None = None
        self._ollama_model_found: bool | None = None

        # CSV/JSON 파일별 번역 대상 선택값입니다. TXT는 영향을 받지 않습니다.
        self.file_field_selections: dict[Path, list[str]] = {}
        self._file_field_info: dict[Path, tuple[list[str], dict]] = {}
        # AI 분석 중인 파일입니다. 결과가 없으므로 전체 선택으로 보여주면 안 됩니다.
        self._schema_pending: set[Path] = set()
        self._displayed_schema_file: Path | None = None
        self._schema_cache = SchemaCache()
        self._pending_draft: dict | None = None

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
        self.chat_page.clear_requested.connect(self._on_chat_clear_requested)
        self.chat_page.export_requested.connect(self._on_chat_export)
        self.chat_page.conversation_selected.connect(self._on_conversation_selected)
        self._refresh_history()

        # 번역 연결
        self.translation_page.start_requested.connect(self._on_translation_start)
        self.translation_page.stop_requested.connect(self._on_translation_stop)
        self.translation_page.open_output_requested.connect(self._on_open_output)
        self.translation_page.open_input_requested.connect(self._on_open_input)
        self.translation_page.files_changed.connect(self._on_files_changed)
        self.translation_page.field_changed.connect(self._on_field_toggled)
        self.translation_page.view_prompt_requested.connect(self._on_view_prompt)
        self.translation_page.new_prompt_requested.connect(self._on_new_prompt)
        self.translation_page.refresh_input_requested.connect(self._load_input_files)
        self.prompt_page.ai_generate_requested.connect(self._on_prompt_page_generate)
        self._refresh_translation_prompts()
        # 기존 setting/input 폴더의 파일을 자동 로드합니다.
        self._load_input_files()

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
        """앱 종료 시 running thread 때문에 crash하지 않도록 정리합니다.

        종료가 무한 대기하지 않도록 전체 5초 deadline 안에서 정리합니다.
        TranslationWorker에는 먼저 중지를 요청하고,
        status/chat worker는 안전하게 종료를 기다립니다.
        """
        try:
            if self._translation_worker is not None:
                self._translation_worker.request_stop()
            deadline = time.monotonic() + 5.0
            for thread in list(self._threads):
                try:
                    thread.quit()
                except Exception:
                    pass
            for thread in list(self._threads):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    thread.wait(max(0, int(remaining * 1000)))
                except Exception:
                    pass
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

    def _check_gemini_available(self) -> bool:
        try:
            settings = Settings.from_env()
            settings.validate()
            return True
        except Exception:
            return False

    def _on_new_chat(self) -> None:
        # 현재 대화는 성공 시마다 이미 저장되므로 새로 시작만 하면 됩니다.
        self._current_session_id = None
        if self.chat_engine is not None:
            try:
                self.chat_engine.clear_history()
            except Exception:
                pass
        self.chat_page.clear_messages()
        self.chat_page.set_conversation_title("새 대화")
        self._refresh_history()

    def _on_chat_clear_requested(self) -> None:
        """저장된 대화를 보고 있으면 삭제 확인 후 영구 삭제합니다."""
        session = (
            self.session_store.get_session(self._current_session_id)
            if self._current_session_id is not None
            else None
        )
        if session is None:
            self._reset_chat_view()
            return
        answer = QMessageBox.question(
            self, "대화 삭제", "현재 대화를 삭제하시겠습니까?"
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.session_store.delete_session(session["id"])
        except Exception:
            pass
        self._reset_chat_view()

    def _reset_chat_view(self) -> None:
        self._current_session_id = None
        if self.chat_engine is not None:
            try:
                self.chat_engine.clear_history()
            except Exception:
                pass
        self.chat_page.clear_messages()
        self.chat_page.set_conversation_title("새 대화")
        self._refresh_history()

    def _refresh_history(self) -> None:
        try:
            sessions = self.session_store.list_sessions()
        except Exception:
            sessions = []
        self.chat_page.set_conversations(sessions, self._current_session_id)

    def _on_conversation_selected(self, session_id: str) -> None:
        session = self.session_store.get_session(session_id)
        if session is None:
            return
        messages = [
            m for m in session.get("messages", [])
            if isinstance(m, dict)
            and m.get("role") in {"user", "assistant"}
            and isinstance(m.get("content"), str)
        ]
        self._current_session_id = session["id"]
        if self.chat_engine is not None:
            try:
                self.chat_engine.history.replace_messages(messages)
            except ValueError:
                try:
                    self.chat_engine.clear_history()
                except Exception:
                    pass
                self._current_session_id = None
                return
        self.chat_page.clear_messages()
        for message in messages:
            if message["role"] == "user":
                self.chat_page.add_user_message(message["content"])
            else:
                self.chat_page.add_ai_message(message["content"])
        self.chat_page.set_conversation_title(session.get("title", "새 대화") or "새 대화")
        self._refresh_history()

    def _on_chat_export(self) -> None:
        # backend history를 단일 source로 사용하므로 오류 bubble은 제외됩니다.
        messages = (
            self.chat_engine.history.get_messages() if self.chat_engine else []
        )
        if not messages:
            self.chat_page.add_error_message("내보낼 대화가 없습니다. 먼저 대화를 시작해주세요.")
            return
        title = self.chat_page.title_label.text().strip() or "새 대화"
        from datetime import date, datetime

        default_name = f"chat_{date.today().isoformat()}_{datetime.now().strftime('%H%M')}.txt"
        path_str, _ = QFileDialog.getSaveFileName(
            self, "대화 내보내기", default_name, "Text (*.txt)"
        )
        if not path_str:
            return
        lines = [
            "Local Translate Chat",
            "",
            f"제목: {title}",
            f"내보낸 날짜: {date.today().isoformat()}",
            "",
        ]
        for message in messages:
            if message.get("role") == "user":
                lines += ["[사용자]", str(message.get("content", "")), ""]
            elif message.get("role") == "assistant":
                lines += ["[AI]", str(message.get("content", "")), ""]
        try:
            Path(path_str).write_text("\n".join(lines), encoding="utf-8")
        except OSError as error:
            self.chat_page.add_error_message(f"대화 저장에 실패했습니다: {error}")

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
        # ChatEngine은 성공 시에만 history를 남기므로 저장해도 안전합니다.
        if self.chat_engine is not None:
            try:
                saved_id = self.session_store.save_session(
                    self.chat_engine.history.get_messages(),
                    session_id=self._current_session_id,
                )
            except Exception:
                saved_id = None
            if saved_id is not None:
                self._current_session_id = saved_id
                session = self.session_store.get_session(saved_id)
                if session is not None:
                    self.chat_page.set_conversation_title(
                        session.get("title", "새 대화") or "새 대화"
                    )
                self._refresh_history()

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
        elif self._gemini_available:
            self.sidebar.set_model_status(False, "○ 로컬 모델 미연결 · API 번역 가능")
            self.translation_page.set_engine_status(False, "○ 엔진 확인 필요")
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

    # ---------- setting/input 자동 로드 ----------
    def _input_loader(self) -> FileLoader:
        input_dir = (
            self.translation_settings.input_dir
            if self.translation_settings is not None
            else INPUT_DIR
        )
        return FileLoader(input_dir)

    def _load_input_files(self, loader: FileLoader | None = None) -> int:
        """setting/input의 지원 파일을 목록에 자동 추가합니다.

        새 파일만 추가하고 외부에서 추가한 파일은 삭제하지 않습니다.
        폴더가 없어도 crash하지 않고 0을 반환합니다.
        """
        try:
            files = (loader or self._input_loader()).list_supported_files()
        except Exception:
            return 0
        added = 0
        for path in files:
            if self.translation_page.add_file_path(path):
                added += 1
        return added

    # ---------- 파일별 field 선택 + Schema 분석 ----------
    def _structured_files(self, file_paths: list[Path]) -> list[Path]:
        return [
            path
            for path in file_paths
            if isinstance(path, Path) and path.suffix.lower() in {".csv", ".json"}
        ]

    def _on_files_changed(self, paths: object) -> None:
        file_paths = list(paths) if isinstance(paths, list) else []
        # 제거된 파일의 선택값을 정리합니다.
        current = set(file_paths)
        for old in list(self.file_field_selections):
            if old not in current:
                del self.file_field_selections[old]
        for old in list(self._file_field_info):
            if old not in current:
                del self._file_field_info[old]
        for old in list(self._schema_pending):
            if old not in current:
                self._schema_pending.discard(old)
        structured = self._structured_files(file_paths)
        if self._displayed_schema_file not in structured:
            self._displayed_schema_file = structured[0] if structured else None
        if self._displayed_schema_file is None:
            self.translation_page.clear_fields()
            self.translation_page.set_analysis_status("")
            return
        for path in structured:
            if (
                path not in self.file_field_selections
                and path not in self._schema_pending
            ):
                self._analyze_file_fields(path)
        self._show_displayed_fields()

    def _on_field_toggled(self) -> None:
        """checkbox 변경을 현재 표시 파일의 선택값에 저장합니다."""
        if self._displayed_schema_file is not None:
            self.file_field_selections[self._displayed_schema_file] = (
                self.translation_page.selected_field_names()
            )

    def _show_displayed_fields(self) -> None:
        """현재 표시 대상 파일의 field를 checkbox에 보여줍니다.

        분석 중인 파일은 추천 결과가 없으므로 전체 미선택으로 보여줍니다.
        """
        path = self._displayed_schema_file
        info = self._file_field_info.get(path) if path is not None else None
        if path is None or info is None:
            self.translation_page.clear_fields()
            return
        available, samples = info
        rows = [
            (name, " · ".join(samples.get(name, [])[:2]) or "-")
            for name in available
        ]
        if path in self._schema_pending:
            selected: list[str] = []
        else:
            selected = self.file_field_selections.get(path, list(available))
        self.translation_page.set_fields(rows, selected)

    def _create_schema_analyzer(self) -> SchemaAnalyzer | None:
        """Gemini 설정이 있을 때만 SchemaAnalyzer를 만듭니다."""
        try:
            gemini_settings = Settings.from_env()
            gemini_settings.validate()
            return SchemaAnalyzer(GeminiSchemaAI(GeminiClient(gemini_settings)))
        except Exception:
            return None

    def _analyze_file_fields(self, path: Path) -> None:
        """한 구조화 파일의 번역 대상을 결정합니다.

        순서: SchemaCache hit(즉시 적용, pending 아님)
        → Gemini 백그라운드 분석(pending)
        → Gemini 사용 불가(미선택 + 직접 선택 안내).
        분석 실패 시도 미선택을 유지하고 전체 fallback을 하지 않습니다.

        이미 분석 중이거나 결과가 있으면 다시 시작하지 않습니다.
        (자동 분석 경로 전용 중복 방지이며, 명시적 재분석을 막지 않습니다.)
        """
        if path in self._schema_pending:
            return
        if path in self.file_field_selections:
            return
        try:
            handler = create_handler(path)
            handler.load(path)
            available = handler.available_fields()
            samples = handler.sample_fields()
        except Exception:
            return
        if not available:
            return
        kind = path.suffix.lower().lstrip(".")
        self._file_field_info[path] = (available, samples)
        self._schema_pending.add(path)
        try:
            cached = self._schema_cache.get(kind, available)
        except Exception:
            cached = None
        if cached is not None:
            self._schema_pending.discard(path)
            self.file_field_selections[path] = list(cached.translate_fields)
            if path == self._displayed_schema_file:
                self._show_displayed_fields()
                self.translation_page.set_analysis_status(f"{path.name}: 저장된 분석 적용")
            return
        analyzer = self._create_schema_analyzer()
        if analyzer is None:
            # AI 분석을 사용할 수 없으면 자동 선택 없이 비워 둡니다.
            self._schema_pending.discard(path)
            self.file_field_selections[path] = []
            if path == self._displayed_schema_file:
                self._show_displayed_fields()
                self.translation_page.set_analysis_status(
                    f"{path.name}: AI 분석을 사용할 수 없습니다. 번역할 항목을 직접 선택해주세요."
                )
            return
        self.translation_page.set_analysis_status(f"{path.name}: AI 분석 중...")
        if path == self._displayed_schema_file:
            self._show_displayed_fields()
        worker = SchemaAnalysisWorker(analyzer, str(path), kind, available, samples)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_schema_finished)
        worker.failed.connect(self._on_schema_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._launch(thread, worker)
        thread.start()

    def _on_schema_finished(self, key: object, analysis: object) -> None:
        try:
            path = Path(str(key))
        except Exception:
            return
        self._schema_pending.discard(path)
        if path not in self.translation_page.selected_paths():
            return
        translate_fields = list(getattr(analysis, "translate_fields", []))
        self.file_field_selections[path] = translate_fields
        try:
            info = self._file_field_info.get(path)
            if info is not None:
                kind = path.suffix.lower().lstrip(".")
                self._schema_cache.put(kind, info[0], analysis)
        except Exception:
            pass
        if path == self._displayed_schema_file:
            self._show_displayed_fields()
            self.translation_page.set_analysis_status(
                f"{path.name}: AI 추천 적용됨 (수정 가능)"
            )

    def _on_schema_failed(self, key: object, error: str) -> None:
        try:
            path = Path(str(key))
        except Exception:
            return
        self._schema_pending.discard(path)
        if path not in self.translation_page.selected_paths():
            return
        # 실패 fallback: 전체를 무조건 선택하지 않고 미선택 상태로 둡니다.
        self.file_field_selections[path] = []
        if path == self._displayed_schema_file:
            self._show_displayed_fields()
            self.translation_page.set_analysis_status(
                f"{path.name}: AI 분석 실패, 직접 선택해주세요 ({error})"
            )

    def _sync_displayed_selection(self) -> None:
        displayed = self._displayed_schema_file
        if displayed is not None and displayed not in self._schema_pending:
            self.file_field_selections[displayed] = (
                self.translation_page.selected_field_names()
            )

    def _all_selected_fields(self) -> list[str]:
        """언어 감지 sample용으로 모든 파일의 선택값을 합칩니다."""
        merged: list[str] = []
        for names in self.file_field_selections.values():
            for name in names:
                if name not in merged:
                    merged.append(name)
        return merged

    def _local_engine_ready(self) -> bool:
        """시작 시 1회만 엔진을 결정합니다. 중간 자동 전환은 하지 않습니다."""
        return bool(self._ollama_connected and self._ollama_model_found)

    def _start_with_api_fallback(self, reason: str) -> None:
        """로컬 모델 불가 시 경고 후 명시적 승인만 API 번역을 진행합니다."""
        self.translation_page.set_status(reason)
        if not self._gemini_available:
            self.translation_page.set_status(
                f"{reason}\nGemini API 설정이 없어 API 번역을 사용할 수 없습니다."
            )
            return
        if not self._confirm_api_fallback():
            return
        self._start_translation_job(use_api=True)

    def _confirm_api_fallback(self) -> bool:
        """외부 API 전송 경고를 띄우고 명시적 동의를 받습니다."""
        box = QMessageBox(self)
        box.setWindowTitle("API 번역 확인")
        box.setText(
            "로컬 번역 모델에 연결할 수 없습니다.\n\n"
            "계속 진행하면 API에 연결된 AI를 사용하여 "
            "선택한 파일의 번역 내용을 처리합니다.\n\n"
            "번역할 원문이 외부 API로 전송될 수 있습니다.\n\n"
            "계속하시겠습니까?"
        )
        cancel_button = box.addButton("취소", QMessageBox.RejectRole)
        api_button = box.addButton("API로 번역", QMessageBox.AcceptRole)
        box.setDefaultButton(cancel_button)
        box.exec()
        return box.clickedButton() is api_button

    def _create_translation_llm(self, settings: TranslationSettings, use_api: bool):
        """선택된 엔진 1개로 Translator를 만듭니다."""
        if use_api:
            gemini_settings = Settings.from_env()
            gemini_settings.validate()
            return GeminiTranslationProvider(GeminiClient(gemini_settings))
        return LocalLLM(settings)

    # ---------- 번역 실행 ----------
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
            return self._start_with_api_fallback(
                "Ollama 서버에 연결할 수 없습니다. Ollama가 실행 중인지 확인해주세요."
            )
        if self._ollama_connected and not self._ollama_model_found:
            return self._start_with_api_fallback(
                "설정된 Ollama 모델을 찾을 수 없습니다:\n"
                f"{self.translation_settings.model}"
            )
        self._start_translation_job(use_api=False)

    def _start_translation_job(self, use_api: bool) -> None:
        file_paths = self.translation_page.selected_paths()
        if not file_paths:
            self.translation_page.set_status("번역할 파일을 먼저 추가해주세요.")
            return
        # 파일별 선택값으로 검증합니다. 비어 있는 파일이 있으면 시작을 막습니다.
        # 전체 fallback이나 잘못된 field 자동 번역을 하지 않습니다.
        self._sync_displayed_selection()
        pending = [
            path
            for path in file_paths
            if path.suffix.lower() in {".csv", ".json"} and path in self._schema_pending
        ]
        if pending:
            self.translation_page.set_status(
                "AI가 번역 대상을 분석 중입니다. 분석이 끝난 후 번역을 시작해주세요."
            )
            return
        missing = validate_per_file_selections(file_paths, self.file_field_selections)
        if missing:
            names = "\n".join(f"· {path.name}" for path in missing)
            self.translation_page.set_status(
                "다음 파일의 번역 대상 field를 1개 이상 선택해주세요:\n"
                f"{names}\n선택하지 않은 컬럼/Key는 번역하지 않습니다."
            )
            return
        if self._translation_thread is not None and self._translation_thread.isRunning():
            return
        self.translation_page.set_translation_state("running")
        self.translation_page.set_progress(0, "번역 준비 중…")

        source, notice = detect_source_language(
            self.translation_page.source_combo.currentText(),
            file_paths,
            self._all_selected_fields(),
        )
        if notice:
            self.translation_page.set_status(notice)
        target = _display_to_language(self.translation_page.target_combo.currentText())
        prompt_text, prompt_name = self._selected_translation_prompt()
        settings = self.translation_settings
        try:
            llm = self._create_translation_llm(settings, use_api)
        except Exception as error:
            self.translation_page.set_status(f"번역 엔진을 준비할 수 없습니다: {error}")
            return

        def factory(stop_requested=None, on_progress=None, file_paths=None):
            handlers = self._build_handlers(file_paths or [], settings.chunk_max_chars)
            return Translator(
                loader=FileLoader(settings.input_dir),
                local_llm=llm,
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
        """CSV/JSON은 파일별 선택 field만, TXT는 기본 핸들러로 번역합니다.

        선택이 0개면 전체 fallback을 하지 않고 해당 파일을 건너뜁니다.
        (시작 전에 막히므로 여기는 방어용입니다.)
        """
        handlers: dict = {}
        for path in file_paths:
            try:
                handler = create_handler(path, chunk_max_chars)
                handler.load(path)
                available = handler.available_fields()
                if path.suffix.lower() in {".csv", ".json"} and available:
                    use = resolve_fields_for_handler(
                        available, self.file_field_selections.get(path, [])
                    )
                    if not use:
                        continue
                    handler.select_fields(use)
                handlers[path] = handler
            except Exception:
                continue
        return handlers

    # ---------- Prompt 내용 확인 / AI 생성 ----------
    def _on_view_prompt(self) -> None:
        index = self.translation_page.prompt_combo.currentIndex()
        title, subtitle, body = resolve_prompt_view(
            index,
            self._prompt_presets,
            self.translation_page.target_combo.currentText(),
        )
        self.translation_page.show_prompt_dialog(title, subtitle, body)

    def _on_new_prompt(self) -> None:
        if self._prompt_thread is not None and self._prompt_thread.isRunning():
            return
        file_paths = self.translation_page.selected_paths()
        if not file_paths:
            self.translation_page.set_status("Prompt 생성용 파일을 먼저 추가해주세요.")
            return
        target = _display_to_language(self.translation_page.target_combo.currentText())
        self._sync_displayed_selection()
        source, notice = detect_source_language(
            self.translation_page.source_combo.currentText(),
            file_paths,
            self._all_selected_fields(),
        )
        if notice:
            self.translation_page.set_status(notice)
        document_type, ok = QInputDialog.getText(
            self, "문서 유형", "문서 유형 (예: game_dialogue):", text="game_dialogue"
        )
        if not ok or not document_type.strip():
            return
        document_type = document_type.strip()
        try:
            gemini_settings = Settings.from_env()
            gemini_settings.validate()
        except Exception:
            self.translation_page.set_status(
                "Gemini API 설정이 없습니다. Prompt 설정 화면에서 직접 만들 수 있습니다."
            )
            return
        sample = collect_detection_sample(file_paths, self._all_selected_fields())
        if not sample.strip():
            self.translation_page.set_status("Prompt 생성용 샘플이 없습니다.")
            return
        builder = PromptBuilder(GeminiPromptAI(GeminiClient(gemini_settings)))
        self._pending_draft = {
            "source": source,
            "target": target,
            "document_type": document_type,
        }
        worker = PromptDraftWorker(
            builder, file_paths, source, target, document_type, sample
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_prompt_draft)
        worker.failed.connect(self._on_prompt_draft_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._launch(
            thread, worker,
            {"_prompt_thread": thread, "_prompt_worker": worker},
        )
        self.translation_page.set_generating_prompt(True)
        self._prompt_thread = thread
        self._prompt_worker = worker
        thread.start()

    def _on_prompt_draft(self, draft: str) -> None:
        self.translation_page.set_generating_prompt(False)
        self._prompt_worker = None
        pending = self._pending_draft or {}
        dialog = QDialog(self)
        dialog.setWindowTitle("AI Prompt 초안")
        dialog.resize(560, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("AI가 생성한 Prompt 초안입니다. 수정 후 저장하세요."))
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("저장할 Preset 이름")
        draft_edit = QTextEdit()
        draft_edit.setPlainText(draft)
        buttons = QHBoxLayout()
        save_button = QPushButton("저장")
        save_button.setObjectName("PrimaryButton")
        cancel_button = QPushButton("취소")
        cancel_button.setObjectName("SecondaryButton")
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addWidget(QLabel("Preset 이름"))
        layout.addWidget(name_edit)
        layout.addWidget(draft_edit, 1)
        layout.addLayout(buttons)
        saved: list[bool] = []

        def save() -> None:
            name = name_edit.text().strip()
            body = draft_edit.toPlainText().strip()
            if not name or not body:
                self.translation_page.set_status("Preset 이름과 내용을 입력하세요.")
                return
            try:
                preset = self.prompt_manager.create_preset(
                    name,
                    str(pending.get("source", "Unknown")),
                    str(pending.get("target", "Korean")),
                    str(pending.get("document_type", "general")),
                    body,
                    "AI",
                )
                self.prompt_manager.save(preset, overwrite=False)
            except ChatbotError as error:
                self.translation_page.set_status(f"Preset 저장 실패: {error}")
                return
            saved.append(True)
            dialog.accept()

        save_button.clicked.connect(save)
        cancel_button.clicked.connect(dialog.reject)
        dialog.exec()
        # 저장 후에는 번역을 자동 시작하지 않고 combo만 refresh합니다.
        if saved:
            self._refresh_translation_prompts()
            self.translation_page.set_status("새 Prompt를 저장했습니다.")
        self._pending_draft = None

    def _on_prompt_draft_failed(self, error: str) -> None:
        self.translation_page.set_generating_prompt(False)
        self._prompt_worker = None
        self._pending_draft = None
        self.translation_page.set_status(
            f"AI Prompt 생성 실패: {error} "
            "Prompt 설정 화면에서 직접 만들 수 있습니다."
        )

    # ---------- Prompt 설정 화면 AI 생성 (기존 PromptDraftWorker 재사용) ----------
    def _on_prompt_page_generate(self) -> None:
        if self._prompt_thread is not None and self._prompt_thread.isRunning():
            return
        source = self.prompt_page.source_combo.currentText().strip()
        target = self.prompt_page.target_combo.currentText().strip()
        document_type = self.prompt_page.doc_edit.text().strip()
        if not document_type:
            self.prompt_page.set_validation(False, "문서 유형을 입력해주세요.")
            return
        if self._schema_pending:
            self.prompt_page.set_validation(
                False, "AI 분석이 끝난 후 Prompt를 생성해주세요."
            )
            return
        file_paths = self.translation_page.selected_paths()
        if file_paths:
            self._sync_displayed_selection()
            sample = collect_detection_sample(file_paths, self._all_selected_fields())
        else:
            sample_path, _ = QFileDialog.getOpenFileName(
                self, "Prompt 생성용 sample 파일", "", "Text/CSV/JSON (*.txt *.csv *.json)"
            )
            if not sample_path:
                return
            sample = collect_detection_sample([Path(sample_path)], None)
        if not sample.strip():
            self.prompt_page.set_validation(False, "Prompt 생성용 샘플이 없습니다.")
            return
        try:
            gemini_settings = Settings.from_env()
            gemini_settings.validate()
        except Exception:
            self.prompt_page.set_validation(
                False, "Gemini API 설정이 없습니다. .env를 확인해주세요."
            )
            return
        builder = PromptBuilder(GeminiPromptAI(GeminiClient(gemini_settings)))
        worker = PromptDraftWorker(
            builder, file_paths, source, target, document_type, sample
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_prompt_page_draft)
        worker.failed.connect(self._on_prompt_page_draft_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._launch(
            thread, worker,
            {"_prompt_thread": thread, "_prompt_worker": worker},
        )
        self.prompt_page.set_generating(True)
        self._prompt_thread = thread
        self._prompt_worker = worker
        thread.start()

    def _on_prompt_page_draft(self, draft: str) -> None:
        self.prompt_page.set_generating(False)
        self._prompt_worker = None
        self.prompt_page.apply_ai_draft(
            draft,
            self.prompt_page.source_combo.currentText().strip(),
            self.prompt_page.target_combo.currentText().strip(),
            self.prompt_page.doc_edit.text().strip(),
        )

    def _on_prompt_page_draft_failed(self, error: str) -> None:
        self.prompt_page.set_generating(False)
        self._prompt_worker = None
        self.prompt_page.set_validation(False, f"AI Prompt 생성 실패: {error}")

    def _on_open_input(self) -> None:
        input_dir = (
            self.translation_settings.input_dir
            if self.translation_settings is not None
            else INPUT_DIR
        )
        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(input_dir.resolve())))
        except Exception:
            pass

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
