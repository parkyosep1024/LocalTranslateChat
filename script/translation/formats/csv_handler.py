"""선택한 CSV 컬럼의 문자열 셀만 번역합니다."""

import csv
import io
from pathlib import Path

from script.translation.formats.base import TranslationUnit
from script.utils.exceptions import FileProcessingError


class CsvHandler:
    def __init__(self) -> None:
        self.header: list[str] = []
        self.rows: list[list[str]] = []
        self.fields: list[str] = []
        self.dialect: csv.Dialect = csv.excel
        self.newline = "\r\n"
        self.bom = False
        self.locations: dict[str, tuple[int, int]] = {}

    def load(self, path: Path) -> None:
        try:
            raw = path.read_bytes()
            self.bom = raw.startswith(b"\xef\xbb\xbf")
            content = raw.decode("utf-8-sig")
            if not content.strip():
                raise FileProcessingError("빈 CSV 파일입니다.")
            self.newline = "\r\n" if "\r\n" in content else "\n"
            sample = content[:8192]
            try:
                self.dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                self.dialect = csv.excel
            reader = csv.reader(io.StringIO(content, newline=""), dialect=self.dialect, strict=True)
            all_rows = list(reader)
        except (UnicodeDecodeError, OSError, csv.Error) as error:
            raise FileProcessingError(f"CSV 파일을 읽거나 파싱할 수 없습니다: {error}") from error
        if not all_rows or not all_rows[0] or not any(all_rows[0]):
            raise FileProcessingError("CSV Header가 없습니다.")
        self.header = all_rows[0]
        if len(set(self.header)) != len(self.header):
            raise FileProcessingError("CSV Header에 중복된 컬럼이 있습니다.")
        self.rows = all_rows[1:]
        if any(len(row) != len(self.header) for row in self.rows):
            raise FileProcessingError("CSV 행의 컬럼 개수가 Header와 다릅니다.")
        self.fields = []
        self.locations = {}

    def available_fields(self) -> list[str]:
        return self.header.copy()

    def select_fields(self, fields: list[str]) -> None:
        if not fields or any(field not in self.header for field in fields):
            raise ValueError("선택한 CSV 컬럼이 없거나 유효하지 않습니다.")
        self.fields = list(dict.fromkeys(fields))

    def extract_units(self) -> list[TranslationUnit]:
        self.locations = {}
        units: list[TranslationUnit] = []
        for row_index, row in enumerate(self.rows):
            for field in self.fields:
                column_index = self.header.index(field)
                value = row[column_index]
                if not value.strip():
                    continue
                key = f"row_{row_index}:{field}"
                self.locations[key] = (row_index, column_index)
                units.append(TranslationUnit(key, value))
        return units

    def apply_translations(self, translations: dict[str, str]) -> None:
        for key, (row_index, column_index) in self.locations.items():
            if key not in translations:
                raise ValueError(f"번역 결과가 없습니다: {key}")
            self.rows[row_index][column_index] = translations[key]

    def serialize(self) -> str:
        buffer = io.StringIO(newline="")
        writer = csv.writer(
            buffer,
            delimiter=self.dialect.delimiter,
            quotechar=self.dialect.quotechar,
            doublequote=self.dialect.doublequote,
            escapechar=self.dialect.escapechar,
            quoting=self.dialect.quoting,
            lineterminator=self.newline,
        )
        writer.writerow(self.header)
        writer.writerows(self.rows)
        return ("\ufeff" if self.bom else "") + buffer.getvalue()
