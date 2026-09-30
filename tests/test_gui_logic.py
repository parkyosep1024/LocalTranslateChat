"""GUI 초기 상태와 backend 연결 구조를 확인합니다. display가 없으면 offscreen을 사용합니다."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    from script.gui.main_window import (
        collect_detection_sample,
        detect_source_language,
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


if __name__ == "__main__":
    unittest.main()
