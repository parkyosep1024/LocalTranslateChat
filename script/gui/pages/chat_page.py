"""AI 챗봇 화면입니다. 초기 상태는 빈 상태이며 Gemini ChatEngine에 연결됩니다."""

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


class ChatInput(QTextEdit):
    """Enter 전송 / Shift+Enter 줄바꿈 입력창입니다."""

    send_pressed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._composing = False

    def inputMethodEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # 한글 IME 조합 중 Enter는 조합 확정에 쓰이므로 전송하지 않습니다.
        try:
            self._composing = bool(event.preeditString())
        except Exception:
            self._composing = False
        super().inputMethodEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not self._composing:
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
                return
            event.accept()
            if self.toPlainText().strip():
                self.send_pressed.emit()
            return
        super().keyPressEvent(event)


class ChatPage(QWidget):
    """좌측 대화 목록 + 우측 대화/컴포저 화면입니다."""

    send_requested = Signal(str)
    new_chat_requested = Signal()
    clear_requested = Signal()
    export_requested = Signal()
    conversation_selected = Signal(str)  # session id (Qt.UserRole 기반)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        root.addWidget(self._build_history_column(), 0)
        root.addWidget(self._build_chat_column(), 1)
        self._refresh_empty_state()

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
        self.history_list.itemClicked.connect(self._on_history_clicked)
        layout.addWidget(self.history_list, 1)
        self.empty_history_label = QLabel("저장된 대화가 없습니다.")
        self.empty_history_label.setObjectName("TinyMuted")
        self.empty_history_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_history_label)

        model_box = QFrame()
        model_box.setObjectName("StatusBox")
        model_layout = QVBoxLayout(model_box)
        model_layout.setContentsMargins(10, 8, 10, 8)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("현재 모델"))
        row1.addStretch(1)
        self.model_value = QLabel("-")
        self.model_value.setStyleSheet("font-weight: 700;")
        row1.addWidget(self.model_value)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("컨텍스트"))
        row2.addStretch(1)
        self.context_value = QLabel("-")
        row2.addWidget(self.context_value)
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
        self.title_label = QLabel("새 대화")
        self.title_label.setStyleSheet("font-size: 17px; font-weight: 800;")
        self.subtitle_label = QLabel("연결 확인 중")
        self.subtitle_label.setObjectName("TinyMuted")
        title_col.addWidget(self.title_label)
        title_col.addWidget(self.subtitle_label)
        header.addLayout(title_col, 1)
        export_btn = QPushButton("⤓  대화 내보내기")
        export_btn.setObjectName("SecondaryButton")
        export_btn.clicked.connect(self.export_requested.emit)
        clear_btn = QPushButton("🗑  지우기")
        clear_btn.setObjectName("SecondaryButton")
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
        self.empty_chat_label = QLabel("새 대화를 시작해보세요.")
        self.empty_chat_label.setObjectName("Muted")
        self.empty_chat_label.setAlignment(Qt.AlignCenter)
        self.messages_layout.addWidget(self.empty_chat_label)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(self.messages_host)
        container.addWidget(self.scroll, 1)

        composer = QFrame()
        composer.setObjectName("Card")
        composer_layout = QVBoxLayout(composer)
        self.input = ChatInput()
        self.input.setPlaceholderText("번역할 문장이나 궁금한 점을 입력하세요…")
        self.input.setFixedHeight(64)
        self.input.setStyleSheet("QTextEdit { border: none; }")
        self.input.send_pressed.connect(self._emit_send)
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
    def message_count(self) -> int:
        count = 0
        for i in range(self.messages_layout.count()):
            item = self.messages_layout.itemAt(i)
            if item.widget() is not None and item.widget() is not self.empty_chat_label:
                count += 1
        return count

    def _bubble(self, text: str, is_user: bool, is_error: bool = False) -> QWidget:
        row = QHBoxLayout()
        bubble = QFrame()
        bubble.setObjectName("Card")
        if is_user:
            bubble.setStyleSheet(
                "QFrame#Card { background: #2F6FED; color: white; border: none; "
                "border-radius: 12px; }"
            )
        elif is_error:
            bubble.setStyleSheet(
                "QFrame#Card { background: #FDECEC; border: 1px solid #F5C6C2; "
                "border-radius: 12px; }"
            )
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(12, 10, 12, 10)
        content = QLabel(text)
        content.setWordWrap(True)
        if is_user:
            content.setStyleSheet("color: white;")
        elif is_error:
            content.setStyleSheet("color: #B3261E;")
        layout.addWidget(content)

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
        self._refresh_empty_state()
        self._scroll_to_bottom()

    def add_ai_message(self, text: str) -> None:
        self.messages_layout.insertWidget(
            self.messages_layout.count() - 1, self._bubble(text, False)
        )
        self._refresh_empty_state()
        self._scroll_to_bottom()

    def add_error_message(self, text: str) -> None:
        """backend 오류를 GUI 프로세스 종료 없이 표시합니다."""
        self.messages_layout.insertWidget(
            self.messages_layout.count() - 1, self._bubble(text, False, is_error=True)
        )
        self._refresh_empty_state()
        self._scroll_to_bottom()

    def clear_messages(self) -> None:
        for i in reversed(range(self.messages_layout.count())):
            item = self.messages_layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None and widget is not self.empty_chat_label:
                self.messages_layout.removeWidget(widget)
                widget.deleteLater()
        if self.empty_chat_label not in [
            self.messages_layout.itemAt(i).widget()
            for i in range(self.messages_layout.count())
        ]:
            self.messages_layout.insertWidget(0, self.empty_chat_label)
        self._refresh_empty_state()

    def set_model_info(self, model: str, context: str = "-") -> None:
        """실제 Settings 모델명을 표시합니다. 연결 전이면 '-'를 사용합니다."""
        self.model_value.setText(model or "-")
        self.context_value.setText(context or "-")
        if model and model != "-":
            self.subtitle_label.setText(f"● 번역 도우미 · {model}")
        else:
            self.subtitle_label.setText("○ 모델 미연결")

    def set_sending(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.input.setReadOnly(busy)

    def _refresh_empty_state(self) -> None:
        has_message = self.message_count() > 0
        self.empty_chat_label.setVisible(not has_message)
        self.empty_history_label.setVisible(self.history_list.count() == 0)

    # ---------- 최근 대화 목록 (표시 전용, id는 Qt.UserRole) ----------
    def _on_history_clicked(self, item: QListWidgetItem) -> None:
        session_id = item.data(Qt.UserRole)
        if isinstance(session_id, str) and session_id:
            self.conversation_selected.emit(session_id)

    def set_conversations(self, sessions: list[dict], current_id: str | None = None) -> None:
        """session 요약 목록을 다시 그립니다. 동일 제목도 id로 구분됩니다."""
        self.history_list.blockSignals(True)
        try:
            self.history_list.clear()
            for session in sessions:
                item = QListWidgetItem(str(session.get("title", "새 대화")))
                item.setData(Qt.UserRole, str(session.get("id", "")))
                self.history_list.addItem(item)
                if current_id is not None and session.get("id") == current_id:
                    self.history_list.setCurrentItem(item)
        finally:
            self.history_list.blockSignals(False)
        self._refresh_empty_state()

    def set_conversation_title(self, title: str) -> None:
        self.title_label.setText(title)

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
