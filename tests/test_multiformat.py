import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from script.translate_main import _prepare_handlers
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
from script.translation.formats import create_handler
from script.translation.formats.csv_handler import CsvHandler
from script.translation.formats.json_handler import JsonHandler
from script.translation.placeholder import protect_placeholders, restore_placeholders
from script.translation.prompt_builder import PromptBuilder
from script.translation.translator import Translator


class UppercaseLLM:
    def __init__(self, fail_on: str = "", corrupt_token: bool = False) -> None:
        self.prompts: list[str] = []
        self.fail_on = fail_on
        self.corrupt_token = corrupt_token

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.fail_on and self.fail_on in prompt:
            raise RuntimeError("LLM failure")
        result = prompt.split("[원문]\n", 1)[1].upper()
        return result.replace("__PH_0__", "__BROKEN__") if self.corrupt_token else result


class FakePromptAI:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def generate(self, prompt: str) -> str:
        self.requests.append(prompt)
        return "PROMPT"


class HandlerTests(unittest.TestCase):
    def test_supports_exactly_three_extensions_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a.TXT", "b.CSV", "c.JSON", "d.xml", "e.csv.bak"):
                (root / name).write_text("x", encoding="utf-8")
            self.assertEqual(
                [path.name for path in FileLoader(root).list_supported_files()],
                ["a.TXT", "b.CSV", "c.JSON"],
            )

    def test_csv_selected_columns_preserve_other_cells_and_quoted_commas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dialogue.csv"
            path.write_text(
                'ID,Character,Text,Description\n1,Alice,"Hello, world!",hero\n2,Bob,,npc\n',
                encoding="utf-8",
            )
            handler = CsvHandler()
            handler.load(path)
            self.assertEqual(handler.available_fields(), ["ID", "Character", "Text", "Description"])
            handler.select_fields(["Text", "Description"])
            units = handler.extract_units()
            self.assertEqual(
                [(unit.key, unit.text) for unit in units],
                [("row_0:Text", "Hello, world!"), ("row_0:Description", "hero"), ("row_1:Description", "npc")],
            )
            handler.apply_translations({unit.key: unit.text.upper() for unit in units})
            rows = list(csv.reader(io.StringIO(handler.serialize())))
            self.assertEqual(rows[0], ["ID", "Character", "Text", "Description"])
            self.assertEqual(rows[1], ["1", "Alice", "HELLO, WORLD!", "HERO"])
            self.assertEqual(rows[2], ["2", "Bob", "", "NPC"])

    def test_csv_bom_and_semicolon_dialect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.csv"
            path.write_bytes("\ufeffid;text\n1;こんにちは\n".encode("utf-8"))
            handler = CsvHandler()
            handler.load(path)
            handler.select_fields(["text"])
            units = handler.extract_units()
            handler.apply_translations({units[0].key: "안녕하세요"})
            self.assertTrue(handler.serialize().startswith("\ufeffid;text"))
            self.assertIn("1;안녕하세요", handler.serialize())

    def test_csv_invalid_header_and_row_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            handler = CsvHandler()
            for content in ("", "a,b\n1\n"):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(Exception):
                    handler.load(path)

    def test_json_nested_values_and_types_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "story.json"
            original = {
                "id": 1001, "enabled": True, "missing": None, "icon": "alice.png",
                "characters": [
                    {"dialogue": "Hello", "name": "Alice"},
                    {"dialogue": "Bye", "name": "Bob"},
                ],
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            handler = JsonHandler()
            handler.load(path)
            self.assertIn("dialogue", handler.available_fields())
            handler.select_fields(["dialogue", "name"])
            units = handler.extract_units()
            self.assertIn("characters.0.dialogue", [unit.key for unit in units])
            handler.apply_translations({unit.key: unit.text.upper() for unit in units})
            result = json.loads(handler.serialize())
            self.assertEqual(result["characters"][0]["dialogue"], "HELLO")
            self.assertEqual(result["characters"][1]["name"], "BOB")
            self.assertEqual(result["icon"], "alice.png")
            self.assertEqual((result["id"], result["enabled"], result["missing"]), (1001, True, None))

    def test_json_list_strings_under_selected_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "list.json"
            path.write_text('{"lines":["Hello"," ","Bye"]}', encoding="utf-8")
            handler = JsonHandler()
            handler.load(path)
            handler.select_fields(["lines"])
            self.assertEqual([unit.key for unit in handler.extract_units()], ["lines.0", "lines.2"])

    def test_json_dotted_key_does_not_collide_with_nested_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ambiguous.json"
            path.write_text('{"a.b":"one","a":{"b":"two"}}', encoding="utf-8")
            handler = JsonHandler()
            handler.load(path)
            handler.select_fields(["a.b", "b"])
            units = handler.extract_units()
            self.assertEqual(len({unit.key for unit in units}), 2)
            handler.apply_translations({unit.key: unit.text.upper() for unit in units})
            self.assertEqual(json.loads(handler.serialize()), {"a.b": "ONE", "a": {"b": "TWO"}})

    def test_json_invalid_root_and_json_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for content in ("{broken", '"string"'):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(Exception):
                    JsonHandler().load(path)

    def test_placeholder_roundtrip_and_order_failure(self) -> None:
        source = "Hello {player_name} {0} %s %d %1 ${variable} <color=red>x</color> [ID_001]"
        protected, originals = protect_placeholders(source)
        self.assertEqual(len(originals), 9)
        self.assertEqual(restore_placeholders(protected, originals), source)
        with self.assertRaisesRegex(ValueError, "Placeholder"):
            restore_placeholders(protected.replace("__PH_0__", "__PH_1__", 1), originals)


class TranslationTests(unittest.TestCase):
    def test_batch_csv_json_txt_and_selected_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("Hello {player_name}", encoding="utf-8")
            (input_dir / "b.csv").write_text("id,text\n1,Hello\n", encoding="utf-8")
            (input_dir / "c.json").write_text('{"id":"npc_1","dialogue":"Hello"}', encoding="utf-8")
            files = FileLoader(input_dir).list_supported_files()
            choices = iter(["2", "2"])
            prepared = _prepare_handlers(
                files, FileWriter(output_dir), 10_000,
                lambda _prompt: next(choices), lambda _message: None,
            )
            self.assertIsNotNone(prepared)
            handlers, samples = prepared  # type: ignore[misc]
            self.assertNotIn("npc_1", samples)
            self.assertNotIn("id", samples)
            llm = UppercaseLLM()
            summary = Translator(
                FileLoader(input_dir), llm, FileWriter(output_dir),
                handlers=handlers,  # type: ignore[arg-type]
            ).translate_all(lambda _message: None)
            self.assertEqual((summary.succeeded, summary.failed), (3, 0))
            self.assertEqual((output_dir / "a.txt").read_text(encoding="utf-8"), "HELLO {player_name}")
            with (output_dir / "b.csv").open(encoding="utf-8", newline="") as stream:
                self.assertEqual(list(csv.reader(stream))[1], ["1", "HELLO"])
            result = json.loads((output_dir / "c.json").read_text(encoding="utf-8"))
            self.assertEqual(result, {"id": "npc_1", "dialogue": "HELLO"})
            self.assertEqual(summary.by_format, {".txt": (1, 0), ".csv": (1, 0), ".json": (1, 0)})

    def test_failed_csv_does_not_save_and_next_json_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "input", root / "output"
            source.mkdir()
            (source / "a.csv").write_text("id,text\n1,FAIL\n", encoding="utf-8")
            (source / "b.json").write_text('{"text":"ok"}', encoding="utf-8")
            files = FileLoader(source).list_supported_files()
            choices = iter(["2", "1"])
            prepared = _prepare_handlers(
                files, FileWriter(target), 10_000,
                lambda _prompt: next(choices), lambda _message: None,
            )
            if prepared is None:
                self.fail("selection unexpectedly cancelled")
            handlers, _ = prepared
            summary = Translator(
                FileLoader(source), UppercaseLLM(fail_on="FAIL"), FileWriter(target),
                handlers=handlers,  # type: ignore[arg-type]
            ).translate_all(lambda _message: None)
            self.assertEqual((summary.failed, summary.succeeded), (1, 1))
            self.assertFalse((target / "a.csv").exists())
            self.assertFalse((target / "a.csv.tmp").exists())
            self.assertEqual(json.loads((target / "b.json").read_text(encoding="utf-8"))["text"], "OK")

    def test_bad_placeholder_response_fails_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "input", root / "output"
            source.mkdir()
            (source / "a.txt").write_text("Hello {player_name}", encoding="utf-8")
            summary = Translator(
                FileLoader(source), UppercaseLLM(corrupt_token=True), FileWriter(target)
            ).translate_all(lambda _message: None)
            self.assertEqual(summary.failed, 1)
            self.assertFalse((target / "a.txt").exists())

    def test_stop_between_csv_cells_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "input", root / "output"
            source.mkdir()
            path = source / "dialogue.csv"
            path.write_text("id,text\n1,Hello\n2,Bye\n", encoding="utf-8")
            handler = create_handler(path)
            handler.load(path)
            handler.select_fields(["text"])
            llm = UppercaseLLM()
            summary = Translator(
                FileLoader(source), llm, FileWriter(target),
                handlers={path: handler},
                stop_requested=lambda: True,
            ).translate_all(lambda _message: None)
            self.assertTrue(summary.was_stopped)
            self.assertEqual(len(llm.prompts), 1)
            self.assertFalse((target / "dialogue.csv").exists())
            self.assertFalse((target / "dialogue.csv.tmp").exists())

    def test_existing_json_output_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "input", root / "output"
            source.mkdir()
            target.mkdir()
            (source / "data.JSON").write_text('{"text":"Hello"}', encoding="utf-8")
            (target / "data.JSON").write_text('{"text":"existing"}', encoding="utf-8")
            llm = UppercaseLLM()
            summary = Translator(FileLoader(source), llm, FileWriter(target)).translate_all(lambda _message: None)
            self.assertEqual(summary.skipped, 1)
            self.assertEqual(llm.prompts, [])
            self.assertEqual(json.loads((target / "data.JSON").read_text(encoding="utf-8"))["text"], "existing")

    def test_txt_chunk_boundary_does_not_split_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "input", root / "output"
            source.mkdir()
            (source / "a.txt").write_text("Hello {player_name} there", encoding="utf-8")
            summary = Translator(
                FileLoader(source), UppercaseLLM(), FileWriter(target),
                chunk_max_chars=16,
            ).translate_all(lambda _message: None)
            self.assertEqual(summary.succeeded, 1)
            self.assertIn("{player_name}", (target / "a.txt").read_text(encoding="utf-8"))

    def test_prompt_builder_uses_only_selected_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text('{"id":"npc_secret","dialogue":"Hello"}', encoding="utf-8")
            prepared = _prepare_handlers([path], None, 10_000, lambda _prompt: "2", lambda _message: None)
            self.assertIsNotNone(prepared)
            _, sample = prepared  # type: ignore[misc]
            ai = FakePromptAI()
            PromptBuilder(ai).create_draft([path], "English", "Korean", "game", sample_text=sample)
            self.assertIn("Hello", ai.requests[0])
            self.assertNotIn("npc_secret", ai.requests[0])


if __name__ == "__main__":
    unittest.main()
