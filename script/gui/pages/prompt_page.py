"""Prompt 설정 화면입니다. 실제 PromptManager 데이터에 연결됩니다."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from script.gui.widgets import Card, muted_label
from script.translation.prompt_manager import PromptManager, PromptPreset
from script.utils.exceptions import ChatbotError


#: Prompt Preset에서 사용하는 고정 언어 목록입니다. 자동 감지는 포함하지 않습니다.
PRESET_LANGUAGES = ("Korean", "Japanese", "English", "Chinese")


class PromptListCard(QFrame):
    """저장된 Prompt 1행 카드입니다."""

    def __init__(self, preset: PromptPreset, selected: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.preset = preset
        self.setObjectName("PromptCard")
        self.setProperty("selected", selected)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        name = QLabel(preset.name)
        name.setStyleSheet("font-weight: 800;")
        title_row.addWidget(name, 1)
        if selected:
            dot = QLabel("●")
            dot.setStyleSheet("color: #2F6FED;")
            title_row.addWidget(dot)
        layout.addLayout(title_row)

        meta_row = QHBoxLayout()
        meta_row.addWidget(self._chip(preset.source_language))
        arrow = QLabel("→")
        arrow.setObjectName("TinyMuted")
        meta_row.addWidget(arrow)
        meta_row.addWidget(self._chip(preset.target_language))
        meta_row.addWidget(self._chip(preset.document_type))
        meta_row.addStretch(1)
        layout.addLayout(meta_row)

        updated_label = QLabel(f"최근 수정 {preset.updated_at}")
        updated_label.setObjectName("TinyMuted")
        layout.addWidget(updated_label)

    @staticmethod
    def _chip(text: str) -> QLabel:
        chip = QLabel(text)
        chip.setStyleSheet(
            "background: #F1F5F9; border-radius: 6px; padding: 3px 8px; "
            "font-size: 11px; color: #475569;"
        )
        return chip


class PromptPage(QWidget):
    """좌측 Prompt 목록 + 우측 Prompt Editor 화면입니다."""

    ai_generate_requested = Signal()

    def __init__(self, manager: PromptManager | None = None, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager or PromptManager()
        self.presets: list[PromptPreset] = []
        self.current: PromptPreset | None = None
        self.is_new = False
        # AI 초안 기반 저장이면 True. Manual 직접 작성과 구분합니다.
        self.ai_draft = False
        self._cards: list[PromptListCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("Prompt 설정")
        title.setObjectName("PageTitle")
        sub = QLabel("언어와 문서 유형별 번역 지침을 만들고 관리합니다.")
        sub.setObjectName("PageSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        header.addLayout(title_col, 1)

        import_btn = QPushButton("⤒  TXT/JSON Prompt 가져오기")
        import_btn.setObjectName("SecondaryButton")
        import_btn.clicked.connect(self.import_prompt)
        export_btn = QPushButton("⤓  내보내기")
        export_btn.setObjectName("SecondaryButton")
        export_btn.clicked.connect(self.export_prompt)
        new_btn = QPushButton("+  새 Prompt 생성")
        new_btn.setObjectName("PrimaryButton")
        new_btn.clicked.connect(self.new_prompt)
        header.addWidget(import_btn)
        header.addWidget(export_btn)
        header.addWidget(new_btn)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)
        body.addWidget(self._build_list_card(), 2)
        body.addWidget(self._build_editor_card(), 3)

        self.load_presets()

    # ---------- 좌측 ----------
    def _build_list_card(self) -> Card:
        card = Card()
        top = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("저장된 Prompt")
        title.setObjectName("SectionTitle")
        self.count_label = QLabel("총 0개")
        self.count_label.setObjectName("TinyMuted")
        title_col.addWidget(title)
        title_col.addWidget(self.count_label)
        top.addLayout(title_col, 1)
        search = QPushButton("⌕")
        search.setObjectName("SecondaryButton")
        search.setFixedWidth(36)
        top.addWidget(search)
        card.inner.addLayout(top)

        filter_grid = QVBoxLayout()
        row1 = QHBoxLayout()
        src_col = QVBoxLayout()
        src_col.addWidget(QLabel("원본 언어"))
        self.filter_source = QComboBox()
        src_col.addWidget(self.filter_source)
        tgt_col = QVBoxLayout()
        tgt_col.addWidget(QLabel("목표 언어"))
        self.filter_target = QComboBox()
        tgt_col.addWidget(self.filter_target)
        row1.addLayout(src_col, 1)
        row1.addLayout(tgt_col, 1)
        filter_grid.addLayout(row1)
        doc_col = QVBoxLayout()
        doc_col.addWidget(QLabel("문서 유형"))
        self.filter_doc = QComboBox()
        doc_col.addWidget(self.filter_doc)
        filter_grid.addLayout(doc_col)
        card.inner.addLayout(filter_grid)
        self.filter_source.currentTextChanged.connect(lambda _=None: self._apply_filter())
        self.filter_target.currentTextChanged.connect(lambda _=None: self._apply_filter())
        self.filter_doc.currentTextChanged.connect(lambda _=None: self._apply_filter())

        self.filter_path_label = QLabel("-")
        self.filter_path_label.setObjectName("Muted")
        self.filter_path_label.setStyleSheet(
            "background: #F7F9FC; border-radius: 6px; padding: 6px 8px;"
        )
        card.inner.addWidget(self.filter_path_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        self.preset_box = QVBoxLayout(host)
        self.preset_box.setContentsMargins(0, 0, 0, 0)
        self.preset_box.setSpacing(8)
        self.empty_preset_label = QLabel("저장된 Prompt가 없습니다.")
        self.empty_preset_label.setObjectName("Muted")
        self.empty_preset_label.setAlignment(Qt.AlignCenter)
        self.preset_box.addWidget(self.empty_preset_label)
        self.preset_box.addStretch(1)
        scroll.setWidget(host)
        card.inner.addWidget(scroll, 1)
        return card

    # ---------- 우측 ----------
    def _build_editor_card(self) -> Card:
        card = Card()

        top = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.title_label = QLabel("Prompt를 선택하세요")
        self.title_label.setStyleSheet("font-size: 15px; font-weight: 800;")
        self.subtitle_label = QLabel("-")
        self.subtitle_label.setObjectName("Muted")
        title_col.addWidget(self.title_label)
        title_col.addWidget(self.subtitle_label)
        top.addLayout(title_col, 1)
        self.ai_button = QPushButton("✦ AI로 Prompt 생성")
        self.ai_button.setObjectName("SecondaryButton")
        self.ai_button.clicked.connect(self.ai_generate_requested.emit)
        self.delete_button = QPushButton("🗑 삭제")
        self.delete_button.setObjectName("DangerButton")
        self.delete_button.clicked.connect(self.delete_current)
        top.addWidget(self.ai_button)
        top.addWidget(self.delete_button)
        card.inner.addLayout(top)

        form = QHBoxLayout()
        # Prompt Preset 언어는 고정 dropdown입니다. 자동 감지는 넣지 않습니다.
        # legacy/custom 값은 _set_combo_text()가 안전하게 추가 표시합니다.
        self.source_combo = self._labeled_combo(
            form, "원본 언어", list(PRESET_LANGUAGES)
        )
        self.target_combo = self._labeled_combo(
            form, "목표 언어", list(PRESET_LANGUAGES)
        )
        self.doc_edit = self._labeled_line_edit(form, "문서 유형")
        card.inner.addLayout(form)

        edit_head = QHBoxLayout()
        edit_head.addWidget(QLabel("Prompt 내용 편집"))
        edit_head.addStretch(1)
        self.char_label = QLabel("")
        self.char_label.setObjectName("TinyMuted")
        edit_head.addWidget(self.char_label)
        card.inner.addLayout(edit_head)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Prompt를 선택하거나 새로 만드세요.")
        self.editor.setMinimumHeight(260)
        self.editor.textChanged.connect(self._refresh_char_count)
        card.inner.addWidget(self.editor, 1)

        bottom = QHBoxLayout()
        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #6B7A90; font-size: 12px;")
        bottom.addWidget(self.validation_label, 1)
        self.cancel_button = QPushButton("변경 취소")
        self.cancel_button.setObjectName("SecondaryButton")
        self.cancel_button.clicked.connect(self.cancel_edit)
        self.save_button = QPushButton("🖫  변경사항 저장")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self.save_current)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.save_button)
        card.inner.addLayout(bottom)

        meta = QHBoxLayout()
        self.meta_usage = QLabel("-")
        self.meta_version = QLabel("-")
        meta.addWidget(muted_label("사용 횟수", "TinyMuted"))
        meta.addStretch(1)
        meta.addWidget(self.meta_usage)
        meta.addStretch(2)
        meta.addWidget(muted_label("버전", "TinyMuted"))
        meta.addStretch(1)
        meta.addWidget(self.meta_version)
        card.inner.addLayout(meta)
        meta2 = QHBoxLayout()
        self.meta_last = QLabel("-")
        self.meta_created_by = QLabel("-")
        meta2.addWidget(muted_label("마지막 사용", "TinyMuted"))
        meta2.addStretch(1)
        meta2.addWidget(self.meta_last)
        meta2.addStretch(2)
        meta2.addWidget(muted_label("생성 방식", "TinyMuted"))
        meta2.addStretch(1)
        meta2.addWidget(self.meta_created_by)
        card.inner.addLayout(meta2)
        return card

    @staticmethod
    def _labeled_combo(parent_layout, label: str, items: list[str]) -> QComboBox:
        col = QVBoxLayout()
        col.addWidget(QLabel(label))
        combo = QComboBox()
        combo.setEditable(True)
        if items:
            combo.addItems(items)
        col.addWidget(combo)
        parent_layout.addLayout(col, 1)
        return combo

    @staticmethod
    def _labeled_line_edit(parent_layout, label: str):
        from PySide6.QtWidgets import QLineEdit

        col = QVBoxLayout()
        col.addWidget(QLabel(label))
        edit = QLineEdit()
        edit.setPlaceholderText("예: game_dialogue")
        col.addWidget(edit)
        parent_layout.addLayout(col, 1)
        return edit

    # ---------- 실제 데이터 로드 ----------
    def load_presets(self) -> None:
        try:
            self.presets = self.manager.list_presets()
        except ChatbotError as error:
            self.presets = []
            self.set_validation(False, f"Preset 불러오기 실패: {error}")
        self._refresh_filter_options()
        self._apply_filter()
        if self.current is None and not self.is_new:
            self._show_empty_editor()

    def _refresh_filter_options(self) -> None:
        def options(values: list[str]) -> list[str]:
            return ["전체"] + sorted(set(values), key=str.casefold)

        src = options([p.source_language for p in self.presets])
        tgt = options([p.target_language for p in self.presets])
        doc = options([p.document_type for p in self.presets])
        for combo, items in (
            (self.filter_source, src),
            (self.filter_target, tgt),
            (self.filter_doc, doc),
        ):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(items)
            if current in items:
                combo.setCurrentText(current)
            combo.blockSignals(False)

    def _apply_filter(self) -> None:
        src = self.filter_source.currentText() if self.filter_source.count() else "전체"
        tgt = self.filter_target.currentText() if self.filter_target.count() else "전체"
        doc = self.filter_doc.currentText() if self.filter_doc.count() else "전체"
        filtered = [
            p
            for p in self.presets
            if (src in ("", "전체") or p.source_language == src)
            and (tgt in ("", "전체") or p.target_language == tgt)
            and (doc in ("", "전체") or p.document_type == doc)
        ]
        self.count_label.setText(f"총 {len(self.presets)}개 · 필터 결과 {len(filtered)}개")
        self.filter_path_label.setText(f"{src}  →  {tgt}  →  {doc}")

        for card in self._cards:
            self.preset_box.removeWidget(card)
            card.deleteLater()
        self._cards = []
        for preset in filtered:
            card = PromptListCard(
                preset,
                selected=self.current is not None and self._same_scope(preset, self.current),
            )
            card.mousePressEvent = self._make_select_handler(preset)  # type: ignore[method-assign]
            self.preset_box.insertWidget(self.preset_box.count() - 1, card)
            self._cards.append(card)
        self.empty_preset_label.setVisible(not filtered)

    @staticmethod
    def _same_scope(a: PromptPreset, b: PromptPreset) -> bool:
        return (
            a.name == b.name
            and a.source_language == b.source_language
            and a.target_language == b.target_language
            and a.document_type == b.document_type
        )

    def _make_select_handler(self, preset: PromptPreset):
        def handler(event) -> None:
            self.select_preset(preset)
            if event is not None:
                event.accept()

        return handler

    # ---------- 선택 / 편집 ----------
    def select_preset(self, preset: PromptPreset) -> None:
        self.current = preset
        self.is_new = False
        self.ai_draft = False
        self.title_label.setText(preset.name)
        self.subtitle_label.setText(
            f"{preset.source_language}  →  {preset.target_language} / {preset.document_type}"
        )
        self._set_combo_text(self.source_combo, preset.source_language)
        self._set_combo_text(self.target_combo, preset.target_language)
        self.doc_edit.setText(preset.document_type)
        self.editor.setPlainText(preset.prompt)
        self.meta_last.setText(preset.updated_at or "-")
        self.meta_created_by.setText(preset.created_by or "-")
        self.meta_usage.setText("-")
        self.meta_version.setText("-")
        self.set_validation(True, "✔  Preset을 불러왔습니다.")
        self._set_editor_enabled(True)
        self._apply_filter()

    def new_prompt(self) -> None:
        self.current = None
        self.is_new = True
        self.ai_draft = False
        self.title_label.setText("새 Prompt")
        self.subtitle_label.setText("-")
        self.source_combo.setCurrentText("Japanese")
        self.target_combo.setCurrentText("Korean")
        self.doc_edit.clear()
        self.editor.clear()
        self.meta_usage.setText("-")
        self.meta_version.setText("-")
        self.meta_last.setText("-")
        self.meta_created_by.setText("-")
        self.set_validation(True, "새 Prompt를 입력한 뒤 저장하세요.")
        self._set_editor_enabled(True)

    def cancel_edit(self) -> None:
        if self.current is not None:
            self.select_preset(self.current)
        else:
            self.is_new = False
            self._show_empty_editor()

    def save_current(self) -> None:
        name = self.title_label.text().strip()
        if self.is_new or self.current is None:
            name, ok = self._ask_text("새 Preset 이름", "저장할 Preset 이름을 입력하세요:")
            if not ok or not name.strip():
                return
            name = name.strip()
        source = self.source_combo.currentText().strip()
        target = self.target_combo.currentText().strip()
        doc_type = self.doc_edit.text().strip()
        prompt = self.editor.toPlainText().strip()
        if not source or not target or not doc_type or not prompt:
            self.set_validation(False, "원본 언어, 목표 언어, 문서 유형, Prompt를 모두 입력하세요.")
            return
        try:
            if self.is_new or self.current is None:
                created_by = "AI" if self.ai_draft else "Manual"
                preset = self.manager.create_preset(name, source, target, doc_type, prompt, created_by)
                self.manager.save(preset, overwrite=False)
            else:
                updated, _ = self.manager.update(
                    self.current,
                    name=name if name != "Prompt를 선택하세요" else self.current.name,
                    source_language=source,
                    target_language=target,
                    document_type=doc_type,
                    prompt=prompt,
                )
                self.current = updated
                self.is_new = False
            self.ai_draft = False
            self.set_validation(True, "✔  저장했습니다.")
        except ChatbotError as error:
            self.set_validation(False, f"저장 실패: {error}")
            return
        self.load_presets()

    def delete_current(self) -> None:
        if self.current is None:
            return
        answer = QMessageBox.question(self, "삭제 확인", f"'{self.current.name}'을 삭제할까요?")
        if answer != QMessageBox.Yes:
            return
        try:
            self.manager.delete(self.current)
        except ChatbotError as error:
            self.set_validation(False, f"삭제 실패: {error}")
            return
        self.current = None
        self.is_new = False
        self.load_presets()

    def import_prompt(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Prompt 가져오기", "", "Prompt (*.txt *.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            if path.suffix.lower() == ".txt":
                name, ok = self._ask_text("가져오기", "Preset 이름을 입력하세요:")
                if not ok or not name.strip():
                    return
                source, ok = self._ask_text("원본 언어", "원본 언어를 입력하세요 (예: Japanese):")
                if not ok:
                    return
                target, ok = self._ask_text("목표 언어", "목표 언어를 입력하세요 (예: Korean):")
                if not ok:
                    return
                doc, ok = self._ask_text("문서 유형", "문서 유형을 입력하세요 (예: game_dialogue):")
                if not ok:
                    return
                self.manager.import_txt(path, name.strip(), source.strip(), target.strip(), doc.strip())
            else:
                self.manager.import_json(path)
            self.set_validation(True, "✔  가져왔습니다.")
        except ChatbotError as error:
            self.set_validation(False, f"가져오기 실패: {error}")
            return
        self.load_presets()

    def export_prompt(self) -> None:
        if self.current is None:
            self.set_validation(False, "내보낼 Prompt를 먼저 선택하세요.")
            return
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Prompt 내보내기", f"{self.current.name}.json", "JSON (*.json);;Text (*.txt)"
        )
        if not path_str:
            return
        try:
            destination = Path(path_str)
            if destination.suffix.lower() == ".txt":
                self.manager.export_txt(self.current, destination)
            else:
                self.manager.export_json(self.current, destination)
            self.set_validation(True, "✔  내보냈습니다.")
        except ChatbotError as error:
            self.set_validation(False, f"내보내기 실패: {error}")

    # ---------- 내부 ----------
    def _show_empty_editor(self) -> None:
        self.title_label.setText("Prompt를 선택하세요")
        self.subtitle_label.setText("-")
        self.editor.clear()
        self.meta_usage.setText("-")
        self.meta_version.setText("-")
        self.meta_last.setText("-")
        self.meta_created_by.setText("-")
        self.char_label.setText("")
        self.set_validation(True, "왼쪽 목록에서 Prompt를 선택하세요.")
        self._set_editor_enabled(False)

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.editor.setReadOnly(not enabled)
        self.save_button.setEnabled(enabled)
        self.cancel_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled and self.current is not None)
        self.source_combo.setEnabled(enabled)
        self.target_combo.setEnabled(enabled)
        self.doc_edit.setEnabled(enabled)

    def _refresh_char_count(self) -> None:
        self.char_label.setText(f"{len(self.editor.toPlainText())}자")

    @staticmethod
    def _set_combo_text(combo: QComboBox, text: str) -> None:
        combo.blockSignals(True)
        if combo.findText(text) < 0:
            combo.addItem(text)
        combo.setCurrentText(text)
        combo.blockSignals(False)

    def _ask_text(self, title: str, label: str) -> tuple[str, bool]:
        from PySide6.QtWidgets import QInputDialog

        return QInputDialog.getText(self, title, label)

    # ---------- backend 연결 지점 ----------
    def set_validation(self, ok: bool, message: str) -> None:
        self.validation_label.setText(message)
        color = "#188038" if ok else "#D93025"
        self.validation_label.setStyleSheet(f"color: {color}; font-size: 12px;")

    def set_editor_text(self, text: str) -> None:
        self.editor.setPlainText(text)

    def editor_text(self) -> str:
        return self.editor.toPlainText()

    def apply_ai_draft(
        self, draft: str, source: str, target: str, document_type: str
    ) -> None:
        """AI 생성 draft를 editor에 삽입합니다. 자동 저장하지 않습니다."""
        self.current = None
        self.is_new = True
        self.ai_draft = True
        self.title_label.setText("새 Prompt (AI 초안)")
        self.subtitle_label.setText("-")
        self._set_combo_text(self.source_combo, source)
        self._set_combo_text(self.target_combo, target)
        self.doc_edit.setText(document_type)
        self.editor.setPlainText(draft)
        self.meta_usage.setText("-")
        self.meta_version.setText("-")
        self.meta_last.setText("-")
        self.meta_created_by.setText("AI")
        self.set_validation(True, "AI Prompt를 생성했습니다. 확인 후 저장해주세요.")
        self._set_editor_enabled(True)

    def set_generating(self, busy: bool) -> None:
        """AI 생성 중 버튼 상태를 표시합니다."""
        self.ai_button.setEnabled(not busy)
        self.ai_button.setText("AI Prompt 생성 중..." if busy else "✦ AI로 Prompt 생성")
