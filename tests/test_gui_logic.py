"""GUI 초기 상태와 backend 연결 구조를 확인합니다. display가 없으면 offscreen을 사용합니다."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEventLoop, QThread, QTimer
    from PySide6.QtWidgets import QApplication

    from script.config.translation_settings import TranslationSettings
    from script.gui.main_window import (
        collect_detection_sample,
        detect_source_language,
        find_unmatched_structured_files,
        resolve_fields_for_handler,
        resolve_prompt_selection,
    )
    from script.gui.pages.chat_page import ChatPage
    from script.gui.pages.prompt_page import PromptPage
    from script.gui.pages.translation_page import TranslationPage
    from script.gui.workers import (
        ChatWorker,
        TranslationWorker,
        fetch_ollama_status,
        ollama_model_found,
    )
    from script.translation.prompt_manager import PromptManager, PromptPreset

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
                window.close()
            except Exception:
                pass
        _app().processEvents()

    def make_window(self):
        from script.gui.main_window import MainWindow

        window = MainWindow()
        self.windows.append(window)
        return window

    def wait_for(self, condition, timeout_ms: int = 8000) -> bool:
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        while not condition():
            loop.exec()
            if not timer.isActive():
                break
        timer.stop()
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

    def test_mismatched_schema_blocks_start(self) -> None:
        # BUG3: a.csv(id/text) 선택값이 b.json(speaker/dialogue)에 없으면 차단합니다.
        window = self.make_window()
        window.translation_settings = TranslationSettings(model="test-model")
        window._ollama_connected = True
        window._ollama_model_found = True
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            json_path = Path(tmp) / "b.json"
            json_path.write_text('{"speaker": "A", "dialogue": "hi"}', encoding="utf-8")
            window.translation_page.add_file_path(csv_path)
            window.translation_page.add_file_path(json_path)
            window._on_translation_start()
            self.assertIn("b.json", window.translation_page.status_label.text())
            self.assertIsNone(window._translation_worker)
            self.assertNotEqual(window.translation_page._state, "running")

    def test_find_unmatched_structured_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "a.csv"
            csv_path.write_text("id,text\n1,hello\n", encoding="utf-8")
            json_path = Path(tmp) / "b.json"
            json_path.write_text('{"speaker": "A", "dialogue": "hi"}', encoding="utf-8")
            offenders = find_unmatched_structured_files(
                [csv_path, json_path], ["id", "text"]
            )
            self.assertEqual(offenders, [json_path])
            # 같은 schema면 차단하지 않습니다.
            csv2 = Path(tmp) / "c.csv"
            csv2.write_text("id,text\n2,bye\n", encoding="utf-8")
            self.assertEqual(
                find_unmatched_structured_files([csv_path, csv2], ["text"]), []
            )
            # TXT는 검사 대상이 아닙니다.
            txt = Path(tmp) / "n.txt"
            txt.write_text("plain", encoding="utf-8")
            self.assertEqual(
                find_unmatched_structured_files([txt, json_path], ["dialogue"]), []
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
        self.assertTrue(self.wait_for(lambda: window._chat_thread is None))
        self.assertIsNone(window._chat_worker)
        self.assertTrue(window.chat_page.send_button.isEnabled())
        # 두 번째 Chat이 삭제된 객체 없이 시작되어야 합니다.
        window.chat_page.input.setPlainText("again")
        window.chat_page._emit_send()
        self.assertTrue(
            self.wait_for(lambda: window.chat_page.message_count() == 4),
            "두 번째 Chat이 시작되지 않았습니다.",
        )
        self.assertTrue(self.wait_for(lambda: window._chat_thread is None))

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
        self.assertTrue(self.wait_for(lambda: window._translation_thread is None))
        self.assertIsNone(window._translation_worker)
        self.assertEqual(window.translation_page.state_badge.text(), "완료")

    def test_translation_stopped_then_restartable(self) -> None:
        window = self.make_window()
        done = self._launch_test_translation(window, self._summary_factory(stopped=True))
        self.assertTrue(self.wait_for(lambda: len(done) == 1))
        self.assertEqual(done, ["stopped"])
        self.assertTrue(self.wait_for(lambda: window._translation_thread is None))
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
        self.assertTrue(self.wait_for(lambda: window._translation_thread is None))
        self.assertIsNone(window._translation_worker)
        done2 = self._launch_test_translation(window, self._summary_factory())
        self.assertTrue(self.wait_for(lambda: len(done2) == 1))
        self.assertEqual(done2, ["finished"])


if __name__ == "__main__":
    unittest.main()
