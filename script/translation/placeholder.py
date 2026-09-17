"""게임 대사 Placeholder를 임시 토큰으로 보호하고 순서를 검증합니다."""

import re

PLACEHOLDER_PATTERN = re.compile(
    r"\$\{[^{}\r\n]+\}|\{[^{}\r\n]+\}|%(?:\d+|[sd])|</?color(?:=[^>\r\n]+)?>|\[ID_[^\]\r\n]+\]"
)
TOKEN_PATTERN = re.compile(r"__PH_\d+__")


def protect_placeholders(text: str) -> tuple[str, list[str]]:
    placeholders: list[str] = []

    def replace(match: re.Match[str]) -> str:
        token = f"__PH_{len(placeholders)}__"
        if token in text:
            raise ValueError("원문에 Placeholder 보호 토큰이 이미 있습니다.")
        placeholders.append(match.group())
        return token

    return PLACEHOLDER_PATTERN.sub(replace, text), placeholders


def restore_placeholders(text: str, placeholders: list[str]) -> str:
    expected = [f"__PH_{index}__" for index in range(len(placeholders))]
    if TOKEN_PATTERN.findall(text) != expected:
        raise ValueError("Placeholder 순서 또는 내용이 변경되어 복원할 수 없습니다.")
    for token, original in zip(expected, placeholders):
        text = text.replace(token, original, 1)
    return text
