"""좌측 공통 Sidebar 위젯입니다."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from script.gui.widgets import muted_label


class Sidebar(QWidget):
    """3개 화면에서 공유하는 좌측 메뉴입니다."""

    page_requested = Signal(int)  # 0: 챗봇, 1: 파일 번역, 2: Prompt 설정

    CHAT = 0
    TRANSLATION = 1
    PROMPT = 2

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(184)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 14, 12, 12)
        root.setSpacing(8)

        brand = QLabel("✦  Local Translate")
        brand.setStyleSheet("font-weight: 800; font-size: 14px;")
        root.addWidget(brand)
        root.addWidget(muted_label("CHAT · OFFLINE", "TinyMuted"))

        self.btn_chat = self._menu_button("◻  AI 챗봇")
        self.btn_translation = self._menu_button("⧉  파일 번역")
        self.btn_prompt = self._menu_button("☰  Prompt 설정")
        for button in (self.btn_chat, self.btn_translation, self.btn_prompt):
            root.addWidget(button)

        self.btn_chat.clicked.connect(lambda: self.page_requested.emit(self.CHAT))
        self.btn_translation.clicked.connect(lambda: self.page_requested.emit(self.TRANSLATION))
        self.btn_prompt.clicked.connect(lambda: self.page_requested.emit(self.PROMPT))

        root.addStretch(1)

        self.model_status = QLabel("● 로컬 모델 연결됨")
        self.model_status.setObjectName("BadgeGreen")
        root.addWidget(self.model_status)

        self.set_active(self.TRANSLATION)

    def _menu_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("SidebarButton")
        button.setCheckable(True)
        button.setAutoExclusive(True)
        return button

    def set_active(self, index: int) -> None:
        buttons = (self.btn_chat, self.btn_translation, self.btn_prompt)
        for i, button in enumerate(buttons):
            button.setChecked(i == index)
            button.setProperty("active", i == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def set_model_status(self, connected: bool, text: str | None = None) -> None:
        """향후 Ollama 연결 상태를 표시하기 위한 메서드입니다(backend 미연결)."""
        if text is None:
            text = "● 로컬 모델 연결됨" if connected else "○ 로컬 모델 끊김"
        self.model_status.setText(text)
