"""파일명과 무관한 SHA-256 파일 내용 Fingerprint."""

import hashlib
from pathlib import Path

from script.utils.exceptions import FileProcessingError


def file_fingerprint(path: Path, block_size: int = 64 * 1024) -> str:
    if block_size <= 0:
        raise ValueError("block_size는 0보다 커야 합니다.")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(block_size)
                if not block:
                    break
                digest.update(block)
    except OSError as error:
        raise FileProcessingError(f"파일 Fingerprint를 계산할 수 없습니다: {error}") from error
    return digest.hexdigest()
