"""파일 번역 화면입니다. 첨부 이미지 1번을 기준으로 구현합니다."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from script.gui.widgets import Card, StatusBadge, muted_label, section_header


class FileRow(QFrame):
    """선택된 파일 1행입니다."""

    removed = Signal(QWidget)

    def __init__(self, filename: str, meta: str, ext: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FileRow" if ext.upper() == "CSV" else "FileRowPlain")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        badge = QLabel(ext.upper())
        badge.setObjectName("ExtCSV" if ext.upper() == "CSV" else "ExtJSON")
        layout.addWidget(badge)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        name = QLabel(filename)
        name.setStyleSheet("font-weight: 700;")
        info = QLabel(meta)
        info.setObjectName("TinyMuted")
        text_col.addWidget(name)
        text_col.addWidget(info)
        layout.addLayout(text_col, 1)

        close_btn = QPushButton("×")
        close_btn.setObjectName("GhostIconButton")
        close_btn.setFixedWidth(28)
        close_btn.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(close_btn)


class TranslationPage(QWidget):
    """좌측 파일 선택/번역 옵션 + 우측 Prompt 선택/번역 진행 화면입니다."""

    start_requested = Signal()
    stop_requested = Signal()
    open_output_requested = Signal()
    files_changed = Signal(list)  # 현재 파일명 목록

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(10)

        # 상단 타이틀
        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        page_title = QLabel("파일 번역")
        page_title.setObjectName("PageTitle")
        page_sub = QLabel("로컬 AI로 TXT, CSV, JSON 파일을 안전하게 번역합니다.")
        page_sub.setObjectName("PageSubtitle")
        title_col.addWidget(page_title)
        title_col.addWidget(page_sub)
        title_row.addLayout(title_col, 1)
        title_row.addWidget(StatusBadge("● 엔진 준비됨"), 0, Qt.AlignTop)
        root.addLayout(title_row)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        # ---- 좌측 ----
        left = QVBoxLayout()
        left.setSpacing(12)
        body.addLayout(left, 3)

        left.addWidget(self._build_file_card())
        left.addWidget(self._build_option_card())
        left.addStretch(1)

        # ---- 우측 ----
        right = QVBoxLayout()
        right.setSpacing(12)
        body.addLayout(right, 2)

        right.addWidget(self._build_prompt_card())
        right.addWidget(self._build_progress_card())
        right.addStretch(1)

        self._state = "idle"
        self.set_translation_state("idle")

    # ---------- 카드 구성 ----------
    def _build_file_card(self) -> Card:
        card = Card()
        card.inner.addLayout(section_header("1. 파일 선택", "2개 선택됨 · 최대 100MB"))

        drop = QFrame()
        drop.setObjectName("DropArea")
        drop.setAcceptDrops(True)
        drop_layout = QVBoxLayout(drop)
        drop_layout.setAlignment(Qt.AlignCenter)
        icon = QLabel("⇪")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 20px; color: #2F6FED;")
        text = QLabel('파일을 여기로 드래그하거나 <a href="#">파일 선택</a>')
        text.setOpenExternalLinks(False)
        text.linkActivated.connect(lambda _=None: self._pick_files())
        sub = QLabel("TXT · CSV · JSON")
        sub.setObjectName("TinyMuted")
        sub.setAlignment(Qt.AlignCenter)
        text.setAlignment(Qt.AlignCenter)
        drop_layout.addWidget(icon)
        drop_layout.addWidget(text)
        drop_layout.addWidget(sub)
        drop.dragEnterEvent = self._on_drag_enter  # type: ignore[method-assign]
        drop.dropEvent = self._on_drop  # type: ignore[method-assign]
        card.inner.addWidget(drop)
        self.drop_area = drop

        self.file_list_box = QVBoxLayout()
        self.file_list_box.setSpacing(8)
        card.inner.addLayout(self.file_list_box)
        # 디자인 확인용 더미 2개 (backend 미연결)
        self._add_file_row("game_dialogue_ep03.csv", "1.8 MB · 2,418행 · 번역 중", "CSV")
        self._add_file_row("item_descriptions.json", "624 KB · 786개 Key · 대기 중", "JSON")
        return card

    def _build_option_card(self) -> Card:
        card = Card()
        card.inner.addLayout(section_header("2. 번역 옵션"))

        lang_row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("원본 언어"))
        self.source_combo = QComboBox()
        self.source_combo.addItems(["자동 감지 (일본어)", "Japanese", "English", "Korean", "Chinese"])
        left_col.addWidget(self.source_combo)
        lang_row.addLayout(left_col, 1)
        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignCenter)
        lang_row.addWidget(arrow)
        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("목표 언어"))
        self.target_combo = QComboBox()
        self.target_combo.addItems(["한국어", "English", "Japanese", "Chinese"])
        right_col.addWidget(self.target_combo)
        lang_row.addLayout(right_col, 1)
        card.inner.addLayout(lang_row)

        ai_row = QHBoxLayout()
        ai_title = QLabel("✦ AI 분석 번역 대상")
        ai_title.setStyleSheet("font-weight: 700; color: #1B2430;")
        ai_row.addWidget(ai_title)
        ai_row.addStretch(1)
        ai_row.addWidget(muted_label("선택 항목은 수정할 수 있습니다", "TinyMuted"))
        card.inner.addLayout(ai_row)

        self.field_checks: list[QCheckBox] = []
        for name, sample, checked in [
            ("speaker_name", "ミナ、ハルト、店主…", True),
            ("dialogue_text", "やっと会えたね。ずっと待ってた。", True),
            ("scene_note", "夕暮れの港、遠くで汽笛が鳴る", False),
        ]:
            row = QHBoxLayout()
            check = QCheckBox(name)
            check.setChecked(checked)
            check.setStyleSheet("font-weight: 700;")
            sample_label = QLabel(sample)
            sample_label.setObjectName("TinyMuted")
            row.addWidget(check)
            row.addWidget(sample_label, 1)
            card.inner.addLayout(row)
            self.field_checks.append(check)
        return card

    def _build_prompt_card(self) -> Card:
        card = Card()
        card.inner.addLayout(section_header("3. Prompt 선택"))
        self.prompt_combo = QComboBox()
        self.prompt_combo.addItems([
            "Japanese → Korean · game_dialogue v3",
            "Japanese → Korean · novel",
            "English → Korean · game_ui",
        ])
        card.inner.addWidget(self.prompt_combo)

        recommend = QFrame()
        recommend.setObjectName("RecommendBox")
        rec_layout = QVBoxLayout(recommend)
        rec_layout.setContentsMargins(10, 8, 10, 8)
        rec_title = QLabel("◷  이전 사용 Prompt 추천")
        rec_title.setStyleSheet("font-weight: 700; font-size: 12px; color: #8A6D1B;")
        rec_sub = QLabel("이 파일에 마지막으로 사용됨 · 2026.09.27")
        rec_sub.setObjectName("TinyMuted")
        rec_layout.addWidget(rec_title)
        rec_layout.addWidget(rec_sub)
        card.inner.addWidget(recommend)
        self.recommend_box = recommend

        btn_row = QHBoxLayout()
        self.btn_view_prompt = QPushButton("👁  Prompt 내용 확인")
        self.btn_view_prompt.setObjectName("SecondaryButton")
        self.btn_new_prompt = QPushButton("✦  AI로 새 Prompt 생성")
        self.btn_new_prompt.setObjectName("SecondaryButton")
        btn_row.addWidget(self.btn_view_prompt, 1)
        btn_row.addWidget(self.btn_new_prompt, 1)
        card.inner.addLayout(btn_row)
        return card

    def _build_progress_card(self) -> Card:
        card = Card()
        top = QHBoxLayout()
        title = QLabel("번역 진행")
        title.setObjectName("SectionTitle")
        top.addWidget(title)
        top.addStretch(1)
        self.state_badge = QLabel("번역 중")
        self.state_badge.setObjectName("BadgeBlue")
        top.addWidget(self.state_badge)
        card.inner.addLayout(top)

        file_row = QHBoxLayout()
        file_icon = QLabel("▤")
        file_icon.setStyleSheet(
            "background: #E8F0FE; color: #2F6FED; border-radius: 6px; padding: 6px;"
        )
        file_col = QVBoxLayout()
        file_col.setSpacing(1)
        self.current_file_label = QLabel("game_dialogue_ep03.csv")
        self.current_file_label.setStyleSheet("font-weight: 700;")
        self.current_file_sub = QLabel("1,642 / 2,418행 · 남은 시간 약 01:18")
        self.current_file_sub.setObjectName("TinyMuted")
        file_col.addWidget(self.current_file_label)
        file_col.addWidget(self.current_file_sub)
        self.percent_label = QLabel("68%")
        self.percent_label.setStyleSheet("font-size: 18px; font-weight: 800; color: #2F6FED;")
        file_row.addWidget(file_icon)
        file_row.addLayout(file_col, 1)
        file_row.addWidget(self.percent_label)
        card.inner.addLayout(file_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(68)
        self.progress.setTextVisible(False)
        card.inner.addWidget(self.progress)

        self.status_label = QLabel("⟳  현재: 1642행 대사 번역 및 용어집 일관성 검사 중…")
        self.status_label.setObjectName("Muted")
        status_box = QFrame()
        status_box.setObjectName("StatusBox")
        status_layout = QHBoxLayout(status_box)
        status_layout.setContentsMargins(10, 8, 10, 8)
        status_layout.addWidget(self.status_label)
        card.inner.addWidget(status_box)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("▶  번역 시작")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_stop = QPushButton("◻  번역 중지")
        self.btn_stop.setObjectName("StopButton")
        btn_row.addWidget(self.btn_start, 1)
        btn_row.addWidget(self.btn_stop, 1)
        card.inner.addLayout(btn_row)
        self.btn_open_output = QPushButton("🗀  결과 폴더 열기")
        self.btn_open_output.setObjectName("SecondaryButton")
        card.inner.addWidget(self.btn_open_output)

        self.btn_start.clicked.connect(self.start_requested.emit)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        self.btn_open_output.clicked.connect(self.open_output_requested.emit)
        return card

    # ---------- 파일 행 / DnD ----------
    def _add_file_row(self, filename: str, meta: str, ext: str) -> None:
        row = FileRow(filename, meta, ext)
        row.removed.connect(self._remove_file_row)
        self.file_list_box.addWidget(row)
        self._emit_files_changed()

    def _remove_file_row(self, row: QWidget) -> None:
        self.file_list_box.removeWidget(row)
        row.deleteLater()
        self._emit_files_changed()

    def _emit_files_changed(self) -> None:
        names = [
            self.file_list_box.itemAt(i).widget().findChildren(QLabel)[1].text()
            for i in range(self.file_list_box.count())
            if self.file_list_box.itemAt(i).widget() is not None
        ]
        self.files_changed.emit(names)

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "번역할 파일 선택", "", "Text/CSV/JSON (*.txt *.csv *.json)"
        )
        for path in paths:
            name = Path(path).name
            self._add_file_row(name, "대기 중", Path(path).suffix.lstrip(".") or "TXT")

    def _on_drag_enter(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def _on_drop(self, event) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in {".txt", ".csv", ".json"}:
                self._add_file_row(path.name, "대기 중", path.suffix.lstrip("."))
        event.acceptProposedAction()

    # ---------- 외부에서 호출하는 상태 메서드 (backend 연결 지점) ----------
    def set_progress(self, percent: int, status: str | None = None) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.percent_label.setText(f"{self.progress.value()}%")
        if status is not None:
            self.set_status(status)

    def set_current_file(self, filename: str, detail: str = "") -> None:
        self.current_file_label.setText(filename)
        if detail:
            self.current_file_sub.setText(detail)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_translation_state(self, state: str) -> None:
        """idle | running | stopped | done — 버튼 활성화와 뱃지를 함께 바꿉니다."""
        self._state = state
        labels = {"idle": "대기 중", "running": "번역 중", "stopped": "중지됨", "done": "완료"}
        self.state_badge.setText(labels.get(state, state))
        self.btn_start.setEnabled(state in {"idle", "stopped", "done"})
        self.btn_stop.setEnabled(state == "running")

    def set_model_status(self, ready: bool) -> None:
        self.set_status("엔진 준비됨" if ready else "엔진 확인 필요")
