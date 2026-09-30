"""GUI 초기 상태와 backend 연결 구조를 확인합니다. display가 없으면 offscreen을 사용합니다."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEventLoop, QThread, QTimer
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
                    timeout_ms=10000,
                )
                window.close()
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
        self.assertTrue(
            self.wait_for(lambda: window.chat_page.message_count() == 2),
            "AI 답변을 받지 못했습니다.",
        )
        self.assertTrue(self.wait_for(lambda: window._chat_thread is None, timeout_ms=10000))
        self.assertIsNone(window._chat_worker)
        self.assertTrue(window.chat_page.send_button.isEnabled())
        # 두 번째 Chat이 삭제된 객체 없이 시작되어야 합니다.
        window.chat_page.input.setPlainText("again")
        window.chat_page._emit_send()
        self.assertTrue(
            self.wait_for(lambda: window.chat_page.message_count() == 4),
            "두 번째 Chat이 시작되지 않았습니다.",
        )
        self.assertTrue(self.wait_for(lambda: window._chat_thread is None, timeout_ms=10000))

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
        self.assertTrue(self.wait_for(lambda: window._translation_thread is None, timeout_ms=10000))
        self.assertIsNone(window._translation_worker)
        self.assertEqual(window.translation_page.state_badge.text(), "완료")

    def test_translation_stopped_then_restartable(self) -> None:
        window = self.make_window()
        done = self._launch_test_translation(window, self._summary_factory(stopped=True))
        self.assertTrue(self.wait_for(lambda: len(done) == 1))
        self.assertEqual(done, ["stopped"])
        self.assertTrue(self.wait_for(lambda: window._translation_thread is None, timeout_ms=10000))
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
        self.assertTrue(self.wait_for(lambda: window._translation_thread is None, timeout_ms=10000))
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

    def test_schema_analysis_runs_async(self) -> None:
        # 분석이 끝나기 전에는 GUI가 막히지 않고 기존 화면을 유지합니다.
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
                # worker가 끝나기 전에도 페이지는 응답합니다.
                self.assertTrue(window.translation_page.btn_start.isEnabled())
                release.set()
            self.assertTrue(
                self.wait_for(
                    lambda: window.file_field_selections.get(csv_path.resolve())
                    == ["id", "text"]
                )
            )

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


if __name__ == "__main__":
    unittest.main()
