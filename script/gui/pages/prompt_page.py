"""Prompt 설정 화면입니다. 첨부 이미지 3번을 기준으로 구현합니다."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from script.gui.widgets import Card, muted_label, section_header


class PromptListCard(QFrame):
    """저장된 Prompt 1행 카드입니다."""

    def __init__(
        self,
        title: str,
        source: str,
        target: str,
        doc_type: str,
        updated: str,
        selected: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PromptCard")
        self.setProperty("selected", selected)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        name = QLabel(title)
        name.setStyleSheet("font-weight: 800;")
        title_row.addWidget(name, 1)
        if selected:
            dot = QLabel("●")
            dot.setStyleSheet("color: #2F6FED;")
            title_row.addWidget(dot)
        layout.addLayout(title_row)

        meta_row = QHBoxLayout()
        meta_row.addWidget(self._chip(source))
        arrow = QLabel("→")
        arrow.setObjectName("TinyMuted")
        meta_row.addWidget(arrow)
        meta_row.addWidget(self._chip(target))
        meta_row.addWidget(self._chip(doc_type))
        meta_row.addStretch(1)
        layout.addLayout(meta_row)

        updated_label = QLabel(f"최근 수정 {updated}")
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

    new_requested = Signal()
    save_requested = Signal()
    cancel_requested = Signal()
    delete_requested = Signal()
    import_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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
        import_btn.clicked.connect(self.import_requested.emit)
        export_btn = QPushButton("⤓  내보내기")
        export_btn.setObjectName("SecondaryButton")
        export_btn.clicked.connect(self.export_requested.emit)
        new_btn = QPushButton("+  새 Prompt 생성")
        new_btn.setObjectName("PrimaryButton")
        new_btn.clicked.connect(self.new_requested.emit)
        header.addWidget(import_btn)
        header.addWidget(export_btn)
        header.addWidget(new_btn)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)
        body.addWidget(self._build_list_card(), 2)
        body.addWidget(self._build_editor_card(), 3)

    # ---------- 좌측 ----------
    def _build_list_card(self) -> Card:
        card = Card()
        top = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("저장된 Prompt")
        title.setObjectName("SectionTitle")
        sub = QLabel("총 4개 · 필터 결과 4개")
        sub.setObjectName("TinyMuted")
        title_col.addWidget(title)
        title_col.addWidget(sub)
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
        self.filter_source.addItems(["Japanese", "English", "Korean"])
        src_col.addWidget(self.filter_source)
        tgt_col = QVBoxLayout()
        tgt_col.addWidget(QLabel("목표 언어"))
        self.filter_target = QComboBox()
        self.filter_target.addItems(["Korean", "English"])
        tgt_col.addWidget(self.filter_target)
        row1.addLayout(src_col, 1)
        row1.addLayout(tgt_col, 1)
        filter_grid.addLayout(row1)
        doc_col = QVBoxLayout()
        doc_col.addWidget(QLabel("문서 유형"))
        self.filter_doc = QComboBox()
        self.filter_doc.addItems(["전체 · novel / game_dialogue", "novel", "game_dialogue", "game_ui"])
        doc_col.addWidget(self.filter_doc)
        filter_grid.addLayout(doc_col)
        card.inner.addLayout(filter_grid)

        path = QLabel("Japanese  →  Korean  →  문서 유형별 4개")
        path.setObjectName("Muted")
        path.setStyleSheet("background: #F7F9FC; border-radius: 6px; padding: 6px 8px;")
        card.inner.addWidget(path)

        for title, src, tgt, doc, updated, selected in [
            ("게임 대사 · 감정 보존 v3", "Japanese", "Korean", "game_dialogue", "2026.09.27", True),
            ("비주얼 노벨 · 서술체", "Japanese", "Korean", "novel", "2026.09.21", False),
            ("게임 UI · 짧은 문구", "Japanese", "Korean", "game_ui", "2026.09.18", False),
            ("아이템 및 스킬 설명", "Japanese", "Korean", "item_description", "2026.09.11", False),
        ]:
            card.inner.addWidget(PromptListCard(title, src, tgt, doc, updated, selected))
        card.inner.addStretch(1)
        return card

    # ---------- 우측 ----------
    def _build_editor_card(self) -> Card:
        card = Card()

        top = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel("게임 대사 · 감정 보존 v3")
        title.setStyleSheet("font-size: 15px; font-weight: 800;")
        sub = QLabel("Japanese  →  Korean / game_dialogue")
        sub.setObjectName("Muted")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        top.addLayout(title_col, 1)
        ai_btn = QPushButton("✦ AI로 Prompt 생성")
        ai_btn.setObjectName("SecondaryButton")
        edit_btn = QPushButton("✎ 수정")
        edit_btn.setObjectName("PrimaryButton")
        del_btn = QPushButton("🗑 삭제")
        del_btn.setObjectName("DangerButton")
        del_btn.clicked.connect(self.delete_requested.emit)
        top.addWidget(ai_btn)
        top.addWidget(edit_btn)
        top.addWidget(del_btn)
        card.inner.addLayout(top)
        self.title_label = title
        self.edit_button = edit_btn
        self.ai_button = ai_btn

        form = QHBoxLayout()
        self.source_combo = self._labeled_combo(form, "원본 언어", ["Japanese (ja)", "English (en)", "Korean (ko)"])
        self.target_combo = self._labeled_combo(form, "목표 언어", ["Korean (ko)", "English (en)", "Japanese (ja)"])
        self.doc_combo = self._labeled_combo(form, "문서 유형", ["game_dialogue", "novel", "game_ui"])
        card.inner.addLayout(form)

        edit_head = QHBoxLayout()
        edit_head.addWidget(QLabel("Prompt 내용 편집"))
        edit_head.addStretch(1)
        edit_head.addWidget(muted_label("1,284자 · 자동 저장됨 10:38", "TinyMuted"))
        card.inner.addLayout(edit_head)

        self.editor = QTextEdit()
        self.editor.setPlainText(
            "당신은 일본어 게임 대사를 한국어로 현지화하는 전문 번역가입니다.\n\n"
            "다음 원칙을 반드시 지켜 번역하세요.\n"
            "1. 원문의 감정, 캐릭터 성격, 관계성을 우선하여 자연스러운 한국어 대사로 번역합니다.\n"
            "2. 직역투를 피하고 실제 한국어 화자가 말하는 호흡과 어순을 사용합니다.\n"
            "3. 고용량식과 반말 용어는 채팅용 용어집을 따르고, 문서 전체에서 일관되게 유지합니다.\n"
            "4. 존댓말/반말, 호칭, 말버릇을 임의로 바꾸지 않습니다.\n"
            "5. 변수 표기와 태그(예: {player_name}, <color>)는 번역하거나 삭제하지 않습니다.\n\n"
            "[출력 형식]\n"
            "번역문만 출력하며 설명이나 미괄표를 추가하지 않습니다."
        )
        self.editor.setMinimumHeight(260)
        card.inner.addWidget(self.editor, 1)

        bottom = QHBoxLayout()
        self.validation_label = QLabel("✔  필수 변수와 출력 형식이 유효합니다.")
        self.validation_label.setStyleSheet("color: #188038; font-size: 12px;")
        bottom.addWidget(self.validation_label, 1)
        cancel_btn = QPushButton("변경 취소")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.clicked.connect(self.cancel_requested.emit)
        save_btn = QPushButton("🖫  변경사항 저장")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.save_requested.emit)
        bottom.addWidget(cancel_btn)
        bottom.addWidget(save_btn)
        card.inner.addLayout(bottom)

        stats = QHBoxLayout()
        stats.addWidget(muted_label("사용 횟수", "TinyMuted"))
        stats.addStretch(1)
        stats.addWidget(QLabel("24회"))
        stats.addStretch(2)
        stats.addWidget(muted_label("버전", "TinyMuted"))
        stats.addStretch(1)
        stats.addWidget(QLabel("v3"))
        card.inner.addLayout(stats)
        stats2 = QHBoxLayout()
        stats2.addWidget(muted_label("마지막 사용", "TinyMuted"))
        stats2.addStretch(1)
        stats2.addWidget(QLabel("2026.09.29"))
        stats2.addStretch(2)
        stats2.addWidget(muted_label("생성 방식", "TinyMuted"))
        stats2.addStretch(1)
        stats2.addWidget(QLabel("AI 초안 + 직접 수정"))
        card.inner.addLayout(stats2)
        return card

    @staticmethod
    def _labeled_combo(parent_layout, label: str, items: list[str]) -> QComboBox:
        col = QVBoxLayout()
        col.addWidget(QLabel(label))
        combo = QComboBox()
        combo.addItems(items)
        col.addWidget(combo)
        parent_layout.addLayout(col, 1)
        return combo

    # ---------- backend 연결 지점 (현재는 UI 상태만 변경) ----------
    def set_validation(self, ok: bool, message: str) -> None:
        self.validation_label.setText(message)
        color = "#188038" if ok else "#D93025"
        self.validation_label.setStyleSheet(f"color: {color}; font-size: 12px;")

    def set_editor_text(self, text: str) -> None:
        self.editor.setPlainText(text)

    def editor_text(self) -> str:
        return self.editor.toPlainText()
