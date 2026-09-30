"""재사용 가능한 작은 위젯 모음입니다."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout


class Card(QFrame):
    """흰색 둥근 카드 컨테이너입니다."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        self.inner = layout

    def add_widget(self, widget) -> None:
        self.inner.addWidget(widget)


def section_header(title: str, right_text: str = "") -> QHBoxLayout:
    """섹션 제목 + 우측 보조 텍스트 행을 만듭니다."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    title_label = QLabel(title)
    title_label.setObjectName("SectionTitle")
    layout.addWidget(title_label)
    layout.addStretch(1)
    if right_text:
        right = QLabel(right_text)
        right.setObjectName("TinyMuted")
        layout.addWidget(right)
    return layout


def muted_label(text: str, object_name: str = "Muted") -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    return label


class StatusBadge(QLabel):
    """우측 상단 상태 뱃지(엔진 준비됨 등)입니다."""

    def __init__(self, text: str = "● 엔진 준비됨", parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("BadgeGreen")
        self.setAlignment(Qt.AlignCenter)
