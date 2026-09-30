"""GUI 초기 상태와 backend 연결 구조를 확인합니다. display가 없으면 offscreen을 사용합니다."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    from script.gui.pages.chat_page import ChatPage
    from script.gui.pages.prompt_page import PromptPage
    from script.gui.pages.translation_page import TranslationPage
    from script.gui.workers import ChatWorker, TranslationWorker
    from script.translation.prompt_manager import PromptManager

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
        finished: list = []
        worker.finished.connect(finished.append)
        worker.run()
        self.assertTrue(seen, "Translator에 stop callback이 전달되어야 합니다.")


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
