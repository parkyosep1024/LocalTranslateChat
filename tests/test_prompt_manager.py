import json
from pathlib import Path
import tempfile
import unittest

from script.translation.prompt_manager import PromptManager
from script.utils.exceptions import PromptPresetError


class PromptManagerTests(unittest.TestCase):
    def make_manager(self, root: Path, today: list[str] | None = None) -> PromptManager:
        current = today or ["2026-09-15"]
        return PromptManager(root / "presets", today_provider=lambda: current[0])

    def test_saves_and_loads_json_with_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            preset = manager.create_preset(
                "JRPG 일본어 대사",
                "Japanese",
                "Korean",
                "game_dialogue",
                "말투를 유지하여 번역한다.",
            )

            path = manager.save(preset)
            loaded = manager.load(path)
            raw = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(loaded, preset)
            self.assertEqual(
                set(raw),
                {
                    "name",
                    "source_language",
                    "target_language",
                    "document_type",
                    "prompt",
                    "created_by",
                    "created_at",
                    "updated_at",
                },
            )
            self.assertNotIn("/", path.name)

    def test_lists_presets_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            for name in ("Zulu", "Alpha"):
                manager.save(
                    manager.create_preset(
                        name, "English", "Korean", "general", f"{name} prompt"
                    )
                )

            self.assertEqual(
                [preset.name for preset in manager.list_presets()], ["Alpha", "Zulu"]
            )

    def test_empty_name_or_prompt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            for name, prompt in (("", "prompt"), ("name", "")):
                with self.subTest(name=name, prompt=prompt):
                    with self.assertRaises(PromptPresetError):
                        manager.create_preset(
                            name, "Japanese", "Korean", "general", prompt
                        )

    def test_duplicate_name_requires_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            preset = manager.create_preset(
                "Same", "English", "Korean", "general", "first"
            )
            manager.save(preset)

            with self.assertRaisesRegex(PromptPresetError, "이미 있습니다"):
                manager.save(
                    manager.create_preset(
                        "Same", "English", "Korean", "general", "second"
                    )
                )

    def test_overwrite_preserves_created_at_and_updates_updated_at(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            today = ["2026-09-15"]
            manager = self.make_manager(Path(directory), today)
            manager.save(
                manager.create_preset(
                    "Update", "English", "Korean", "general", "first"
                )
            )
            today[0] = "2026-09-16"

            path = manager.save(
                manager.create_preset(
                    "Update", "English", "Korean", "general", "second"
                ),
                overwrite=True,
            )
            updated = manager.load(path)

            self.assertEqual(updated.created_at, "2026-09-15")
            self.assertEqual(updated.updated_at, "2026-09-16")
            self.assertEqual(updated.prompt, "second")

    def test_invalid_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            manager.ensure_presets_dir()
            invalid = manager.presets_dir / "invalid.json"
            invalid.write_text("not-json", encoding="utf-8")

            with self.assertRaisesRegex(PromptPresetError, "JSON"):
                manager.load(invalid)


if __name__ == "__main__":
    unittest.main()
