"""파일 번역 화면입니다. 초기 상태는 빈 상태이며 실제 Path를 보관합니다."""

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
from script.translation.formats import SUPPORTED_EXTENSIONS

ALLOWED_EXTENSIONS = SUPPORTED_EXTENSIONS


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


class FileRow(QFrame):
    """선택된 파일 1행입니다. 실제 Path를 보관합니다."""

    removed = Signal(object)  # Path

    def __init__(self, file_path: Path, meta: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        ext = file_path.suffix.upper().lstrip(".") or "TXT"
        self.setObjectName("FileRow" if ext == "CSV" else "FileRowPlain")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        badge = QLabel(ext)
        badge.setObjectName("ExtCSV" if ext == "CSV" else "ExtJSON")
        layout.addWidget(badge)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        name = QLabel(file_path.name)
        name.setStyleSheet("font-weight: 700;")
        info = QLabel(meta)
        info.setObjectName("TinyMuted")
        text_col.addWidget(name)
        text_col.addWidget(info)
        layout.addLayout(text_col, 1)

        close_btn = QPushButton("×")
        close_btn.setObjectName("GhostIconButton")
        close_btn.setFixedWidth(28)
        close_btn.clicked.connect(lambda: self.removed.emit(self.file_path))
        layout.addWidget(close_btn)


class TranslationPage(QWidget):
    """좌측 파일 선택/번역 옵션 + 우측 Prompt 선택/번역 진행 화면입니다."""

    start_requested = Signal()
    stop_requested = Signal()
    open_output_requested = Signal()
    files_changed = Signal(object)  # list[Path]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.selected_files: list[Path] = []
        self._rows: dict[Path, FileRow] = {}
        self.field_checks: list[QCheckBox] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(10)

        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        page_title = QLabel("파일 번역")
        page_title.setObjectName("PageTitle")
        page_sub = QLabel("로컬 AI로 TXT, CSV, JSON 파일을 안전하게 번역합니다.")
        page_sub.setObjectName("PageSubtitle")
        title_col.addWidget(page_title)
        title_col.addWidget(page_sub)
        title_row.addLayout(title_col, 1)
        self.engine_badge = StatusBadge("○ 엔진 확인 중")
        title_row.addWidget(self.engine_badge, 0, Qt.AlignTop)
        root.addLayout(title_row)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(12)
        body.addLayout(left, 3)
        left.addWidget(self._build_file_card())
        left.addWidget(self._build_option_card())
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(12)
        body.addLayout(right, 2)
        right.addWidget(self._build_prompt_card())
        right.addWidget(self._build_progress_card())
        right.addStretch(1)

        self._state = "idle"
        self.set_translation_state("idle")
        self.set_progress(0)
        self.set_current_file("-")
        self._refresh_empty_state()

    # ---------- 카드 구성 ----------
    def _build_file_card(self) -> Card:
        card = Card()
        header = section_header("1. 파일 선택", "최대 100MB")
        card.inner.addLayout(header)
        self.file_count_label = header.itemAt(2).widget() if header.count() > 2 else None

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

        self.empty_file_label = QLabel("번역할 파일을 추가해주세요.")
        self.empty_file_label.setObjectName("Muted")
        self.empty_file_label.setAlignment(Qt.AlignCenter)
        card.inner.addWidget(self.empty_file_label)

        self.file_list_box = QVBoxLayout()
        self.file_list_box.setSpacing(8)
        card.inner.addLayout(self.file_list_box)
        return card

    def _build_option_card(self) -> Card:
        card = Card()
        card.inner.addLayout(section_header("2. 번역 옵션"))

        lang_row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("원본 언어"))
        self.source_combo = QComboBox()
        self.source_combo.addItems(["자동 감지", "Korean", "Japanese", "English", "Chinese"])
        left_col.addWidget(self.source_combo)
        lang_row.addLayout(left_col, 1)
        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignCenter)
        lang_row.addWidget(arrow)
        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("목표 언어"))
        self.target_combo = QComboBox()
        self.target_combo.addItems(["Korean", "English", "Japanese", "Chinese"])
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

        self.fields_box = QVBoxLayout()
        self.fields_box.setSpacing(6)
        card.inner.addLayout(self.fields_box)
        self.empty_field_label = QLabel("CSV/JSON 파일을 선택하면 분석 후 표시됩니다.")
        self.empty_field_label.setObjectName("TinyMuted")
        self.fields_box.addWidget(self.empty_field_label)
        return card

    def _build_prompt_card(self) -> Card:
        card = Card()
        card.inner.addLayout(section_header("3. Prompt 선택"))
        self.prompt_combo = QComboBox()
        self.prompt_combo.addItem("기본 Prompt")
        card.inner.addWidget(self.prompt_combo)

        recommend = QFrame()
        recommend.setObjectName("RecommendBox")
        rec_layout = QVBoxLayout(recommend)
        rec_layout.setContentsMargins(10, 8, 10, 8)
        self.recommend_title = QLabel("이전 사용 Prompt 추천 없음")
        self.recommend_title.setStyleSheet("font-weight: 700; font-size: 12px; color: #8A6D1B;")
        self.recommend_sub = QLabel("파일을 선택하면 추천을 표시합니다.")
        self.recommend_sub.setObjectName("TinyMuted")
        rec_layout.addWidget(self.recommend_title)
        rec_layout.addWidget(self.recommend_sub)
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
        self.state_badge = QLabel("대기 중")
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
        self.current_file_label = QLabel("-")
        self.current_file_label.setStyleSheet("font-weight: 700;")
        self.current_file_sub = QLabel("")
        self.current_file_sub.setObjectName("TinyMuted")
        file_col.addWidget(self.current_file_label)
        file_col.addWidget(self.current_file_sub)
        self.percent_label = QLabel("0%")
        self.percent_label.setStyleSheet("font-size: 18px; font-weight: 800; color: #2F6FED;")
        file_row.addWidget(file_icon)
        file_row.addLayout(file_col, 1)
        file_row.addWidget(self.percent_label)
        card.inner.addLayout(file_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        card.inner.addWidget(self.progress)

        self.status_label = QLabel("대기 중")
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

    # ---------- 파일 Path 보관 ----------
    def add_file_path(self, path: Path) -> bool:
        """실제 Path를 보관합니다. 중복/미지원 확장자는 거부하고 False를 반환합니다."""
        try:
            resolved = path.resolve() if path.exists() else path.absolute()
        except OSError:
            return False
        if resolved.suffix.lower() not in ALLOWED_EXTENSIONS:
            return False
        if any(existing == resolved for existing in self.selected_files):
            return False
        meta = "대기 중"
        try:
            if resolved.is_file():
                meta = f"{_format_size(resolved.stat().st_size)} · 대기 중"
        except OSError:
            pass
        self.selected_files.append(resolved)
        row = FileRow(resolved, meta)
        row.removed.connect(self.remove_file_path)
        self._rows[resolved] = row
        self.file_list_box.addWidget(row)
        self._refresh_empty_state()
        self.files_changed.emit(list(self.selected_files))
        return True

    def remove_file_path(self, path: Path) -> None:
        target = None
        for existing in self.selected_files:
            if existing == path:
                target = existing
                break
        if target is None:
            return
        self.selected_files.remove(target)
        row = self._rows.pop(target, None)
        if row is not None:
            self.file_list_box.removeWidget(row)
            row.deleteLater()
        self._refresh_empty_state()
        self.files_changed.emit(list(self.selected_files))

    def selected_paths(self) -> list[Path]:
        return list(self.selected_files)

    def clear_files(self) -> None:
        for path in list(self.selected_files):
            self.remove_file_path(path)

    def _refresh_empty_state(self) -> None:
        empty = not self.selected_files
        self.empty_file_label.setVisible(empty)

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "번역할 파일 선택", "", "Text/CSV/JSON (*.txt *.csv *.json)"
        )
        for raw in paths:
            self.add_file_path(Path(raw))

    def _on_drag_enter(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def _on_drop(self, event) -> None:
        for url in event.mimeData().urls():
            self.add_file_path(Path(url.toLocalFile()))
        event.acceptProposedAction()

    # ---------- 번역 대상 필드 (실제 분석 후에만 표시) ----------
    def set_fields(self, fields: list[tuple[str, str]], selected: list[str] | None = None) -> None:
        """CSV/JSON 실제 field/key 목록을 표시합니다. 분석 전에는 호출하지 마세요."""
        self.clear_fields()
        self.empty_field_label.setVisible(False)
        chosen = set(selected) if selected is not None else {name for name, _ in fields}
        for name, sample in fields:
            row = QHBoxLayout()
            check = QCheckBox(name)
            check.setChecked(name in chosen)
            check.setStyleSheet("font-weight: 700;")
            sample_label = QLabel(sample)
            sample_label.setObjectName("TinyMuted")
            row.addWidget(check)
            row.addWidget(sample_label, 1)
            self.fields_box.addLayout(row)
            self.field_checks.append(check)

    def clear_fields(self) -> None:
        self.field_checks = []
        for i in reversed(range(self.fields_box.count())):
            item = self.fields_box.itemAt(i)
            if item is None:
                continue
            if item.widget() is self.empty_field_label:
                continue
            taken = self.fields_box.takeAt(i)
            if taken.widget():
                taken.widget().deleteLater()
            elif taken.layout():
                layout = taken.layout()
                while layout.count():
                    sub = layout.takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
        self.empty_field_label.setVisible(True)

    def selected_field_names(self) -> list[str]:
        return [check.text() for check in self.field_checks if check.isChecked()]

    # ---------- Prompt 콤보 (실제 Preset용) ----------
    def set_prompt_items(self, items: list[str]) -> None:
        self.prompt_combo.clear()
        if items:
            self.prompt_combo.addItems(items)
        else:
            self.prompt_combo.addItem("기본 Prompt")

    def set_recommendation(self, title: str, detail: str) -> None:
        self.recommend_title.setText(title)
        self.recommend_sub.setText(detail)

    # ---------- 상태 메서드 ----------
    def set_progress(self, percent: int, status: str | None = None) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.percent_label.setText(f"{self.progress.value()}%")
        if status is not None:
            self.set_status(status)

    def set_current_file(self, filename: str, detail: str = "") -> None:
        self.current_file_label.setText(filename)
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

    def set_engine_status(self, ready: bool) -> None:
        """상단 엔진 Badge를 실제 Ollama 상태와 연동합니다."""
        self.engine_badge.setText("● 엔진 준비됨" if ready else "○ 엔진 확인 필요")
        self.engine_badge.setObjectName("BadgeGreen" if ready else "BadgeBlue")
        self.engine_badge.style().unpolish(self.engine_badge)
        self.engine_badge.style().polish(self.engine_badge)

    def set_model_status(self, ready: bool) -> None:
        """기존 호환용 별칭입니다. 상단 Badge를 갱신합니다."""
        self.set_engine_status(ready)
