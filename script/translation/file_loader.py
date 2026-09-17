"""입력 폴더의 TXT 파일을 탐색하고 읽습니다."""

from pathlib import Path

from script.utils.exceptions import FileProcessingError


class FileLoader:
    def __init__(self, input_dir: Path) -> None:
        self.input_dir = input_dir

    def ensure_input_dir(self) -> None:
        self.input_dir.mkdir(parents=True, exist_ok=True)

    def list_txt_files(self) -> list[Path]:
        self.ensure_input_dir()
        files = [
            path
            for path in self.input_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".txt"
        ]
        return sorted(files, key=lambda path: path.name.casefold())

    @staticmethod
    def read_text(path: Path) -> str:
        try:
            with path.open("r", encoding="utf-8", newline="") as file:
                return file.read()
        except UnicodeDecodeError as error:
            raise FileProcessingError(
                "파일을 UTF-8 형식으로 읽을 수 없습니다."
            ) from error
        except OSError as error:
            raise FileProcessingError(f"파일을 읽을 수 없습니다: {error}") from error
