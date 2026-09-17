"""번역 결과를 원본과 분리된 출력 폴더에 안전하게 저장합니다."""

from pathlib import Path

from script.utils.exceptions import FileProcessingError


class FileWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def ensure_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def output_path_for(self, input_path: Path) -> Path:
        return self.output_dir / input_path.name

    def exists_for(self, input_path: Path) -> bool:
        return self.output_path_for(input_path).exists()

    def write(self, input_path: Path, content: str) -> Path:
        self.ensure_output_dir()
        output_path = self.output_path_for(input_path)
        temporary_path = output_path.with_name(f"{output_path.name}.tmp")

        try:
            with temporary_path.open("w", encoding="utf-8", newline="") as file:
                file.write(content)
            temporary_path.replace(output_path)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise FileProcessingError(f"번역 결과를 저장할 수 없습니다: {error}") from error

        return output_path
