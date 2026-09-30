from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from script.config.translation_settings import TranslationSettings
from script.translate_main import (
    _select_and_confirm_prompt,
    main,
    run_interactive_translation,
    run_menu,
)
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
from script.translation.formats import create_handler
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
    def test_cli_translates_multiple_units_and_files_without_intermediate_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            csv_path = input_dir / "001.csv"
            json_path = input_dir / "002.json"
            txt_path = input_dir / "003.txt"
            csv_path.write_text("id,text\n1,hello\n2,bye\n", encoding="utf-8")
            json_path.write_text('{"lines":["hello","bye"]}', encoding="utf-8")
            txt_path.write_text("1111\n2222", encoding="utf-8")
            settings = TranslationSettings(
                model="test-model",
                chunk_max_chars=5,
                input_dir=input_dir,
                output_dir=output_dir,
            )
            handlers = {}
            for path, fields in ((csv_path, ["text"]), (json_path, ["lines"])):
                handler = create_handler(path, settings.chunk_max_chars)
                handler.load(path)
                handler.select_fields(fields)
                handlers[path] = handler
            txt_handler = create_handler(txt_path, settings.chunk_max_chars)
            txt_handler.load(txt_path)
            handlers[txt_path] = txt_handler
            inputs = iter(["1", "1", "1", "1"])
            prompts: list[str] = []
            translation_started = False

            def input_func(prompt: str) -> str:
                if translation_started:
                    raise AssertionError("번역 시작 후 input()이 호출되었습니다.")
                prompts.append(prompt)
                return next(inputs)

            def generate_translation(prompt: str) -> str:
                nonlocal translation_started
                translation_started = True
                return prompt.split("[원문]\n", 1)[1].upper()

            with (
                patch(
                    "script.translate_main.TranslationSettings.from_env",
                    return_value=settings,
                ),
                patch(
                    "script.translate_main._prepare_handlers",
                    return_value=(handlers, "hello bye"),
                ),
                patch(
                    "script.translate_main.PromptManager",
                    return_value=PromptManager(root / "presets"),
                ),
                patch(
                    "script.translate_main.LocalLLM.generate",
                    side_effect=generate_translation,
                ) as generate,
            ):
                run_interactive_translation(
                    input_func=input_func,
                    output_func=lambda _message: None,
                )

            self.assertEqual(len(prompts), 4)
            self.assertFalse(any("계속하려면" in prompt for prompt in prompts))
            self.assertEqual(generate.call_count, 6)
            self.assertTrue((output_dir / "001.csv").exists())
            self.assertTrue((output_dir / "002.json").exists())
            self.assertTrue((output_dir / "003.txt").exists())

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

    def test_main_converts_keyboard_interrupt_to_clean_exit(self) -> None:
        with (
            patch("script.translate_main.run_menu", side_effect=KeyboardInterrupt),
            patch("builtins.print") as print_mock,
        ):
            exit_code = main()

        self.assertEqual(exit_code, 130)
        print_mock.assert_called_once_with("\n프로그램을 종료합니다.")


if __name__ == "__main__":
    unittest.main()
