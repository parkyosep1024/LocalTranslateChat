"""TXT 샘플의 문자 구성을 이용해 원본 언어를 추정합니다."""

from pathlib import Path
import re

from script.translation.file_loader import FileLoader
from script.utils.exceptions import FileProcessingError


SUPPORTED_LANGUAGES = {
    "1": "Korean",
    "2": "Japanese",
    "3": "English",
    "4": "Chinese",
}
UNKNOWN_LANGUAGE = "Unknown"
DEFAULT_SAMPLE_CHARS_PER_FILE = 3_000
DEFAULT_TOTAL_SAMPLE_CHARS = 12_000

HANGUL_PATTERN = re.compile(r"[가-힣]")
KANA_PATTERN = re.compile(r"[\u3040-\u30ff]")
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_PATTERN = re.compile(r"[A-Za-z]")


class LanguageDetector:
    def __init__(
        self,
        chars_per_file: int = DEFAULT_SAMPLE_CHARS_PER_FILE,
        total_chars: int = DEFAULT_TOTAL_SAMPLE_CHARS,
    ) -> None:
        if chars_per_file <= 0 or total_chars <= 0:
            raise ValueError("언어 감지 샘플 크기는 0보다 커야 합니다.")
        self.chars_per_file = chars_per_file
        self.total_chars = total_chars

    def detect_files(self, files: list[Path]) -> str:
        samples: list[str] = []
        remaining = self.total_chars

        for path in files:
            if remaining <= 0:
                break
            try:
                text = FileLoader.read_text(path)
            except FileProcessingError:
                continue
            sample = text[: min(self.chars_per_file, remaining)]
            if sample:
                samples.append(sample)
                remaining -= len(sample)

        return self.detect_text("\n".join(samples))

    @staticmethod
    def detect_text(text: str) -> str:
        hangul = len(HANGUL_PATTERN.findall(text))
        kana = len(KANA_PATTERN.findall(text))
        cjk = len(CJK_PATTERN.findall(text))
        latin = len(LATIN_PATTERN.findall(text))
        script_total = hangul + kana + cjk + latin

        if script_total == 0:
            return UNKNOWN_LANGUAGE

        # 가나 문자는 일본어를 가장 명확하게 구분하는 신호입니다.
        if kana and kana / script_total >= 0.05:
            return "Japanese"

        scores = {
            "Korean": hangul,
            "Chinese": cjk,
            "English": latin,
        }
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_language, best_score = ranked[0]
        second_score = ranked[1][1]

        if best_score < 2:
            return UNKNOWN_LANGUAGE
        if second_score and best_score < second_score * 1.25:
            return UNKNOWN_LANGUAGE
        return best_language
