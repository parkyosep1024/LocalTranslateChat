"""GUI 초기 상태와 backend 연결 구조를 확인합니다. display가 없으면 offscreen을 사용합니다."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, QEventLoop, Qt, QThread, QTimer
    from PySide6.QtGui import QInputMethodEvent, QKeyEvent
    from PySide6.QtWidgets import QApplication

    from script.config.translation_settings import TranslationSettings
    from script.gui.main_window import (
        MainWindow,
        collect_detection_sample,
        detect_source_language,
        resolve_fields_for_handler,
        resolve_prompt_selection,
        resolve_prompt_view,
        validate_per_file_selections,
    )
    from script.gui.pages.chat_page import ChatPage
    from script.gui.pages.prompt_page import PRESET_LANGUAGES, PromptPage
    from script.gui.pages.translation_page import TranslationPage
    from script.gui.workers import (
        ChatWorker,
        PromptDraftWorker,
        SchemaAnalysisWorker,
        TranslationWorker,
        fetch_ollama_status,
        ollama_model_found,
    )
    from script.translation.file_loader import FileLoader
    from script.translation.prompt_manager import PromptManager, PromptPreset
    from script.translation.schema_analyzer import SchemaAnalysis, SchemaCache
    from script.translation.translator import BASIC_TRANSLATION_PROMPT

    PYSIDE_AVAILABLE = True
except Exception:  # PySide6 미설치 환경에서는 전체 skip합니다.
    PYSIDE_AVAILABLE = False


def _app() -> "QApplication":
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class TranslationPageTest(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.page = TranslationPage()

    def test_initial_state_is_empty(self) -> None:
        self.assertEqual(self.page.selected_paths(), [])
        self.assertEqual(self.page.progress.value(), 0)
        self.assertEqual(self.page.percent_label.text(), "0%")
        self.assertEqual(self.page.current_file_label.text(), "-")
        self.assertFalse(self.page.btn_stop.isEnabled())
        # show하지 않은 widget의 isVisible()은 항상 False이므로,
        # 실제 표시 후 빈 상태 안내가 보이는지 검증합니다.
        self.page.show()
        _app().processEvents()
        self.assertTrue(self.page.empty_file_label.isVisible())

    def test_no_dummy_progress_or_files(self) -> None:
        labels = [child.text() for child in self.page.findChildren(type(self.page.status_label))]
        joined = "\n".join(labels)
        self.assertNotIn("68%", joined)
        self.assertNotIn("game_dialogue_ep03.csv", self.page.current_file_label.text())

    def test_add_remove_keeps_real_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.txt"
            second = Path(tmp) / "b.csv"
            first.write_text("hello", encoding="utf-8")
            second.write_text("h1,h2\nv1,v2\n", encoding="utf-8")

            received: list = []
            self.page.files_changed.connect(lambda paths: received.append(list(paths)))

            self.assertTrue(self.page.add_file_path(first))
            self.assertFalse(self.page.add_file_path(first))  # 중복 방지
            self.assertFalse(self.page.add_file_path(Path(tmp) / "c.exe"))  # 미지원 확장자
            self.assertTrue(self.page.add_file_path(second))
            self.assertEqual(self.page.selected_paths(), [first.resolve(), second.resolve()])
            self.assertEqual(received[-1], [first.resolve(), second.resolve()])

            self.page.remove_file_path(first.resolve())
            self.assertEqual(self.page.selected_paths(), [second.resolve()])


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class PromptPageTest(unittest.TestCase):
    def test_loads_real_presets(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            manager = PromptManager(presets_dir=Path(tmp))
            preset = manager.create_preset("real_one", "Japanese", "Korean", "game_dialogue", "prompt body")
            manager.save(preset)
            page = PromptPage(manager=manager)
            names = [card.preset.name for card in page._cards]
            self.assertIn("real_one", names)
            # 선택 시 Editor에 실제 내용이 표시됩니다.
            page.select_preset(page.presets[0])
            self.assertIn("prompt body", page.editor_text())
            self.assertNotIn("현지화하는 전문 번역가", page.editor_text())

    def test_empty_initial_editor(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            page = PromptPage(manager=PromptManager(presets_dir=Path(tmp)))
            self.assertEqual(page.title_label.text(), "Prompt를 선택하세요")
            self.assertEqual(page.editor_text(), "")
            self.assertFalse(page.save_button.isEnabled())
            self.assertIn("저장된 Prompt가 없습니다.", page.empty_preset_label.text())


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class WorkerSafetyTest(unittest.TestCase):
    def test_chat_failure_does_not_raise(self) -> None:
        _app()

        class FailingEngine:
            def chat(self, _message: str) -> str:
                raise RuntimeError("boom")

        worker = ChatWorker(FailingEngine(), "hi")
        errors: list[str] = []
        worker.failed.connect(errors.append)
        worker.run()
        self.assertEqual(errors, ["boom"])

    def test_stop_flag_propagates(self) -> None:
        _app()
        seen: list[bool] = []

        class FakeTranslator:
            def __init__(self, stop_requested=None, on_progress=None, file_paths=None) -> None:
                self._stop_requested = stop_requested

            def translate_all(self, output_func=print):
                seen.append(bool(self._stop_requested and self._stop_requested()))

                class Summary:
                    was_stopped = True

                return Summary()

        worker = TranslationWorker(FakeTranslator, [])
        worker.request_stop()
        self.assertTrue(worker.stop_requested())
        stopped: list = []
        finished: list = []
        failed: list = []
        worker.stopped.connect(lambda: stopped.append(True))
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)
        worker.run()
        self.assertTrue(seen, "Translator에 stop callback이 전달되어야 합니다.")
        self.assertEqual(len(stopped), 1)
        self.assertEqual(finished, [], "stopped이면 finished를 emit하면 안 됩니다.")
        self.assertEqual(failed, [])


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class TranslationWorkerExitSignalTest(unittest.TestCase):
    """성공/중지/예외 종료 signal이 정확히 1개씩만 발생하는지 확인합니다."""

    def _run(self, factory):
        _app()
        worker = TranslationWorker(factory, [])
        events: list[str] = []
        worker.finished.connect(lambda _s: events.append("finished"))
        worker.stopped.connect(lambda: events.append("stopped"))
        worker.failed.connect(lambda _e: events.append("failed"))
        worker.run()
        return events

    def test_success_emits_finished_only(self) -> None:
        class FakeTranslator:
            def __init__(self, stop_requested=None, on_progress=None, file_paths=None) -> None:
                pass

            def translate_all(self, output_func=print):
                class Summary:
                    was_stopped = False
                    succeeded = 1

                return Summary()

        self.assertEqual(self._run(FakeTranslator), ["finished"])

    def test_stopped_emits_stopped_only(self) -> None:
        class FakeTranslator:
            def __init__(self, stop_requested=None, on_progress=None, file_paths=None) -> None:
                pass

            def translate_all(self, output_func=print):
                class Summary:
                    was_stopped = True

                return Summary()

        self.assertEqual(self._run(FakeTranslator), ["stopped"])

    def test_exception_emits_failed_only(self) -> None:
        class FakeTranslator:
            def __init__(self, stop_requested=None, on_progress=None, file_paths=None) -> None:
                pass

            def translate_all(self, output_func=print):
                raise RuntimeError("llm down")

        self.assertEqual(self._run(FakeTranslator), ["failed"])


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class DefaultPromptTest(unittest.TestCase):
    def test_basic_prompt_always_first(self) -> None:
        _app()
        page = TranslationPage()
        page.set_prompt_items([])
        self.assertEqual(page.prompt_combo.itemText(0), "기본 Prompt")
        page.set_prompt_items(["Japanese → Korean · preset A", "English → Korean · preset B"])
        self.assertEqual(page.prompt_combo.itemText(0), "기본 Prompt")
        self.assertEqual(page.prompt_combo.count(), 3)

    def test_prompt_index_mapping(self) -> None:
        presets = [
            PromptPreset("A", "Japanese", "Korean", "game_dialogue", "prompt-a", "AI", "2026-01-01", "2026-01-02"),
            PromptPreset("B", "English", "Korean", "novel", "prompt-b", "AI", "2026-01-01", "2026-01-02"),
        ]
        self.assertEqual(resolve_prompt_selection(0, presets), (None, "기본 Prompt"))
        self.assertEqual(resolve_prompt_selection(1, presets), ("prompt-a", "A"))
        self.assertEqual(resolve_prompt_selection(2, presets), ("prompt-b", "B"))
        # 범위 밖 index도 안전하게 기본 Prompt로 처리합니다.
        self.assertEqual(resolve_prompt_selection(99, presets), (None, "기본 Prompt"))
        self.assertEqual(resolve_prompt_selection(-1, presets), (None, "기본 Prompt"))
        self.assertEqual(resolve_prompt_selection(1, []), (None, "기본 Prompt"))


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class AutoDetectTest(unittest.TestCase):
    def test_japanese_txt_detected_with_real_detector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("やっと会えたね。ずっと待っていたよ。明日また会おう。", encoding="utf-8")
            language, notice = detect_source_language("자동 감지", [path])
            self.assertEqual(language, "Japanese")
            self.assertIsNotNone(notice)
            self.assertIn("Japanese", notice)

    def test_direct_selection_passes_through(self) -> None:
        language, notice = detect_source_language("Korean", [])
        self.assertEqual(language, "Korean")
        self.assertIsNone(notice)

    def test_undetectable_falls_back_to_unknown(self) -> None:
        language, notice = detect_source_language("자동 감지", [])
        self.assertEqual(language, "Unknown")
        self.assertIsNotNone(notice)

    def test_csv_uses_only_selected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.csv"
            path.write_text(
                "id,dialogue_text\nID_001,やっと会えたね\nID_002,ずっと待っていた\n",
                encoding="utf-8",
            )
            sample = collect_detection_sample([path], ["dialogue_text"])
            self.assertNotIn("ID_001", sample)
            self.assertIn("やっと会えたね", sample)


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class FieldSelectionTest(unittest.TestCase):
    def test_no_fallback_to_all_fields(self) -> None:
        # 전부 체크 해제하면 빈 리스트여야 합니다. 전체 번역을 반환하면 안 됩니다.
        self.assertEqual(
            resolve_fields_for_handler(["id", "dialogue_text"], []), []
        )

    def test_only_checked_fields(self) -> None:
        self.assertEqual(
            resolve_fields_for_handler(["id", "dialogue_text"], ["dialogue_text"]),
            ["dialogue_text"],
        )


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class OllamaStatusTest(unittest.TestCase):
    def _opener(self, payload=None, error=None):
        class FakeResponse:
            def __init__(self, data) -> None:
                self._data = data

            def read(self):
                import json

                return json.dumps(self._data).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_opener(request, timeout=None):
            if error is not None:
                raise error
            return FakeResponse(payload)

        return fake_opener

    def test_server_up_with_model(self) -> None:
        result = fetch_ollama_status(
            "http://localhost:11434",
            "mymodel:tag",
            opener=self._opener({"models": [{"name": "mymodel:tag"}]}),
        )
        self.assertEqual(result, {"connected": True, "model_found": True})

    def test_server_up_without_model(self) -> None:
        result = fetch_ollama_status(
            "http://localhost:11434",
            "mymodel:tag",
            opener=self._opener({"models": [{"name": "other:latest"}]}),
        )
        self.assertEqual(result, {"connected": True, "model_found": False})

    def test_server_down(self) -> None:
        from urllib.error import URLError

        result = fetch_ollama_status(
            "http://localhost:11434", "mymodel:tag", opener=self._opener(error=URLError("down"))
        )
        self.assertEqual(result, {"connected": False, "model_found": False})

    def test_tag_omitted_model_matches_any_tag(self) -> None:
        self.assertTrue(
            ollama_model_found({"models": [{"name": "mymodel:latest"}]}, "mymodel")
        )
        self.assertFalse(
            ollama_model_found({"models": [{"name": "mymodel:latest"}]}, "mymodel:other")
        )


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class ChatPageEmptyTest(unittest.TestCase):
    def test_no_dummy_messages(self) -> None:
        _app()
        page = ChatPage()
        self.assertEqual(page.message_count(), 0)
        self.assertEqual(page.model_value.text(), "-")
        # show하지 않은 widget의 isVisible()은 항상 False이므로,
        # 실제 표시 후 빈 상태 안내가 보이는지 검증합니다.
        page.show()
        _app().processEvents()
        self.assertTrue(page.empty_chat_label.isVisible())


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class MainWindowLifecycleTest(unittest.TestCase):
    """BUG1~4 회귀 테스트: 생성, ref 정리, 재시작, schema 검증, 상태 확인 전 차단."""

    def setUp(self) -> None:
        _app()
        self.windows: list = []
        # 상태 확인 스레드도 live 서버가 아니라 즉시 fake로 끝냅니다.
        # (지연 변동이 전체 스위트를 흔들어서 flake가 났습니다.
        # 실제 판정 로직은 OllamaStatusTest와 아래 상태 테스트에서 검증합니다.)
        self._status_patch = patch(
            "script.gui.workers.fetch_ollama_status",
            return_value={"connected": False, "model_found": False},
        )
        self._status_patch.start()

    def tearDown(self) -> None:
        for window in self.windows:
            try:
                # 백그라운드 Ollama 확인이 끝나기 전에 닫고 종료하면
                # 진행 중인 QThread와 함께 fail-fast crash가 납니다.
                # 상태 확정 + 스레드 종료 신호 전달까지 기다린 뒤 닫습니다
                # (이미 끝났으면 즉시 반환).
                self.wait_for(
                    lambda w=window: w._ollama_connected is not None,
                    timeout_ms=10000,
                )
                self.wait_for(
                    lambda w=window: len(w._threads) == 0,
                    timeout_ms=30000,
                )
                window.close()
            except Exception:
                pass
        try:
            self._status_patch.stop()
        except Exception:
            pass
        _app().processEvents()

    def make_window(self, analyzer=None):
        # 생성 시 실제 setting/input + 실제 Gemini 분석을 하지 않도록 격리합니다.
        # (실제 분석은 backend CLI 테스트와 수동 확인에서 검증합니다.)
        with patch.object(MainWindow, "_create_schema_analyzer", return_value=analyzer):
            window = MainWindow()
        self.windows.append(window)
        window.translation_page.clear_files()
        window.file_field_selections.clear()
        window._file_field_info.clear()
        window._displayed_schema_file = None
        window.translation_page.set_analysis_status("")
        return window

    def wait_for(self, condition, timeout_ms: int = 3000) -> bool:
        """조건이 만족되면 즉시 True, timeout이면 False를 반환합니다.

        짧은 polling QTimer로 확인하므로 busy loop가 없고,
        sleep으로 GUI event loop를 막지 않습니다.
        """
        if condition():
            return True

        loop = QEventLoop()

        poll = QTimer()
        poll.setInterval(10)

        timeout = QTimer()
        timeout.setSingleShot(True)

        def check() -> None:
            if condition():
                loop.quit()

        poll.timeout.connect(check)
        timeout.timeout.connect(loop.quit)

        poll.start()
        timeout.start(timeout_ms)

        loop.exec()

        poll.stop()
        timeout.stop()

        return condition()

    def test_constructs_without_attribute_error(self) -> None:
        # BUG1: _threads/_workers가 _check_ollama_status보다 먼저 있어야 합니다.
        window = self.make_window()
        self.assertIsInstance(window._threads, set)
        self.assertIsInstance(window._workers, set)
        self.assertIsNotNone(window._status_thread)

    def test_ollama_status_states_update_ui(self) -> None:
        # 스레드 없이 3가지 상태 판정이 UI에 반영되는지 직접 검증합니다.
        window = self.make_window()
        window._gemini_available = False
        window._on_ollama_status({"connected": True, "model_found": True})
        self.assertIn("로컬 모델 연결됨", window.sidebar.model_status.text())
        self.assertIn("엔진 준비됨", window.translation_page.engine_badge.text())
        window._on_ollama_status({"connected": True, "model_found": False})
        self.assertIn("Ollama 연결됨", window.sidebar.model_status.text())
        self.assertIn("모델 없음", window.translation_page.engine_badge.text())
        window._on_ollama_status({"connected": False, "model_found": False})
        self.assertIn("연결 안 됨", window.sidebar.model_status.text())
        # Gemini 사용 가능 시에는 오해 없는 문구를 표시합니다.
        window._gemini_available = True
        window._on_ollama_status({"connected": False, "model_found": False})
        self.assertIn("API 번역 가능", window.sidebar.model_status.text())

    def test_ollama_checking_blocks_translation_start(self) -> None:
        # BUG4: _ollama_connected is None(확인 중)이면 시작하지 않습니다.
        window = self.make_window()
        window.translation_settings = TranslationSettings(model="test-model")
        window._ollama_connected = None
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello world, hello again", encoding="utf-8")
            window.translation_page.add_file_path(target)
            window._on_translation_start()
            self.assertIn("확인 중", window.translation_page.status_label.text())
            self.assertIsNone(window._translation_worker)
            self.assertNotEqual(window.translation_page._state, "running")
            # tearDown 대기가 끝나도록 확인 중 상태를 복원합니다.
            window._ollama_connected = False
            window._ollama_model_found = False

    def test_empty_per_file_selection_blocks_start(self) -> None:
        # 파일별 선택값이 비어 있으면 해당 파일명을 지목하며 차단합니다.
        window = self.make_window()
        window.translation_settings = TranslationSettings(model="test-model")
        window._ollama_connected = True
        window._ollama_model_found = True
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")
            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            json_path = Path(tmp) / "b.json"
            json_path.write_text('{"speaker": "A", "dialogue": "hi"}', encoding="utf-8")
            with patch.object(MainWindow, "_create_schema_analyzer", return_value=None):
                window.translation_page.add_file_path(csv_path)
                window.translation_page.add_file_path(json_path)
            # b.json 선택값을 비워 서로 다른 schema 상황을 만듭니다.
            window.file_field_selections[json_path.resolve()] = []
            window._on_translation_start()
            self.assertIn("b.json", window.translation_page.status_label.text())
            self.assertIsNone(window._translation_worker)
            self.assertNotEqual(window.translation_page._state, "running")

    def test_per_file_selections_allow_different_schemas(self) -> None:
        # 각 파일이 자기 schema에서 선택값을 가지면 시작을 막지 않습니다.
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            json_path = Path(tmp) / "b.json"
            json_path.write_text('{"speaker": "A", "dialogue": "hi"}', encoding="utf-8")
            selections = {
                csv_path.resolve(): ["text"],
                json_path.resolve(): ["dialogue"],
            }
            self.assertEqual(
                validate_per_file_selections(
                    [csv_path.resolve(), json_path.resolve()], selections
                ),
                [],
            )
            selections[json_path.resolve()] = []
            self.assertEqual(
                validate_per_file_selections(
                    [csv_path.resolve(), json_path.resolve()], selections
                ),
                [json_path.resolve()],
            )
            # TXT는 검사 대상이 아닙니다.
            txt = Path(tmp) / "n.txt"
            txt.write_text("plain", encoding="utf-8")
            self.assertEqual(
                validate_per_file_selections([txt.resolve()], {}), []
            )

    def test_chat_completes_clears_refs_and_restarts(self) -> None:
        # BUG2: Chat 1회 완료 후 reference가 정리되고 다시 시작 가능해야 합니다.
        window = self.make_window()

        class FakeEngine:
            def chat(self, message: str) -> str:
                return "echo:" + message

            def clear_history(self) -> None:
                pass

        window.chat_engine = FakeEngine()
        window.chat_page.input.setPlainText("hi")
        window.chat_page._emit_send()
        self.assertTrue(self.wait_for(lambda: window.chat_page.message_count() == 2),
                        "AI 답변을 받지 못했습니다.")
        self.assertTrue(self.wait_for(lambda: window._chat_thread is None, timeout_ms=30000))
        self.assertIsNone(window._chat_worker)
        self.assertTrue(window.chat_page.send_button.isEnabled())
        # 두 번째 Chat이 삭제된 객체 없이 시작되어야 합니다.
        window.chat_page.input.setPlainText("again")
        window.chat_page._emit_send()
        self.assertTrue(
            self.wait_for(lambda: window.chat_page.message_count() == 4),
            "두 번째 Chat이 시작되지 않았습니다.",
        )
        self.assertTrue(self.wait_for(lambda: window._chat_thread is None, timeout_ms=30000))

    def _launch_test_translation(self, window, factory):
        worker = TranslationWorker(factory, [])
        thread = QThread()
        worker.moveToThread(thread)
        done: list[str] = []
        thread.started.connect(worker.run)
        worker.finished.connect(window._on_translation_finished)
        worker.failed.connect(window._on_translation_failed)
        worker.stopped.connect(window._on_translation_stopped)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.stopped.connect(thread.quit)
        worker.finished.connect(lambda _s: done.append("finished"))
        worker.failed.connect(lambda _e: done.append("failed"))
        worker.stopped.connect(lambda: done.append("stopped"))
        window._launch(
            thread,
            worker,
            {"_translation_thread": thread, "_translation_worker": worker},
        )
        window._translation_thread = thread
        window._translation_worker = worker
        thread.start()
        return done

    @staticmethod
    def _summary_factory(stopped: bool = False, fail: bool = False):
        class FakeTranslator:
            def __init__(self, stop_requested=None, on_progress=None, file_paths=None) -> None:
                pass

            def translate_all(self, output_func=print):
                if fail:
                    raise RuntimeError("llm down")

                class Summary:
                    was_stopped = stopped
                    succeeded = 0 if stopped else 1
                    failed = 0

                return Summary()

        return FakeTranslator

    def test_translation_success_clears_refs(self) -> None:
        window = self.make_window()
        done = self._launch_test_translation(window, self._summary_factory())
        self.assertTrue(self.wait_for(lambda: len(done) == 1))
        self.assertEqual(done, ["finished"])
        self.assertTrue(self.wait_for(lambda: window._translation_thread is None, timeout_ms=30000))
        self.assertIsNone(window._translation_worker)
        self.assertEqual(window.translation_page.state_badge.text(), "완료")

    def test_translation_stopped_then_restartable(self) -> None:
        window = self.make_window()
        done = self._launch_test_translation(window, self._summary_factory(stopped=True))
        self.assertTrue(self.wait_for(lambda: len(done) == 1))
        self.assertEqual(done, ["stopped"])
        self.assertTrue(self.wait_for(lambda: window._translation_thread is None, timeout_ms=30000))
        self.assertIsNone(window._translation_worker)
        # 중지 상태가 완료 100%로 덮이지 않아야 합니다.
        self.assertEqual(window.translation_page.state_badge.text(), "중지됨")
        self.assertLess(window.translation_page.progress.value(), 100)
        # 중지 후 다시 시작 가능해야 합니다.
        done2 = self._launch_test_translation(window, self._summary_factory())
        self.assertTrue(self.wait_for(lambda: len(done2) == 1))
        self.assertEqual(done2, ["finished"])
        self.assertEqual(window.translation_page.state_badge.text(), "완료")

    def test_translation_failed_then_retryable(self) -> None:
        window = self.make_window()
        done = self._launch_test_translation(window, self._summary_factory(fail=True))
        self.assertTrue(self.wait_for(lambda: len(done) == 1))
        self.assertEqual(done, ["failed"])
        self.assertTrue(self.wait_for(lambda: window._translation_thread is None, timeout_ms=30000))
        self.assertIsNone(window._translation_worker)
        done2 = self._launch_test_translation(window, self._summary_factory())
        self.assertTrue(self.wait_for(lambda: len(done2) == 1))
        self.assertEqual(done2, ["finished"])

    def test_input_autoload_and_refresh(self) -> None:
        # setting/input 자동 로드 + 새로고침(새 파일만, 외부 파일 유지, 중복 방지).
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "input"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")
            (input_dir / "b.csv").write_text("h1,h2\nv1,v2\n", encoding="utf-8")
            (input_dir / "skip.exe").write_text("x", encoding="utf-8")
            loader = FileLoader(input_dir)
            with patch.object(MainWindow, "_create_schema_analyzer", return_value=None):
                first = window._load_input_files(loader)
                self.assertEqual(first, 2)
                # 두 번째 로드는 중복 없이 0개 추가합니다.
                self.assertEqual(window._load_input_files(loader), 0)
                # 외부 파일은 유지되고 input 새 파일만 추가됩니다.
                external = Path(tmp) / "ext.txt"
                external.write_text("external", encoding="utf-8")
                window.translation_page.add_file_path(external)
                (input_dir / "c.json").write_text('{"k": "v"}', encoding="utf-8")
                self.assertEqual(window._load_input_files(loader), 1)
                names = sorted(p.name for p in window.translation_page.selected_paths())
                self.assertEqual(names, ["a.txt", "b.csv", "c.json", "ext.txt"])

    def test_input_loader_missing_dir_does_not_crash(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-such-dir"
            self.assertEqual(window._load_input_files(FileLoader(missing)), 0)

    def test_schema_recommendation_applied(self) -> None:
        # AI 추천 field가 checkbox에 반영됩니다.
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")

            class FakeAnalyzer:
                def analyze(self, kind, fields, samples):
                    return SchemaAnalysis(
                        ("text",), ("id",),
                        {"text": "대사", "id": "내부 코드"},
                    )

            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            with patch.object(
                MainWindow, "_create_schema_analyzer", return_value=FakeAnalyzer()
            ):
                window.translation_page.add_file_path(csv_path)
            self.assertTrue(
                self.wait_for(
                    lambda: window.file_field_selections.get(csv_path.resolve())
                    == ["text"]
                )
            )
            checked = {
                check.text()
                for check in window.translation_page.field_checks
                if check.isChecked()
            }
            self.assertEqual(checked, {"text"})
            self.assertNotIn(csv_path.resolve(), window._schema_pending)

    def test_schema_failure_falls_back_manual_unchecked(self) -> None:
        # 분석 실패 시 전체를 선택하지 않고 미선택 상태로 둡니다.
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")

            class FailingAnalyzer:
                def analyze(self, kind, fields, samples):
                    raise RuntimeError("gemini down")

            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            with patch.object(
                MainWindow, "_create_schema_analyzer", return_value=FailingAnalyzer()
            ):
                window.translation_page.add_file_path(csv_path)
            self.assertTrue(
                self.wait_for(
                    lambda: csv_path.resolve() in window.file_field_selections
                )
            )
            self.assertEqual(
                window.file_field_selections[csv_path.resolve()], []
            )
            self.assertEqual(window.translation_page.selected_field_names(), [])
            self.assertNotIn(csv_path.resolve(), window._schema_pending)

    def test_schema_analysis_runs_async(self) -> None:
        # 분석이 끝나기 전에도 GUI가 멈추지 않고, 전체 선택으로 보이지 않습니다.
        # (버튼 enabled 자체가 아니라 freeze 여부가 쟁점이며, 분석 중 시작은 막습니다.)
        import threading

        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")
            release = threading.Event()

            class SlowAnalyzer:
                def analyze(self, kind, fields, samples):
                    release.wait(timeout=10)
                    return SchemaAnalysis(
                        tuple(fields), (),
                        {name: "ok" for name in fields},
                    )

            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            with patch.object(
                MainWindow, "_create_schema_analyzer", return_value=SlowAnalyzer()
            ):
                window.translation_page.add_file_path(csv_path)
                # 분석 중: pending 추적 + 전체 미선택 + 페이지는 응답합니다.
                self.assertIn(csv_path.resolve(), window._schema_pending)
                self.assertEqual(window.translation_page.selected_field_names(), [])
                window.translation_page.set_status("응답 확인")
                self.assertEqual(
                    window.translation_page.status_label.text(), "응답 확인"
                )
                release.set()
            self.assertTrue(
                self.wait_for(
                    lambda: window.file_field_selections.get(csv_path.resolve())
                    == ["id", "text"]
                )
            )
            self.assertNotIn(csv_path.resolve(), window._schema_pending)

    def test_pending_blocks_translation_start(self) -> None:
        # 분석 중인 파일이 있으면 번역 시작을 막습니다. TXT만 있으면 영향 없습니다.
        import threading

        window = self.make_window()
        window.translation_settings = TranslationSettings(model="test-model")
        window._ollama_connected = True
        window._ollama_model_found = True
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")
            release = threading.Event()

            class SlowAnalyzer:
                def analyze(self, kind, fields, samples):
                    release.wait(timeout=10)
                    return SchemaAnalysis(
                        tuple(fields), (),
                        {name: "ok" for name in fields},
                    )

            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            with patch.object(
                MainWindow, "_create_schema_analyzer", return_value=SlowAnalyzer()
            ):
                window.translation_page.add_file_path(csv_path)
                self.assertIn(csv_path.resolve(), window._schema_pending)
                window._on_translation_start()
                self.assertIn("분석 중", window.translation_page.status_label.text())
                self.assertIsNone(window._translation_worker)
                self.assertNotEqual(window.translation_page._state, "running")
                release.set()
            self.assertTrue(
                self.wait_for(lambda: len(window._threads) == 0, timeout_ms=30000)
            )

    def test_no_analyzer_means_unchecked(self) -> None:
        # Gemini 설정을 사용할 수 없으면 전체 선택 없이 비워 둡니다.
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")
            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,voice,text\n1,f.wav,hello\n", encoding="utf-8")
            with patch.object(MainWindow, "_create_schema_analyzer", return_value=None):
                window.translation_page.add_file_path(csv_path)
            self.assertEqual(
                window.file_field_selections.get(csv_path.resolve()), []
            )
            self.assertEqual(window.translation_page.selected_field_names(), [])
            self.assertNotIn(csv_path.resolve(), window._schema_pending)
            self.assertIn(
                "직접 선택", window.translation_page.analysis_label.text()
            )

    def test_cache_hit_applies_immediately(self) -> None:
        # Cache hit이면 worker 없이 즉시 적용되고 pending에 들어가지 않습니다.
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            cache = SchemaCache(Path(tmp) / "cache.json")
            cache.put(
                "csv", ["id", "text"],
                SchemaAnalysis(("text",), ("id",), {"text": "대사", "id": "코드"}),
            )
            window._schema_cache = cache

            class NeverCalledAnalyzer:
                def analyze(self, kind, fields, samples):
                    raise AssertionError("cache hit에서는 분석기를 호출하면 안 됩니다.")

            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            with patch.object(
                MainWindow, "_create_schema_analyzer",
                return_value=NeverCalledAnalyzer(),
            ):
                window.translation_page.add_file_path(csv_path)
            self.assertEqual(
                window.file_field_selections.get(csv_path.resolve()), ["text"]
            )
            self.assertNotIn(csv_path.resolve(), window._schema_pending)
            checked = {
                check.text()
                for check in window.translation_page.field_checks
                if check.isChecked()
            }
            self.assertEqual(checked, {"text"})

    def test_remove_pending_file_cleans_state(self) -> None:
        # 분석 중 파일 제거 시 pending/selection/info가 정리되고 늦은 결과도 무시됩니다.
        import threading

        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")
            release = threading.Event()

            class SlowAnalyzer:
                def analyze(self, kind, fields, samples):
                    release.wait(timeout=10)
                    return SchemaAnalysis(
                        tuple(fields), (),
                        {name: "ok" for name in fields},
                    )

            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            with patch.object(
                MainWindow, "_create_schema_analyzer", return_value=SlowAnalyzer()
            ):
                window.translation_page.add_file_path(csv_path)
                resolved = csv_path.resolve()
                self.assertIn(resolved, window._schema_pending)
                window.translation_page.remove_file_path(resolved)
                self.assertNotIn(resolved, window._schema_pending)
                self.assertNotIn(resolved, window.file_field_selections)
                self.assertNotIn(resolved, window._file_field_info)
                release.set()
            self.assertTrue(
                self.wait_for(lambda: len(window._threads) == 0, timeout_ms=30000)
            )
            self.assertNotIn(resolved, window._schema_pending)
            self.assertNotIn(resolved, window.file_field_selections)
            self.assertNotIn(resolved, window._file_field_info)

    def test_thread_finish_wiring_clears_refs(self) -> None:
        # 스레드 스케줄링과 무관하게 _launch 정리 wiring 자체를 검증합니다.
        # (thread.finished 동기 broadcast로 ref 정리·추적 집합 정리를 확인)
        window = self.make_window()

        class FakeFactory:
            def translate_all(self, output_func=print):
                raise AssertionError("호출되면 안 됩니다.")

        worker = TranslationWorker(FakeFactory, [])
        thread = QThread()
        window._launch(
            thread,
            worker,
            {"_translation_thread": thread, "_translation_worker": worker},
        )
        window._translation_thread = thread
        window._translation_worker = worker
        thread.finished.emit()
        _app().processEvents()
        self.assertIsNone(window._translation_thread)
        self.assertIsNone(window._translation_worker)
        self.assertNotIn(thread, window._threads)
        self.assertNotIn(worker, window._workers)

    def test_build_handlers_use_per_file_selections(self) -> None:
        # Translator handler가 파일별 선택 field만 받습니다.
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            json_path = Path(tmp) / "b.json"
            json_path.write_text('{"speaker": "A", "voice": "f.wav"}', encoding="utf-8")
            window.file_field_selections = {
                csv_path: ["text"],
                json_path: ["speaker"],
            }
            handlers = window._build_handlers([csv_path, json_path], 10_000)
            self.assertEqual(handlers[csv_path].fields, ["text"])
            self.assertEqual(handlers[json_path].fields, ["speaker"])

    def test_close_immediately_after_start(self) -> None:
        # 실행 직후 닫아도 QThread crash 없이 닫힙니다.
        window = self.make_window()
        window.close()
        _app().processEvents()
        self.assertFalse(window.isVisible())

    def test_no_duplicate_analysis_on_refiles_changed(self) -> None:
        # A. 분석 중 files_changed가 다시 발생해도 같은 파일은 1회만 분석합니다.
        import threading

        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")
            release = threading.Event()
            calls: list[str] = []

            class CountingSlowAnalyzer:
                def analyze(self, kind, fields, samples):
                    calls.append(kind)
                    release.wait(timeout=10)
                    return SchemaAnalysis(
                        tuple(fields), (),
                        {name: "ok" for name in fields},
                    )

            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            json_path = Path(tmp) / "b.json"
            json_path.write_text('{"speaker": "A"}', encoding="utf-8")
            with patch.object(
                MainWindow, "_create_schema_analyzer",
                return_value=CountingSlowAnalyzer(),
            ):
                window.translation_page.add_file_path(csv_path)
                self.assertIn(csv_path.resolve(), window._schema_pending)
                # 분석 완료 전 b.json 추가 → files_changed 재발생.
                window.translation_page.add_file_path(json_path)
                release.set()
            self.assertTrue(
                self.wait_for(lambda: len(window._threads) == 0, timeout_ms=30000)
            )
            # worker 스레드에서 호출되므로 순서는 정렬 후 비교합니다.
            self.assertEqual(sorted(calls), ["csv", "json"])

    def test_input_autoload_analyzes_each_file_once(self) -> None:
        # B. input 자동 로드(a.csv, b.csv, c.json)도 파일당 정확히 1회 분석합니다.
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")
            input_dir = Path(tmp) / "input"
            input_dir.mkdir()
            targets = {
                "a.csv": "id,text\n1,hello\n",
                "b.csv": "code,body\n1,bye\n",
                "c.json": '{"speaker": "A"}',
            }
            for name, content in targets.items():
                (input_dir / name).write_text(content, encoding="utf-8")
            calls: list[str] = []

            class CountingAnalyzer:
                def analyze(self, kind, fields, samples):
                    calls.append(kind)
                    return SchemaAnalysis(
                        tuple(fields), (),
                        {name: "ok" for name in fields},
                    )

            from script.translation.file_loader import FileLoader

            with patch.object(
                MainWindow, "_create_schema_analyzer",
                return_value=CountingAnalyzer(),
            ):
                added = window._load_input_files(FileLoader(input_dir))
            self.assertEqual(added, 3)
            self.assertTrue(
                self.wait_for(lambda: len(window._threads) == 0, timeout_ms=30000)
            )
            self.assertEqual(sorted(calls), ["csv", "csv", "json"])

    def test_direct_reanalyze_call_guarded_while_pending(self) -> None:
        # C. pending 상태에서 직접 재호출해도 analyzer는 한 번만 시작합니다.
        # (결과가 무시되지 않도록 파일을 먼저 등록하는 실제 흐름을 사용합니다.)
        import threading

        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")
            release = threading.Event()
            calls = 0

            class CountingSlowAnalyzer:
                def analyze(self, kind, fields, samples):
                    nonlocal calls
                    calls += 1
                    release.wait(timeout=10)
                    return SchemaAnalysis(
                        tuple(fields), (),
                        {name: "ok" for name in fields},
                    )

            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            analyzer = CountingSlowAnalyzer()
            with patch.object(
                MainWindow, "_create_schema_analyzer", return_value=analyzer
            ):
                window.translation_page.add_file_path(csv_path)
                window._analyze_file_fields(csv_path.resolve())
                window._analyze_file_fields(csv_path.resolve())
                release.set()
            self.assertTrue(
                self.wait_for(lambda: len(window._threads) == 0, timeout_ms=30000)
            )
            self.assertEqual(calls, 1)
            self.assertEqual(
                window.file_field_selections.get(csv_path.resolve()), ["id", "text"]
            )

    def test_success_result_kept_despite_guard(self) -> None:
        # D. 중복 방지 때문에 정상 성공 결과가 사라지지 않습니다.
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window._schema_cache = SchemaCache(Path(tmp) / "cache.json")

            class FakeAnalyzer:
                def analyze(self, kind, fields, samples):
                    return SchemaAnalysis(
                        ("text",), ("id",),
                        {"text": "대사", "id": "코드"},
                    )

            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            with patch.object(
                MainWindow, "_create_schema_analyzer", return_value=FakeAnalyzer()
            ):
                window.translation_page.add_file_path(csv_path)
            self.assertTrue(
                self.wait_for(
                    lambda: window.file_field_selections.get(csv_path.resolve())
                    == ["text"]
                )
            )
            # 결과가 있는데 다시 files_changed가 와도 유지됩니다.
            window._on_files_changed(window.translation_page.selected_paths())
            self.assertEqual(
                window.file_field_selections.get(csv_path.resolve()), ["text"]
            )


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class PromptLanguageTest(unittest.TestCase):
    def test_fixed_language_dropdowns(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            page = PromptPage(manager=PromptManager(presets_dir=Path(tmp)))
            sources = [
                page.source_combo.itemText(i) for i in range(page.source_combo.count())
            ]
            targets = [
                page.target_combo.itemText(i) for i in range(page.target_combo.count())
            ]
            self.assertEqual(sources, list(PRESET_LANGUAGES))
            self.assertEqual(targets, list(PRESET_LANGUAGES))
            self.assertNotIn("자동 감지", sources + targets)

    def test_legacy_language_preserved_on_load(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            manager = PromptManager(presets_dir=Path(tmp))
            preset = manager.create_preset(
                "legacy", "French", "Korean", "novel", "prompt body"
            )
            manager.save(preset)
            page = PromptPage(manager=manager)
            page.select_preset(page.presets[0])
            self.assertEqual(page.source_combo.currentText(), "French")
            self.assertEqual(page.target_combo.currentText(), "Korean")


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class PromptViewTest(unittest.TestCase):
    def test_default_prompt_body(self) -> None:
        title, subtitle, body = resolve_prompt_view(0, [], "Korean")
        self.assertEqual(title, "기본 Prompt")
        self.assertIn("Korean", subtitle)
        self.assertEqual(
            body, BASIC_TRANSLATION_PROMPT.format(target_language="Korean").strip()
        )
        self.assertNotIn("{target_language}", body)

    def test_preset_body_and_out_of_range(self) -> None:
        presets = [
            PromptPreset(
                "game_dialogue", "Japanese", "Korean", "game_dialogue",
                "preset body", "AI", "2026-01-01", "2026-01-02",
            )
        ]
        title, subtitle, body = resolve_prompt_view(1, presets, "Korean")
        self.assertEqual(title, "game_dialogue")
        self.assertIn("Japanese → Korean", subtitle)
        self.assertEqual(body, "preset body")
        # 범위 밖 index도 기본 Prompt로 안전하게 처리합니다.
        title, _subtitle, body = resolve_prompt_view(99, presets, "Korean")
        self.assertEqual(title, "기본 Prompt")


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class PromptDraftWorkerTest(unittest.TestCase):
    def test_draft_success(self) -> None:
        _app()

        class FakeBuilder:
            def create_draft(self, files, source, target, doc, sample_text=None):
                assert sample_text and "hello" in sample_text
                return "draft:" + doc

        worker = PromptDraftWorker(FakeBuilder(), [], "Japanese", "Korean", "game", "hello")
        drafts: list[str] = []
        errors: list[str] = []
        worker.finished.connect(drafts.append)
        worker.failed.connect(errors.append)
        worker.run()
        self.assertEqual(drafts, ["draft:game"])
        self.assertEqual(errors, [])

    def test_draft_failure_is_safe(self) -> None:
        _app()

        class FailingBuilder:
            def create_draft(self, *args, **kwargs):
                raise RuntimeError("gemini down")

        worker = PromptDraftWorker(FailingBuilder(), [], "Japanese", "Korean", "game")
        drafts: list[str] = []
        errors: list[str] = []
        worker.finished.connect(drafts.append)
        worker.failed.connect(errors.append)
        worker.run()
        self.assertEqual(drafts, [])
        self.assertEqual(errors, ["gemini down"])


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class SchemaWorkerTest(unittest.TestCase):
    def test_analysis_success(self) -> None:
        _app()

        class FakeAnalyzer:
            def analyze(self, kind, fields, samples):
                assert kind == "csv"
                return SchemaAnalysis(("text",), ("id",), {"text": "r", "id": "r"})

        worker = SchemaAnalysisWorker(FakeAnalyzer(), "k", "csv", ["id", "text"], {})
        done: list = []
        errors: list = []
        worker.finished.connect(lambda key, analysis: done.append((key, analysis)))
        worker.failed.connect(lambda key, error: errors.append((key, error)))
        worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][0], "k")
        self.assertEqual(list(done[0][1].translate_fields), ["text"])

    def test_analysis_failure_is_safe(self) -> None:
        _app()

        class FailingAnalyzer:
            def analyze(self, kind, fields, samples):
                raise RuntimeError("bad json")

        worker = SchemaAnalysisWorker(FailingAnalyzer(), "k", "csv", ["id"], {})
        done: list = []
        errors: list = []
        worker.finished.connect(lambda key, analysis: done.append((key, analysis)))
        worker.failed.connect(lambda key, error: errors.append((key, error)))
        worker.run()
        self.assertEqual(done, [])
        self.assertEqual(errors, [("k", "bad json")])


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class _WindowTestBase(unittest.TestCase):
    """MainWindow 기반 테스트 공용 fixture입니다."""

    def setUp(self) -> None:
        _app()
        self.windows: list = []
        self._status_patch = patch(
            "script.gui.workers.fetch_ollama_status",
            return_value={"connected": False, "model_found": False},
        )
        self._status_patch.start()

    def tearDown(self) -> None:
        for window in self.windows:
            try:
                self.wait_for(
                    lambda w=window: w._ollama_connected is not None,
                    timeout_ms=10000,
                )
                self.wait_for(
                    lambda w=window: len(w._threads) == 0,
                    timeout_ms=30000,
                )
                window.close()
            except Exception:
                pass
        try:
            self._status_patch.stop()
        except Exception:
            pass
        _app().processEvents()

    def make_window(self, analyzer=None):
        with patch.object(MainWindow, "_create_schema_analyzer", return_value=analyzer):
            window = MainWindow()
        self.windows.append(window)
        window.translation_page.clear_files()
        window.file_field_selections.clear()
        window._file_field_info.clear()
        window._displayed_schema_file = None
        window.translation_page.set_analysis_status("")
        return window

    def wait_for(self, condition, timeout_ms: int = 3000) -> bool:
        if condition():
            return True
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        timeout = QTimer()
        timeout.setSingleShot(True)

        def check() -> None:
            if condition():
                loop.quit()

        poll.timeout.connect(check)
        timeout.timeout.connect(loop.quit)
        poll.start()
        timeout.start(timeout_ms)
        loop.exec()
        poll.stop()
        timeout.stop()
        return condition()

    def use_tmp_store(self, window, tmp: str):
        from script.chat.session_store import ChatSessionStore

        window.session_store = ChatSessionStore(Path(tmp) / "chat_history.json")
        return window.session_store


class _FakeChatEngine:
    """성공 시에만 history를 남기는 ChatEngine 대역입니다."""

    def __init__(self, answers=("ans",), fail: bool = False) -> None:
        from script.chat.history import ConversationHistory

        self.history = ConversationHistory()
        self._answers = list(answers)
        self._fail = fail

    def chat(self, message: str) -> str:
        if self._fail:
            raise RuntimeError("boom")
        answer = self._answers.pop(0) if self._answers else "ans"
        self.history.add_user_message(message)
        self.history.add_assistant_message(answer)
        return answer

    def clear_history(self) -> None:
        self.history.clear()


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class ChatInputTest(unittest.TestCase):
    def test_enter_sends_once_without_newline(self) -> None:
        _app()
        from script.gui.pages.chat_page import ChatInput

        sent: list = []
        box = ChatInput()
        box.send_pressed.connect(lambda: sent.append(True))
        box.setPlainText("hi")
        box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.assertEqual(len(sent), 1)
        self.assertEqual(box.toPlainText(), "hi")

    def test_numpad_enter_sends(self) -> None:
        _app()
        from script.gui.pages.chat_page import ChatInput

        sent: list = []
        box = ChatInput()
        box.send_pressed.connect(lambda: sent.append(True))
        box.setPlainText("hi")
        box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Enter, Qt.NoModifier))
        self.assertEqual(len(sent), 1)

    def test_shift_enter_inserts_newline_without_send(self) -> None:
        _app()
        from script.gui.pages.chat_page import ChatInput

        sent: list = []
        box = ChatInput()
        box.send_pressed.connect(lambda: sent.append(True))
        box.setPlainText("hi")
        from PySide6.QtGui import QTextCursor

        box.moveCursor(QTextCursor.MoveOperation.End)
        box.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.ShiftModifier)
        )
        self.assertEqual(sent, [])
        self.assertIn("\n", box.toPlainText())

    def test_ime_composing_enter_does_not_send(self) -> None:
        _app()
        from script.gui.pages.chat_page import ChatInput

        sent: list = []
        box = ChatInput()
        box.send_pressed.connect(lambda: sent.append(True))
        box.inputMethodEvent(QInputMethodEvent("한", []))
        box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.assertEqual(sent, [])
        # 조합 확정 후에는 정상 전송됩니다.
        box.inputMethodEvent(QInputMethodEvent("", []))
        box.setPlainText("한")
        box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.assertEqual(len(sent), 1)

    def test_page_empty_input_does_not_send(self) -> None:
        _app()
        page = ChatPage()
        sent: list = []
        page.send_requested.connect(sent.append)
        page.input.setPlainText("   ")
        page._emit_send()
        self.assertEqual(sent, [])

    def test_page_send_button_sends(self) -> None:
        _app()
        page = ChatPage()
        sent: list = []
        page.send_requested.connect(sent.append)
        page.input.setPlainText("hello")
        page.send_button.click()
        self.assertEqual(sent, ["hello"])

    def test_sending_disables_input(self) -> None:
        _app()
        page = ChatPage()
        page.set_sending(True)
        self.assertTrue(page.input.isReadOnly())
        self.assertFalse(page.send_button.isEnabled())
        page.set_sending(False)
        self.assertFalse(page.input.isReadOnly())
        self.assertTrue(page.send_button.isEnabled())


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class ChatSessionGuiTest(_WindowTestBase):
    def test_first_success_saves_session(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("hello",))
            window.chat_page.input.setPlainText("hi")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            sessions = window.session_store.list_sessions()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["title"], "hi")
            self.assertIsNotNone(window._current_session_id)

    def test_new_chat_keeps_old_session(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("hello",))
            window.chat_page.input.setPlainText("hi")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            window._on_new_chat()
            self.assertEqual(window.chat_page.message_count(), 0)
            self.assertIsNone(window._current_session_id)
            self.assertEqual(len(window.session_store.list_sessions()), 1)

    def test_select_restores_messages_and_engine(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("hello",))
            window.chat_page.input.setPlainText("hi")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            session_id = window._current_session_id
            assert session_id is not None
            window._on_new_chat()
            item = window.chat_page.history_list.item(0)
            window.chat_page.history_list.itemClicked.emit(item)
            self.assertEqual(window.chat_page.message_count(), 2)
            self.assertEqual(len(window.chat_engine.history.get_messages()), 2)
            self.assertEqual(window._current_session_id, session_id)

    def test_reload_store_loads_sessions(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("hello",))
            window.chat_page.input.setPlainText("hi")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            from script.chat.session_store import ChatSessionStore

            reloaded = ChatSessionStore(Path(tmp) / "chat_history.json")
            self.assertEqual(len(reloaded.list_sessions()), 1)

    def test_delete_saved_conversation(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("hello",))
            window.chat_page.input.setPlainText("hi")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            with patch("script.gui.main_window.QMessageBox") as box:
                box.question.return_value = box.Yes
                window._on_chat_clear_requested()
            self.assertEqual(window.session_store.list_sessions(), [])
            self.assertEqual(window.chat_page.message_count(), 0)
            self.assertEqual(window.chat_engine.history.get_messages(), [])

    def test_delete_cancelled_changes_nothing(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("hello",))
            window.chat_page.input.setPlainText("hi")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            with patch("script.gui.main_window.QMessageBox") as box:
                box.question.return_value = box.No
                window._on_chat_clear_requested()
            self.assertEqual(len(window.session_store.list_sessions()), 1)
            self.assertEqual(window.chat_page.message_count(), 2)

    def test_failed_request_not_saved_as_pair(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(fail=True)
            window.chat_page.input.setPlainText("hi")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            self.assertEqual(window.session_store.list_sessions(), [])
            self.assertEqual(window.chat_engine.history.get_messages(), [])

    def test_export_empty_blocked(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(())
            with patch(
                "script.gui.main_window.QFileDialog"
            ) as dialog:
                dialog.getSaveFileName.side_effect = AssertionError("호출 금지")
                window._on_chat_export()
            self.assertEqual(window.chat_page.message_count(), 1)  # 안내 bubble만

    def test_export_writes_readable_txt(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("안녕 나야",))
            window.chat_page.input.setPlainText("TCP가 뭐야?")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            target = str(Path(tmp) / "chat.txt")
            with patch("script.gui.main_window.QFileDialog") as dialog:
                dialog.getSaveFileName.return_value = (target, "Text (*.txt)")
                window._on_chat_export()
            content = Path(target).read_text(encoding="utf-8")
            self.assertIn("제목: TCP가 뭐야?", content)
            self.assertIn("[사용자]", content)
            self.assertIn("TCP가 뭐야?", content)
            self.assertIn("[AI]", content)
            self.assertIn("안녕 나야", content)

    def test_export_cancelled_creates_nothing(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("a",))
            window.chat_page.input.setPlainText("q")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            target = str(Path(tmp) / "chat.txt")
            with patch("script.gui.main_window.QFileDialog") as dialog:
                dialog.getSaveFileName.return_value = ("", "")
                window._on_chat_export()
            self.assertFalse(Path(target).exists())

    def test_export_write_failure_is_safe(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("a",))
            window.chat_page.input.setPlainText("q")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            before = window.chat_page.message_count()
            with patch("script.gui.main_window.QFileDialog") as dialog:
                dialog.getSaveFileName.return_value = (tmp, "Text (*.txt)")
                window._on_chat_export()  # 디렉터리에 쓰기 → OSError
            self.assertEqual(window.chat_page.message_count(), before + 1)


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class ChatRaceTest(_WindowTestBase):
    def _busy_window(self, window) -> None:
        thread = MagicMock()
        thread.isRunning.return_value = True
        window._chat_thread = thread

    def test_sending_disables_controls(self) -> None:
        _app()
        page = ChatPage()
        page.set_sending(True)
        self.assertFalse(page.send_button.isEnabled())
        self.assertTrue(page.input.isReadOnly())
        self.assertFalse(page.new_chat_button.isEnabled())
        self.assertFalse(page.history_list.isEnabled())
        self.assertFalse(page.clear_button.isEnabled())
        self.assertFalse(page.export_button.isEnabled())
        page.set_sending(False)
        self.assertTrue(page.send_button.isEnabled())
        self.assertFalse(page.input.isReadOnly())
        self.assertTrue(page.new_chat_button.isEnabled())
        self.assertTrue(page.history_list.isEnabled())
        self.assertTrue(page.clear_button.isEnabled())
        self.assertTrue(page.export_button.isEnabled())

    def test_busy_new_chat_blocked(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("a",))
            window.chat_page.input.setPlainText("q")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            session_id = window._current_session_id
            self._busy_window(window)
            window._on_new_chat()
            self.assertEqual(window._current_session_id, session_id)
            self.assertEqual(window.chat_page.message_count(), 2)
            self.assertEqual(len(window.chat_engine.history.get_messages()), 2)

    def test_busy_select_blocked(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("a",))
            window.chat_page.input.setPlainText("q")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            window._on_new_chat()
            self._busy_window(window)
            first_id = window.session_store.list_sessions()[0]["id"]
            window._on_conversation_selected(first_id)
            self.assertIsNone(window._current_session_id)
            self.assertEqual(window.chat_page.message_count(), 0)

    def test_busy_delete_blocked(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("a",))
            window.chat_page.input.setPlainText("q")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            self._busy_window(window)
            with patch("script.gui.main_window.QMessageBox") as box:
                box.question.return_value = box.Yes
                window._on_chat_clear_requested()
                box.question.assert_not_called()
            self.assertEqual(len(window.session_store.list_sessions()), 1)

    def test_idle_allows_after_success(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("a",))
            window.chat_page.input.setPlainText("q")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            window._chat_thread = None
            window._on_new_chat()
            self.assertIsNone(window._current_session_id)
            self.assertEqual(window.chat_page.message_count(), 0)

    def test_busy_export_blocked(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            self.use_tmp_store(window, tmp)
            window.chat_engine = _FakeChatEngine(("a",))
            window.chat_page.input.setPlainText("q")
            window.chat_page._emit_send()
            self.assertTrue(
                self.wait_for(lambda: window.chat_page.message_count() == 2)
            )
            self._busy_window(window)
            with patch("script.gui.main_window.QFileDialog") as dialog:
                dialog.getSaveFileName.side_effect = AssertionError("호출 금지")
                window._on_chat_export()


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class TranslationFallbackTest(_WindowTestBase):
    def test_local_engine_selected_when_ready(self) -> None:
        from script.providers.local_llm import LocalLLM

        window = self.make_window()
        settings = TranslationSettings(model="m")
        llm = window._create_translation_llm(settings, use_api=False)
        self.assertIsInstance(llm, LocalLLM)

    def test_api_provider_message_shape(self) -> None:
        from script.providers.gemini_translation import GeminiTranslationProvider

        received: list = []

        class FakeClient:
            def create_chat_completion(self, messages):
                received.extend(messages)
                return "TRANSLATED"

        provider = GeminiTranslationProvider(FakeClient())
        self.assertEqual(provider.generate("hello"), "TRANSLATED")
        roles = [m["role"] for m in received]
        self.assertIn("system", roles)
        self.assertIn("hello", [m["content"] for m in received if m["role"] == "user"])

    def test_fallback_cancelled_starts_nothing(self) -> None:
        window = self.make_window()
        window.translation_settings = TranslationSettings(model="test-model")
        window._ollama_connected = False
        window._gemini_available = True
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello", encoding="utf-8")
            window.translation_page.add_file_path(target)
            with patch.object(
                MainWindow, "_confirm_api_fallback", return_value=False
            ):
                window._on_translation_start()
            self.assertIsNone(window._translation_worker)
            self.assertNotEqual(window.translation_page._state, "running")

    def test_no_gemini_blocks_api_translation(self) -> None:
        window = self.make_window()
        window.translation_settings = TranslationSettings(model="test-model")
        window._ollama_connected = False
        window._gemini_available = False
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello", encoding="utf-8")
            window.translation_page.add_file_path(target)
            window._on_translation_start()
            self.assertIn("사용 가능한 번역 엔진이 없습니다", window.translation_page.status_label.text())
            self.assertIsNone(window._translation_worker)

    def test_empty_ollama_model_reaches_fallback(self) -> None:
        # A. OLLAMA_MODEL이 비어도 settings가 None이 되지 않고 fallback 확인에 도달합니다.
        window = self.make_window()
        window.translation_settings = TranslationSettings(model="")
        window._ollama_connected = False
        window._gemini_available = True
        self.assertIsNotNone(window.translation_settings)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello", encoding="utf-8")
            window.translation_page.add_file_path(target)
            with patch.object(
                MainWindow, "_confirm_api_fallback", return_value=False
            ):
                window._on_translation_start()
            self.assertIsNone(window._translation_worker)
            self.assertNotEqual(window.translation_page._state, "running")

    def test_empty_model_approved_starts_gemini_job(self) -> None:
        # B. 승인 시 Gemini provider로 TranslationWorker가 시작됩니다.
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            window.translation_settings = TranslationSettings(
                model="", input_dir=Path(tmp), output_dir=Path(tmp) / "out"
            )
            window._ollama_connected = False
            window._gemini_available = True
            target = Path(tmp) / "a.txt"
            target.write_text("hello {player_name}", encoding="utf-8")
            window.translation_page.add_file_path(target)
            with patch.object(MainWindow, "_confirm_api_fallback", return_value=True), \
                patch.object(window, "_create_translation_llm", return_value=self._echo_llm()):
                window._on_translation_start()
            self.assertTrue(
                self.wait_for(
                    lambda: window.translation_page._state in {"done", "stopped"},
                    timeout_ms=30000,
                )
            )
            out = Path(tmp) / "out" / "a.txt"
            self.assertTrue(out.is_file())
            self.assertIn("{player_name}", out.read_text(encoding="utf-8"))

    def test_empty_model_no_gemini_blocks(self) -> None:
        # D. OLLAMA_MODEL 없음 + Gemini 설정 없음 → 시작 금지 + 안내.
        window = self.make_window()
        window.translation_settings = TranslationSettings(model="")
        window._ollama_connected = False
        window._gemini_available = False
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello", encoding="utf-8")
            window.translation_page.add_file_path(target)
            window._on_translation_start()
            self.assertIn(
                "사용 가능한 번역 엔진이 없습니다",
                window.translation_page.status_label.text(),
            )
            self.assertIsNone(window._translation_worker)

    def _echo_llm(self):
        class EchoLLM:
            def generate(self, prompt: str) -> str:
                marker = "[원문]\n"
                body = prompt.split(marker, 1)[1] if marker in prompt else prompt
                return "ECHO:" + body

        return EchoLLM()

    def _run_api_translation(self, window, tmp: str, files: dict) -> Path:
        out_dir = Path(tmp) / "out"
        window.translation_settings = TranslationSettings(
            model="test-model", input_dir=Path(tmp), output_dir=out_dir
        )
        window._ollama_connected = False
        window._gemini_available = True
        with patch.object(MainWindow, "_create_schema_analyzer", return_value=None):
            for name, content in files.items():
                path = Path(tmp) / name
                path.write_text(content, encoding="utf-8")
                window.translation_page.add_file_path(path)
        for check in window.translation_page.field_checks:
            check.setChecked(check.text() in {"text", "dialogue", "body"})
        with patch.object(MainWindow, "_confirm_api_fallback", return_value=True), \
            patch.object(window, "_create_translation_llm", return_value=self._echo_llm()):
            window._on_translation_start()
        self.assertTrue(
            self.wait_for(
                lambda: window.translation_page._state in {"done", "stopped"},
                timeout_ms=30000,
            )
        )
        return out_dir

    def test_api_fallback_txt_with_placeholder(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = self._run_api_translation(
                window, tmp, {"a.txt": "hello {player_name} %s"}
            )
            content = (out_dir / "a.txt").read_text(encoding="utf-8")
            self.assertIn("{player_name}", content)
            self.assertIn("%s", content)

    def test_api_fallback_csv_selected_only(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = self._run_api_translation(
                window, tmp, {"a.csv": "id,text\n1,hi {player_name}\n"}
            )
            import csv as csv_module

            with (out_dir / "a.csv").open(encoding="utf-8") as stream:
                rows = list(csv_module.DictReader(stream))
            self.assertEqual(rows[0]["id"], "1")
            self.assertIn("{player_name}", rows[0]["text"])

    def test_api_fallback_json_selected_only(self) -> None:
        import json as json_module

        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = self._run_api_translation(
                window, tmp, {"a.json": '{"voice": "f.wav", "dialogue": "hi ${v}}"}'}
            )
            data = json_module.loads((out_dir / "a.json").read_text(encoding="utf-8"))
            self.assertEqual(data["voice"], "f.wav")
            self.assertIn("${v}", data["dialogue"])

    def test_no_midrun_auto_fallback(self) -> None:
        from script.translation.file_loader import FileLoader
        from script.translation.file_writer import FileWriter
        from script.translation.translator import Translator

        class FailingLLM:
            def generate(self, prompt: str) -> str:
                raise RuntimeError("ollama down")

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.txt"
            src.write_text("hello", encoding="utf-8")
            settings = TranslationSettings(
                model="m", input_dir=Path(tmp), output_dir=Path(tmp) / "out"
            )
            translator = Translator(
                loader=FileLoader(settings.input_dir),
                local_llm=FailingLLM(),
                writer=FileWriter(settings.output_dir),
                file_paths=[src],
            )
            summary = translator.translate_all(output_func=lambda _m: None)
            self.assertEqual(summary.failed, 1)


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class PromptPageGenerateTest(_WindowTestBase):
    def test_ai_button_emits_signal(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            page = PromptPage(manager=PromptManager(presets_dir=Path(tmp)))
            fired: list = []
            page.ai_generate_requested.connect(lambda: fired.append(True))
            page.ai_button.click()
            self.assertEqual(fired, [True])

    def test_empty_doctype_dialog_cancel_blocks_call(self) -> None:
        window = self.make_window()
        window.prompt_page.doc_edit.clear()
        with patch("script.gui.main_window.QInputDialog") as dialog:
            dialog.getText.return_value = ("", False)
            window._on_prompt_page_generate()
        self.assertIsNone(window._prompt_worker)

    def test_empty_doctype_dialog_accept_continues(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello world, hello again", encoding="utf-8")
            window.translation_page.add_file_path(target)
            window.prompt_page.doc_edit.clear()

            class FakeBuilder:
                def create_draft(self, files, source, target, doc, sample_text=None):
                    assert doc == "my_docs"
                    return "DRAFT-DOC"

            with patch("script.gui.main_window.QInputDialog") as dialog, \
                patch("script.gui.main_window.Settings") as settings_cls, \
                patch("script.gui.main_window.PromptBuilder", return_value=FakeBuilder()):
                dialog.getText.return_value = ("my_docs", True)
                settings_cls.from_env.return_value.validate.return_value = None
                window._on_prompt_page_generate()
                self.assertTrue(
                    self.wait_for(lambda: "DRAFT-DOC" in window.prompt_page.editor_text())
                )
            self.assertEqual(window.prompt_page.doc_edit.text(), "my_docs")

    def test_initial_empty_state_enters_new_prompt_mode(self) -> None:
        window = self.make_window()
        self.assertEqual(window.prompt_page.title_label.text(), "Prompt를 선택하세요")
        window.prompt_page.doc_edit.clear()
        with patch("script.gui.main_window.QInputDialog") as dialog:
            dialog.getText.return_value = ("", False)
            window._on_prompt_page_generate()
        # 새 Prompt mode 진입: form/editor 입력 가능, 취소 시 Gemini 호출 없음.
        self.assertTrue(window.prompt_page.is_new)
        self.assertFalse(window.prompt_page.editor.isReadOnly())
        self.assertTrue(window.prompt_page.doc_edit.isEnabled())
        self.assertIsNone(window._prompt_worker)

    def test_success_inserts_draft_without_save(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello world, hello again", encoding="utf-8")
            window.translation_page.add_file_path(target)

            class FakeBuilder:
                def create_draft(self, files, source, target, doc, sample_text=None):
                    assert sample_text and "hello" in sample_text
                    return "DRAFT-BODY"

            window.prompt_page.doc_edit.setText("game_dialogue")
            with patch("script.gui.main_window.QInputDialog") as dialog, \
                patch("script.gui.main_window.Settings") as settings_cls, \
                patch("script.gui.main_window.PromptBuilder", return_value=FakeBuilder()):
                dialog.getText.return_value = ("game_dialogue", True)
                settings_cls.from_env.return_value.validate.return_value = None
                window._on_prompt_page_generate()
                self.assertTrue(
                    self.wait_for(lambda: "DRAFT-BODY" in window.prompt_page.editor_text())
                )
            self.assertIn("DRAFT-BODY", window.prompt_page.editor_text())
            self.assertTrue(window.prompt_page.ai_draft)
            self.assertTrue(window.prompt_page.ai_button.isEnabled())

    def test_no_files_uses_dialog_sample(self) -> None:
        window = self.make_window()
        window.translation_page.clear_files()
        window.file_field_selections.clear()
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "s.txt"
            sample.write_text("sample text here", encoding="utf-8")

            class FakeBuilder:
                def create_draft(self, files, source, target, doc, sample_text=None):
                    assert sample_text and "sample text" in sample_text
                    return "DRAFT2"

            window.prompt_page.doc_edit.setText("novel")
            with patch("script.gui.main_window.QFileDialog") as filedialog, \
                patch("script.gui.main_window.QInputDialog") as dialog, \
                patch("script.gui.main_window.Settings") as settings_cls, \
                patch("script.gui.main_window.PromptBuilder", return_value=FakeBuilder()):
                filedialog.getOpenFileName.return_value = (str(sample), "")
                dialog.getText.return_value = ("novel", True)
                settings_cls.from_env.return_value.validate.return_value = None
                window._on_prompt_page_generate()
                self.assertTrue(
                    self.wait_for(lambda: "DRAFT2" in window.prompt_page.editor_text())
                )
            self.assertEqual(window.translation_page.selected_paths(), [])

    def test_failure_keeps_editor(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello hello hello", encoding="utf-8")
            window.translation_page.add_file_path(target)
            window.prompt_page.set_editor_text("기존 내용 유지")

            class FailingBuilder:
                def create_draft(self, *args, **kwargs):
                    raise RuntimeError("gemini down")

            window.prompt_page.doc_edit.setText("game_dialogue")
            with patch("script.gui.main_window.QInputDialog") as dialog, \
                patch("script.gui.main_window.Settings") as settings_cls, \
                patch("script.gui.main_window.PromptBuilder", return_value=FailingBuilder()):
                dialog.getText.return_value = ("game_dialogue", True)
                settings_cls.from_env.return_value.validate.return_value = None
                window._on_prompt_page_generate()
                self.assertTrue(
                    self.wait_for(
                        lambda: "실패" in window.prompt_page.validation_label.text()
                    )
                )
            self.assertEqual(window.prompt_page.editor_text(), "기존 내용 유지")
            self.assertTrue(window.prompt_page.ai_button.isEnabled())

    def test_busy_disables_button(self) -> None:
        import threading

        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello hello hello", encoding="utf-8")
            window.translation_page.add_file_path(target)
            release = threading.Event()

            class SlowBuilder:
                def create_draft(self, *args, **kwargs):
                    release.wait(timeout=10)
                    return "SLOW"

            window.prompt_page.doc_edit.setText("game_dialogue")
            with patch("script.gui.main_window.QInputDialog") as dialog, \
                patch("script.gui.main_window.Settings") as settings_cls, \
                patch("script.gui.main_window.PromptBuilder", return_value=SlowBuilder()):
                dialog.getText.return_value = ("game_dialogue", True)
                settings_cls.from_env.return_value.validate.return_value = None
                window._on_prompt_page_generate()
                self.assertFalse(window.prompt_page.ai_button.isEnabled())
                release.set()
                self.assertTrue(
                    self.wait_for(lambda: "SLOW" in window.prompt_page.editor_text())
                )
            self.assertTrue(window.prompt_page.ai_button.isEnabled())

    def test_existing_preset_not_overwritten(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("hello world, hello again", encoding="utf-8")
            window.translation_page.add_file_path(target)
            manager = PromptManager(presets_dir=Path(tmp) / "presets")
            preset = manager.create_preset(
                "keep", "Japanese", "Korean", "game_dialogue", "original body"
            )
            saved_path = manager.save(preset)
            before = saved_path.read_text(encoding="utf-8")
            window.prompt_page.manager = manager
            window.prompt_page.load_presets()
            window.prompt_page.select_preset(window.prompt_page.presets[0])

            class FakeBuilder:
                def create_draft(self, files, source, target, doc, sample_text=None):
                    return "NEW-DRAFT"

            with patch("script.gui.main_window.QInputDialog") as dialog, \
                patch("script.gui.main_window.Settings") as settings_cls, \
                patch("script.gui.main_window.PromptBuilder", return_value=FakeBuilder()):
                dialog.getText.return_value = ("game_dialogue", True)
                settings_cls.from_env.return_value.validate.return_value = None
                window._on_prompt_page_generate()
                self.assertTrue(
                    self.wait_for(lambda: "NEW-DRAFT" in window.prompt_page.editor_text())
                )
            self.assertEqual(saved_path.read_text(encoding="utf-8"), before)
            self.assertIsNone(window.prompt_page.current)
            self.assertTrue(window.prompt_page.ai_draft)

    def test_ai_draft_save_marks_ai(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            manager = PromptManager(presets_dir=Path(tmp))
            page = PromptPage(manager=manager)
            page.apply_ai_draft("draft body", "Japanese", "Korean", "game_dialogue")
            with patch.object(page, "_ask_text", return_value=("n1", True)):
                page.save_current()
            saved = manager.find_by_name("n1", "Japanese", "Korean", "game_dialogue")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.created_by, "AI")

    def test_manual_save_marks_manual(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            manager = PromptManager(presets_dir=Path(tmp))
            page = PromptPage(manager=manager)
            page.new_prompt()
            page.set_editor_text("manual body")
            page.doc_edit.setText("novel")
            with patch.object(page, "_ask_text", return_value=("n2", True)):
                page.save_current()
            saved = manager.find_by_name("n2", "Japanese", "Korean", "novel")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.created_by, "Manual")


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6이 필요합니다.")
class SidebarInputTest(_WindowTestBase):
    def test_no_settings_button(self) -> None:
        from PySide6.QtWidgets import QPushButton
        from script.gui.widgets.sidebar import Sidebar

        _app()
        sidebar = Sidebar()
        self.assertFalse(hasattr(sidebar, "btn_settings"))
        texts = [button.text() for button in sidebar.findChildren(QPushButton)]
        self.assertTrue(all("환경설정" not in text for text in texts))

    def test_three_pages_navigate(self) -> None:
        window = self.make_window()
        window.sidebar.btn_chat.click()
        self.assertEqual(window.stack.currentIndex(), 0)
        window.sidebar.btn_translation.click()
        self.assertEqual(window.stack.currentIndex(), 1)
        window.sidebar.btn_prompt.click()
        self.assertEqual(window.stack.currentIndex(), 2)

    def test_open_input_button_signal(self) -> None:
        _app()
        page = TranslationPage()
        fired: list = []
        page.open_input_requested.connect(lambda: fired.append(True))
        page.btn_open_input.click()
        self.assertEqual(fired, [True])

    def test_open_input_creates_dir_and_url(self) -> None:
        from PySide6.QtGui import QDesktopServices

        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new-input"
            window.translation_settings = TranslationSettings(
                model="m", input_dir=target, output_dir=Path(tmp) / "out"
            )
            opened: list = []
            with patch.object(
                QDesktopServices, "openUrl", return_value=True
            ) as opener:
                window._on_open_input()
                opened.extend(
                    call.args[0].toString() for call in opener.call_args_list
                )
            self.assertTrue(target.is_dir())
            self.assertTrue(any("new-input" in url for url in opened))


if __name__ == "__main__":
    unittest.main()
