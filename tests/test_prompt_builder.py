from pathlib import Path
import tempfile
import unittest

from script.translation.prompt_builder import PromptBuilder
from script.utils.exceptions import InvalidResponseError


class FakePromptAI:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.requests: list[str] = []

    def generate(self, prompt: str) -> str:
        self.requests.append(prompt)
        return next(self.responses)


class PromptBuilderTests(unittest.TestCase):
    def test_samples_multiple_files_with_per_file_and_total_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            for name in ("a.txt", "b.txt", "c.txt"):
                path = root / name
                path.write_text(name[0] * 50, encoding="utf-8")
                files.append(path)
            builder = PromptBuilder(
                FakePromptAI(["draft"]), chars_per_file=10, total_chars=70
            )

            samples = builder.collect_samples(files)

            self.assertLessEqual(len(samples), 70)
            self.assertIn("[파일: a.txt]", samples)
            self.assertIn("[파일: b.txt]", samples)
            self.assertNotIn("a" * 11, samples)

    def test_create_draft_sends_languages_type_and_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dialogue.txt"
            path.write_text("こんにちは", encoding="utf-8")
            provider = FakePromptAI([" 생성된 Prompt "])
            builder = PromptBuilder(provider)

            draft = builder.create_draft(
                [path], "Japanese", "Korean", "game_dialogue"
            )

            self.assertEqual(draft, "생성된 Prompt")
            request = provider.requests[0]
            self.assertIn("Japanese", request)
            self.assertIn("Korean", request)
            self.assertIn("game_dialogue", request)
            self.assertIn("こんにちは", request)
            self.assertIn("직접 번역하지 말고", request)

    def test_revision_sends_current_prompt_and_user_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dialogue.txt"
            path.write_text("sample", encoding="utf-8")
            provider = FakePromptAI(["완성된 새 Prompt"])
            builder = PromptBuilder(provider)

            revised = builder.revise_draft(
                "기존 Prompt",
                "고유명사는 유지해 줘",
                [path],
                "English",
                "Korean",
                "game_dialogue",
            )

            self.assertEqual(revised, "완성된 새 Prompt")
            self.assertIn("기존 Prompt", provider.requests[0])
            self.assertIn("고유명사는 유지해 줘", provider.requests[0])
            self.assertIn("완성된 Prompt 전체", provider.requests[0])

    def test_empty_ai_draft_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("sample", encoding="utf-8")
            builder = PromptBuilder(FakePromptAI(["  "]))

            with self.assertRaisesRegex(InvalidResponseError, "빈 Draft"):
                builder.create_draft([path], "English", "Korean", "general")


if __name__ == "__main__":
    unittest.main()
