"""QMainWindow + Sidebar + QStackedWidget 공통 셸입니다."""

from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from script.gui.pages.chat_page import ChatPage
from script.gui.pages.prompt_page import PromptPage
from script.gui.pages.translation_page import TranslationPage
from script.gui.widgets.sidebar import Sidebar


class MainWindow(QMainWindow):
    """1200x800 기준 데스크톱 셸입니다. OS 네이티브 타이틀바를 사용합니다."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Local Translate Chat")
        self.resize(1200, 800)
        self.setMinimumSize(1024, 640)

        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.chat_page = ChatPage()
        self.translation_page = TranslationPage()
        self.prompt_page = PromptPage()

        # Sidebar index: 0 챗봇, 1 번역, 2 Prompt
        self.stack.addWidget(self.chat_page)  # 0
        self.stack.addWidget(self.translation_page)  # 1
        self.stack.addWidget(self.prompt_page)  # 2

        self.sidebar.page_requested.connect(self.set_page)
        self.set_page(Sidebar.TRANSLATION)

        # 기본 UI interaction: 챗봇 전송은 더미 답변 구조만 유지합니다.
        self.chat_page.send_requested.connect(self._on_chat_send)

    def set_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.sidebar.set_active(index)

    def _on_chat_send(self, text: str) -> None:
        # 이번 단계는 backend 미연결: ChatEngine 연결 지점임을 명시합니다.
        _ = text
        # self.chat_page.add_ai_message(...) 호출은 실제 Worker 연결 후 수행합니다.
