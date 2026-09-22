import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from script.translation.file_fingerprint import file_fingerprint
from script.translation.file_loader import FileLoader
from script.translation.file_writer import FileWriter
from script.translation.prompt_manager import PromptManager
from script.translation.prompt_usage import PromptUsageRegistry
from script.translation.translator import Translator
from script.utils.exceptions import PromptPresetError


class PromptLibraryTests(unittest.TestCase):
    def make_manager(self, root: Path, today: list[str] | None = None) -> PromptManager:
        current = today or ["2026-09-22"]
        return PromptManager(root / "presets", today_provider=lambda: current[0])

    def make_preset(
        self,
        manager: PromptManager,
        name: str,
        source: str = "Japanese",
        target: str = "Korean",
        document_type: str = "game_dialogue",
        prompt: str = "Translate naturally.",
    ):
        return manager.create_preset(name, source, target, document_type, prompt)

    def test_hierarchical_path_and_filtered_lists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            jp_game = self.make_preset(manager, "Natural Game")
            jp_novel = self.make_preset(manager, "Natural Novel", document_type="novel")
            en_game = self.make_preset(manager, "Natural Game", source="English")
            paths = [manager.save(item) for item in (jp_game, jp_novel, en_game)]
            self.assertEqual(
                paths[0].relative_to(manager.presets_dir).parts,
                ("Japanese", "Korean", "game_dialogue", "natural_game.json"),
            )
            self.assertEqual(len(manager.list_by_source_language("Japanese")), 2)
            self.assertEqual(len(manager.list_by_language_pair("Japanese", "Korean")), 2)
            self.assertEqual(
                [item.name for item in manager.list_by_document_type("novel")],
                ["Natural Novel"],
            )
            self.assertEqual(manager.list_languages(), ["English", "Japanese", "Korean"])
            self.assertEqual(
                manager.find_by_name(
                    "Natural Game", "English", "Korean", "game_dialogue"
                ),
                en_game,
            )

    def test_same_name_allowed_in_different_scopes_but_not_same_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            manager.save(self.make_preset(manager, "Natural"))
            manager.save(self.make_preset(manager, "Natural", source="English"))
            with self.assertRaisesRegex(PromptPresetError, "이미 있습니다"):
                manager.save(self.make_preset(manager, "Natural"))

    def test_legacy_flat_and_hierarchical_presets_are_both_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            manager.ensure_presets_dir()
            legacy = self.make_preset(manager, "Legacy")
            (manager.presets_dir / "legacy.json").write_text(
                json.dumps(legacy.__dict__), encoding="utf-8"
            )
            manager.save(self.make_preset(manager, "Modern", source="English"))
            self.assertEqual(
                {item.name for item in manager.list_presets()}, {"Legacy", "Modern"}
            )

    def test_matching_priority_and_exact_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            for preset in (
                self.make_preset(manager, "Exact"),
                self.make_preset(manager, "Pair", document_type="novel"),
                self.make_preset(manager, "Source", target="English", document_type="novel"),
                self.make_preset(manager, "Other", source="English"),
            ):
                manager.save(preset)
            matches = manager.find_matching_presets(
                "Japanese", "Korean", "game_dialogue"
            )
            self.assertEqual([item.name for item in matches], ["Exact", "Pair", "Source"])
            self.assertEqual(
                [item.name for item in manager.find_matching_presets(
                    "Japanese", "Korean", "game_dialogue", exact=True
                )],
                ["Exact"],
            )

    def test_update_moves_safely_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            today = ["2026-09-22"]
            manager = self.make_manager(Path(directory), today)
            old_path = manager.save(self.make_preset(manager, "Old"))
            today[0] = "2026-09-23"
            updated, new_path = manager.update(
                old_path,
                name="New",
                source_language="English",
                document_type="novel",
                prompt="Updated prompt",
            )
            self.assertFalse(old_path.exists())
            self.assertTrue(new_path.exists())
            self.assertEqual(updated.created_at, "2026-09-22")
            self.assertEqual(updated.updated_at, "2026-09-23")
            self.assertEqual(manager.load(new_path).prompt, "Updated prompt")
            self.assertTrue(manager.delete(new_path))
            self.assertFalse(new_path.exists())
            self.assertFalse(manager.delete(new_path))

    def test_update_collision_preserves_both_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            first = manager.save(self.make_preset(manager, "First"))
            second = manager.save(self.make_preset(manager, "Second"))
            with self.assertRaisesRegex(PromptPresetError, "이미 있습니다"):
                manager.update(first, name="Second")
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_failed_atomic_update_preserves_existing_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            path = manager.save(self.make_preset(manager, "Stable", prompt="old"))
            with patch.object(Path, "replace", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(PromptPresetError, "저장"):
                    manager.update(path, prompt="new")
            self.assertEqual(manager.load(path).prompt, "old")
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_import_and_export_txt_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            source_txt = root / "external.txt"
            source_txt.write_text("Imported prompt", encoding="utf-8")
            imported = manager.import_txt(
                source_txt, "Imported", "Japanese", "Korean", "novel"
            )
            txt_export = manager.export_txt(imported, root / "export" / "prompt.txt")
            json_export = manager.export_json(imported, root / "export" / "prompt.json")
            self.assertEqual(txt_export.read_text(encoding="utf-8"), "Imported prompt")
            self.assertEqual(
                json.loads(json_export.read_text(encoding="utf-8"))["name"], "Imported"
            )

            other = self.make_manager(root / "other")
            other_path = other.import_json(json_export)
            self.assertEqual(other.load(other_path).prompt, "Imported prompt")
            self.assertFalse((root / "export" / "prompt.json.tmp").exists())

    def test_invalid_json_import_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad = root / "bad.json"
            bad.write_text('{"prompt":"missing metadata"}', encoding="utf-8")
            with self.assertRaises(PromptPresetError):
                self.make_manager(root).import_json(bad)
            wrong_extension = root / "preset.txt"
            wrong_extension.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(PromptPresetError, "JSON"):
                self.make_manager(root).import_json(wrong_extension)


class FingerprintTests(unittest.TestCase):
    def test_content_not_filename_determines_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, renamed, changed = root / "a.txt", root / "b.txt", root / "c.txt"
            first.write_bytes(b"same content")
            renamed.write_bytes(b"same content")
            changed.write_bytes(b"different")
            self.assertEqual(file_fingerprint(first), file_fingerprint(renamed))
            self.assertNotEqual(file_fingerprint(first), file_fingerprint(changed))
            self.assertEqual(len(file_fingerprint(first)), 64)


class PromptUsageTests(unittest.TestCase):
    def setup_library(self, root: Path):
        manager = PromptManager(root / "presets")
        exact = manager.create_preset(
            "Game", "Japanese", "Korean", "game_dialogue", "GAME PROMPT"
        )
        novel = manager.create_preset(
            "Novel", "Japanese", "Korean", "novel", "NOVEL PROMPT"
        )
        exact_path = manager.save(exact)
        novel_path = manager.save(novel)
        return manager, exact, novel, exact_path, novel_path

    def test_record_load_exact_match_and_atomic_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, exact, _, exact_path, _ = self.setup_library(root)
            file_path = root / "dialogue.csv"
            file_path.write_text("id,text\n1,Hello", encoding="utf-8")
            registry = PromptUsageRegistry(
                root / "setting" / "prompt_usage.json",
                today_provider=lambda: "2026-09-22",
            )
            registry.record_success(
                file_path, manager, exact_path, "Japanese", "Korean",
                "game_dialogue", 'csv:["id","text"]',
            )
            record = registry.get_record(file_path)
            self.assertEqual(record["prompt_name"], "Game")
            self.assertEqual(record["schema_fingerprint"], 'csv:["id","text"]')
            self.assertNotIn("GAME PROMPT", json.dumps(record))
            self.assertEqual(
                list(registry.list_records()), [file_fingerprint(file_path)]
            )
            self.assertEqual(registry.get_previous_prompt(file_path, manager), exact)
            self.assertFalse((registry.path.parent / "prompt_usage.json.tmp").exists())

            renamed = root / "renamed.csv"
            renamed.write_bytes(file_path.read_bytes())
            self.assertEqual(registry.get_previous_prompt(renamed, manager), exact)

    def test_failed_usage_replace_preserves_previous_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, _, _, exact_path, _ = self.setup_library(root)
            first, second = root / "first.txt", root / "second.txt"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            registry = PromptUsageRegistry(root / "usage.json")
            registry.record_success(
                first, manager, exact_path, "Japanese", "Korean", "game_dialogue"
            )
            original = registry.path.read_bytes()
            with patch.object(Path, "replace", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(Exception, "Usage Registry"):
                    registry.record_success(
                        second, manager, exact_path,
                        "Japanese", "Korean", "game_dialogue",
                    )
            self.assertEqual(registry.path.read_bytes(), original)
            self.assertFalse(registry.path.with_name("usage.json.tmp").exists())

    def test_deleted_prompt_reference_falls_back_to_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, exact, novel, exact_path, _ = self.setup_library(root)
            file_path = root / "data.txt"
            file_path.write_text("hello", encoding="utf-8")
            registry = PromptUsageRegistry(root / "usage.json")
            registry.record_success(
                file_path, manager, exact_path, "Japanese", "Korean", "game_dialogue"
            )
            manager.delete(exact_path)
            self.assertIsNone(registry.get_previous_prompt(file_path, manager))
            recommendations = registry.recommend(
                file_path, manager, "Japanese", "Korean", "game_dialogue"
            )
            self.assertEqual([item.preset for item in recommendations], [novel])
            self.assertFalse(recommendations[0].exact_file)

    def test_corrupt_or_invalid_usage_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, exact, _, _, _ = self.setup_library(root)
            file_path = root / "data.txt"
            file_path.write_text("hello", encoding="utf-8")
            usage_path = root / "usage.json"
            for content in ("{broken", "[]", '{"files":[]}'):
                usage_path.write_text(content, encoding="utf-8")
                registry = PromptUsageRegistry(usage_path)
                self.assertIsNone(registry.get_record(file_path))
                self.assertEqual(
                    registry.recommend(
                        file_path, manager, "Japanese", "Korean", "game_dialogue"
                    )[0].preset,
                    exact,
                )

    def test_schema_extension_and_txt_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, exact, novel, exact_path, novel_path = self.setup_library(root)
            registry = PromptUsageRegistry(root / "usage.json")
            csv_a = root / "a.csv"
            csv_b = root / "b.csv"
            txt = root / "a.txt"
            for path, content in ((csv_a, "a"), (csv_b, "b"), (txt, "c")):
                path.write_text(content, encoding="utf-8")
            registry.record_success(
                csv_a, manager, exact_path, "Japanese", "Korean",
                "game_dialogue", "csv:same",
            )
            registry.record_success(
                txt, manager, novel_path, "Japanese", "Korean", "novel"
            )
            csv_recs = registry.recommend(
                csv_b, manager, "Japanese", "Korean", "game_dialogue", "csv:same"
            )
            self.assertEqual(csv_recs[0].preset, exact)
            self.assertIn("Schema", csv_recs[0].reason)
            txt_other = root / "other.txt"
            txt_other.write_text("other", encoding="utf-8")
            txt_recs = registry.recommend(
                txt_other, manager, "Japanese", "Korean", "novel"
            )
            self.assertEqual(txt_recs[0].preset, novel)

    def test_no_candidate_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_path = root / "a.txt"
            file_path.write_text("a", encoding="utf-8")
            self.assertEqual(
                PromptUsageRegistry(root / "usage.json").recommend(
                    file_path, PromptManager(root / "presets"),
                    "Chinese", "English", "unknown",
                ),
                [],
            )

    def test_usage_callback_runs_only_after_successful_file_save(self) -> None:
        class FailLLM:
            def generate(self, prompt: str) -> str:
                raise RuntimeError("failure")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")
            called: list[Path] = []
            summary = Translator(
                FileLoader(input_dir), FailLLM(), FileWriter(output_dir),
                on_file_succeeded=called.append,
            ).translate_all(lambda _line: None)
            self.assertEqual(summary.failed, 1)
            self.assertEqual(called, [])

            class EchoLLM:
                def generate(self, prompt: str) -> str:
                    return prompt.split("[원문]\n", 1)[1].upper()

            success_output = root / "success-output"
            success_called: list[Path] = []
            success = Translator(
                FileLoader(input_dir), EchoLLM(), FileWriter(success_output),
                on_file_succeeded=success_called.append,
            ).translate_all(lambda _line: None)
            self.assertEqual(success.succeeded, 1)
            self.assertEqual(success_called, [input_dir / "a.txt"])
            self.assertTrue((success_output / "a.txt").exists())


if __name__ == "__main__":
    unittest.main()
