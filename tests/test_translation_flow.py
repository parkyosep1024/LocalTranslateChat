from pathlib import Path
import tempfile
import unittest

from script.translate_main import _select_and_confirm_prompt, run_menu
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
from script.translation.prompt_manager import PromptManager
from script.translation.translator import (
    Translator,
    build_translation_prompt,
)


class RecordingLocalLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "번역 결과"


class TranslationFlowTests(unittest.TestCase):
    def test_custom_prompt_and_languages_are_added_to_every_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "text.txt").write_text("1111\n2222", encoding="utf-8")
            llm = RecordingLocalLLM()
            translator = Translator(
                FileLoader(input_dir),
                llm,  # type: ignore[arg-type]
                FileWriter(output_dir),
                chunk_max_chars=5,
                source_language="Japanese",
                target_language="English",
                selected_prompt="CUSTOM RULE",
                prompt_name="Custom",
            )

            summary = translator.translate_all(output_func=lambda _message: None)

            self.assertEqual(summary.succeeded, 1)
            self.assertEqual(len(llm.prompts), 2)
            for prompt in llm.prompts:
                self.assertIn("CUSTOM RULE", prompt)
                self.assertIn("원본 언어: Japanese", prompt)
                self.assertIn("목표 언어: English", prompt)

    def test_default_prompt_uses_selected_target_language(self) -> None:
        prompt = build_translation_prompt("source", "Korean", "Japanese")

        self.assertIn("Japanese(으)로", prompt)
        self.assertIn("원본 언어: Korean", prompt)

    def test_stop_between_chunks_leaves_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "stop.txt").write_text("1111\n2222", encoding="utf-8")
            translator = Translator(
                FileLoader(input_dir),
                RecordingLocalLLM(),  # type: ignore[arg-type]
                FileWriter(output_dir),
                chunk_max_chars=5,
                stop_requested=lambda: True,
            )

            summary = translator.translate_all(output_func=lambda _message: None)

            self.assertTrue(summary.was_stopped)
            self.assertEqual(summary.stopped, 1)
            self.assertEqual(summary.stopped_file, "stop.txt")
            self.assertFalse((output_dir / "stop.txt").exists())

    def test_stop_between_files_preserves_completed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "001.txt").write_text("first", encoding="utf-8")
            (input_dir / "002.txt").write_text("second", encoding="utf-8")
            translator = Translator(
                FileLoader(input_dir),
                RecordingLocalLLM(),  # type: ignore[arg-type]
                FileWriter(output_dir),
                stop_requested=lambda: True,
            )

            summary = translator.translate_all(output_func=lambda _message: None)

            self.assertTrue(summary.was_stopped)
            self.assertEqual(summary.succeeded, 1)
            self.assertTrue((output_dir / "001.txt").exists())
            self.assertFalse((output_dir / "002.txt").exists())

    def test_preset_language_mismatch_warns_and_can_still_be_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = PromptManager(Path(directory) / "presets")
            manager.save(
                manager.create_preset(
                    "JP to KR",
                    "Japanese",
                    "Korean",
                    "dialogue",
                    "PRESET RULE",
                )
            )
            inputs = iter(["2", "1", "1"])
            outputs: list[str] = []

            selected = _select_and_confirm_prompt(
                manager,
                "English",
                "Korean",
                input_func=lambda _prompt: next(inputs),
                output_func=outputs.append,
            )

            self.assertEqual(selected, ("PRESET RULE", "JP to KR"))
            self.assertTrue(any("언어 설정이" in line for line in outputs))

    def test_default_prompt_fallback_can_be_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = PromptManager(Path(directory) / "presets")
            inputs = iter(["1", "1"])

            selected = _select_and_confirm_prompt(
                manager,
                "English",
                "Korean",
                input_func=lambda _prompt: next(inputs),
                output_func=lambda _message: None,
            )

            self.assertEqual(selected, (None, "기본 Prompt"))

    def test_menu_can_exit(self) -> None:
        inputs = iter(["3"])
        outputs: list[str] = []

        exit_code = run_menu(
            input_func=lambda _prompt: next(inputs), output_func=outputs.append
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("프로그램을 종료합니다.", outputs)


if __name__ == "__main__":
    unittest.main()
