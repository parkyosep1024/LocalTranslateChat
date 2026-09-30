"""AI 챗봇 화면입니다. 첨부 이미지 2번을 기준으로 구현합니다."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ChatPage(QWidget):
    """좌측 대화 목록 + 우측 대화/컴포저 화면입니다."""

    send_requested = Signal(str)
    new_chat_requested = Signal()
    clear_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        root.addWidget(self._build_history_column(), 0)
        root.addWidget(self._build_chat_column(), 1)

        # 디자인 확인용 더미 대화
        self.add_user_message("일본어 게임 대사 “やっと会えたね、ずっと待ってたよ。”를 한국어로 자연스럽게 번역해줘. 오랜 친구를 다시 만난 따뜻한 장면이야.")
        self.add_ai_message(
            "자연스러운 번역은 “드디어 만났네. 계속 기다리고 있었어.”입니다. "
            "친한 사이의 따뜻한 재회 장면이라 문장을 짧게 끊어 감정을 살리는 편이 좋습니다.",
            tip="좀 더 반가움을 강조하려면 “드디어 만났구나! 정말 오래 기다렸어.”로 조정할 수 있어요.",
        )
        self.add_user_message("캐릭터가 차분한 성격이라 느낌표는 빼고, 조금 더 애틋하게 바꿔줘.")
        self.add_ai_message("“드디어 만났네. 계속… 기다리고 있었어.”가 잘 어울립니다. 말줄임표가 차분한 호흡과 기다림의 시간을 함께 전달해 줍니다.")

    # ---------- 좌측 ----------
    def _build_history_column(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)

        new_btn = QPushButton("+  새 대화")
        new_btn.setObjectName("PrimaryButton")
        new_btn.clicked.connect(self.new_chat_requested.emit)
        layout.addWidget(new_btn)

        search_row = QHBoxLayout()
        label = QLabel("최근 대화")
        label.setObjectName("Muted")
        search_row.addWidget(label)
        search_row.addStretch(1)
        search_btn = QPushButton("⌕")
        search_btn.setObjectName("GhostIconButton")
        search_row.addWidget(search_btn)
        layout.addLayout(search_row)

        self.history_list = QListWidget()
        self.history_list.setStyleSheet("QListWidget { border: none; }")
        for title, meta, selected in [
            ("게임 대사 자연스럽게 다듬기", "오늘 · 10:42", True),
            ("일본어 경어 뉘앙스 질문", "어제 · 18:21", False),
            ("CSV 용어집 정리", "9월 27일", False),
            ("아이템 설명 번역 검토", "9월 25일", False),
        ]:
            item = QListWidgetItem(f"{title}\n{meta}")
            self.history_list.addItem(item)
            if selected:
                self.history_list.setCurrentItem(item)
        layout.addWidget(self.history_list, 1)

        model_box = QFrame()
        model_box.setObjectName("StatusBox")
        model_layout = QVBoxLayout(model_box)
        model_layout.setContentsMargins(10, 8, 10, 8)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("현재 모델"))
        row1.addStretch(1)
        value1 = QLabel("Qwen2.5 14B")
        value1.setStyleSheet("font-weight: 700;")
        row1.addWidget(value1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("컨텍스트"))
        row2.addStretch(1)
        row2.addWidget(QLabel("3,240 / 16K"))
        model_layout.addLayout(row1)
        model_layout.addLayout(row2)
        layout.addWidget(model_box)

        panel.setFixedWidth(228)
        return panel

    # ---------- 우측 ----------
    def _build_chat_column(self) -> QWidget:
        container = QVBoxLayout()
        container.setSpacing(8)
        host = QWidget()
        host.setLayout(container)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("게임 대사 자연스럽게 다듬기")
        title.setStyleSheet("font-size: 17px; font-weight: 800;")
        sub = QLabel("● 번역 도우미 · 로컬 모델")
        sub.setObjectName("TinyMuted")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        header.addLayout(title_col, 1)
        export_btn = QPushButton("⤓  대화 내보내기")
        export_btn.setObjectName("SecondaryButton")
        export_btn.clicked.connect(self.export_requested.emit)
        clear_btn = QPushButton("🗑  지우기")
        clear_btn.setObjectName("SecondaryButton")
        clear_btn.clicked.connect(self.clear_messages)
        clear_btn.clicked.connect(self.clear_requested.emit)
        header.addWidget(export_btn)
        header.addWidget(clear_btn)
        container.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.messages_host = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_host)
        self.messages_layout.setContentsMargins(12, 8, 12, 8)
        self.messages_layout.setSpacing(10)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(self.messages_host)
        container.addWidget(self.scroll, 1)

        composer = QFrame()
        composer.setObjectName("Card")
        composer_layout = QVBoxLayout(composer)
        self.input = QTextEdit()
        self.input.setPlaceholderText("번역할 문장이나 궁금한 점을 입력하세요…")
        self.input.setFixedHeight(64)
        self.input.setStyleSheet("QTextEdit { border: none; }")
        composer_layout.addWidget(self.input)
        bottom = QHBoxLayout()
        attach = QPushButton("📎  파일 첨부")
        attach.setObjectName("GhostIconButton")
        hint = QLabel("Shift + Enter로 줄바꿈")
        hint.setObjectName("TinyMuted")
        send = QPushButton("➤  전송")
        send.setObjectName("PrimaryButton")
        send.clicked.connect(self._emit_send)
        bottom.addWidget(attach)
        bottom.addWidget(hint)
        bottom.addStretch(1)
        bottom.addWidget(send)
        composer_layout.addLayout(bottom)
        self.send_button = send
        container.addWidget(composer)
        return host

    # ---------- 메시지 ----------
    def _bubble(self, text: str, is_user: bool, tip: str = "") -> QWidget:
        row = QHBoxLayout()
        bubble = QFrame()
        bubble.setObjectName("Card")
        if is_user:
            bubble.setStyleSheet(
                "QFrame#Card { background: #2F6FED; color: white; border: none; "
                "border-radius: 12px; }"
            )
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(12, 10, 12, 10)
        content = QLabel(text)
        content.setWordWrap(True)
        if is_user:
            content.setStyleSheet("color: white;")
        layout.addWidget(content)
        if tip and not is_user:
            tip_box = QFrame()
            tip_box.setObjectName("StatusBox")
            tip_layout = QHBoxLayout(tip_box)
            tip_layout.setContentsMargins(8, 6, 8, 6)
            tip_label = QLabel(f"ⓘ  {tip}")
            tip_label.setWordWrap(True)
            tip_label.setObjectName("Muted")
            tip_layout.addWidget(tip_label)
            layout.addWidget(tip_box)

        if is_user:
            row.addStretch(1)
            row.addWidget(bubble, 4)
        else:
            icon = QLabel("✦")
            icon.setFixedSize(28, 28)
            icon.setAlignment(Qt.AlignCenter)
            icon.setStyleSheet(
                "background: #2F6FED; color: white; border-radius: 6px; font-weight: 800;"
            )
            row.addWidget(icon, 0, Qt.AlignTop)
            row.addWidget(bubble, 4)
            row.addStretch(1)
        host = QWidget()
        host.setLayout(row)
        host.setStyleSheet("background: transparent;")
        return host

    def add_user_message(self, text: str) -> None:
        self.messages_layout.insertWidget(
            self.messages_layout.count() - 1, self._bubble(text, True)
        )
        self._scroll_to_bottom()

    def add_ai_message(self, text: str, tip: str = "") -> None:
        self.messages_layout.insertWidget(
            self.messages_layout.count() - 1, self._bubble(text, False, tip)
        )
        self._scroll_to_bottom()

    def clear_messages(self) -> None:
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def set_model_info(self, model: str, context: str) -> None:
        """향후 ChatEngine/설정 연결 지점입니다(현재는 더미 표시 유지)."""
        _ = (model, context)

    def _emit_send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.add_user_message(text)
        self.send_requested.emit(text)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
