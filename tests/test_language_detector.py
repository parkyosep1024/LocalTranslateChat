from pathlib import Path
import tempfile
import unittest

from script.translate_main import confirm_source_language
from script.translation.language_detector import LanguageDetector


class LanguageDetectorTests(unittest.TestCase):
    def test_detects_japanese(self) -> None:
        self.assertEqual(
            LanguageDetector.detect_text("これは日本語の文章です。"), "Japanese"
        )

    def test_detects_korean(self) -> None:
        self.assertEqual(
            LanguageDetector.detect_text("이것은 한국어 문장입니다."), "Korean"
        )

    def test_detects_english(self) -> None:
        self.assertEqual(
            LanguageDetector.detect_text("This is an English sentence."), "English"
        )

    def test_detects_chinese(self) -> None:
        self.assertEqual(
            LanguageDetector.detect_text("这是一个中文句子。"), "Chinese"
        )

    def test_balanced_mixed_text_is_unknown(self) -> None:
        self.assertEqual(LanguageDetector.detect_text("hello안녕하세요"), "Unknown")

    def test_empty_or_non_language_text_is_unknown(self) -> None:
        for text in ("", "1234 !!!"):
            with self.subTest(text=text):
                self.assertEqual(LanguageDetector.detect_text(text), "Unknown")

    def test_file_samples_are_limited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            for index in range(3):
                path = root / f"{index}.txt"
                path.write_text("한국어문장" * 20, encoding="utf-8")
                files.append(path)

            detector = LanguageDetector(chars_per_file=10, total_chars=20)

            self.assertEqual(detector.detect_files(files), "Korean")

    def test_user_can_replace_detected_language(self) -> None:
        inputs = iter(["2", "2"])

        selected = confirm_source_language(
            "Unknown",
            input_func=lambda _prompt: next(inputs),
            output_func=lambda _message: None,
        )

        self.assertEqual(selected, "Japanese")


if __name__ == "__main__":
    unittest.main()
