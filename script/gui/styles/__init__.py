"""GUI 공통 디자인 토큰과 QSS를 제공합니다."""

COLORS = {
    "app_bg": "#EEF2F7",
    "sidebar_bg": "#FFFFFF",
    "card_bg": "#FFFFFF",
    "border": "#E2E8F0",
    "border_soft": "#EAF0F6",
    "text_main": "#1B2430",
    "text_sub": "#6B7A90",
    "text_muted": "#94A3B8",
    "primary": "#2F6FED",
    "primary_hover": "#245FD6",
    "primary_soft": "#E8F0FE",
    "primary_border": "#C9DCFD",
    "success": "#188038",
    "success_bg": "#E6F4EA",
    "danger": "#D93025",
    "danger_bg": "#FDECEC",
    "danger_border": "#F5C6C2",
    "stop_bg": "#FDECEA",
    "warning_bg": "#FFF7E6",
    "warning_border": "#F5DFA8",
    "bubble_user": "#2F6FED",
    "bubble_ai": "#FFFFFF",
    "input_bg": "#F7F9FC",
}

FONTS = '"Noto Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif'


def get_stylesheet() -> str:
    """애플리케이션 전역 QSS를 반환합니다."""
    return f"""
* {{
    font-family: {FONTS};
}}
QMainWindow, QWidget#AppRoot {{
    background: {COLORS["app_bg"]};
    color: {COLORS["text_main"]};
    font-size: 13px;
}}
QWidget#Sidebar {{
    background: {COLORS["sidebar_bg"]};
    border-right: 1px solid {COLORS["border"]};
}}
QFrame#Card {{
    background: {COLORS["card_bg"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 12px;
}}
QLabel#PageTitle {{
    font-size: 22px;
    font-weight: 800;
}}
QLabel#PageSubtitle {{
    font-size: 12px;
    color: {COLORS["text_sub"]};
}}
QLabel#SectionTitle {{
    font-size: 14px;
    font-weight: 700;
}}
QLabel#Muted {{
    color: {COLORS["text_sub"]};
    font-size: 12px;
}}
QLabel#TinyMuted {{
    color: {COLORS["text_muted"]};
    font-size: 11px;
}}
QPushButton#SidebarButton {{
    text-align: left;
    border: none;
    border-radius: 8px;
    padding: 9px 10px 9px 14px;
    font-size: 13px;
    color: {COLORS["text_sub"]};
    background: transparent;
}}
QPushButton#SidebarButton:hover {{
    background: #F2F6FC;
}}
QPushButton#SidebarButton[active="true"] {{
    background: {COLORS["primary_soft"]};
    color: {COLORS["primary"]};
    font-weight: 700;
    border-left: 3px solid {COLORS["primary"]};
    padding-left: 11px;
}}
QPushButton#PrimaryButton {{
    background: {COLORS["primary"]};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 9px 14px;
    font-weight: 700;
}}
QPushButton#PrimaryButton:hover {{
    background: {COLORS["primary_hover"]};
}}
QPushButton#PrimaryButton:disabled {{
    background: #A9C2F7;
}}
QPushButton#SecondaryButton {{
    background: white;
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 8px 12px;
    font-weight: 600;
}}
QPushButton#SecondaryButton:hover {{
    border-color: {COLORS["primary_border"]};
    background: #F8FAFF;
}}
QPushButton#DangerButton {{
    background: white;
    border: 1px solid {COLORS["danger_border"]};
    color: {COLORS["danger"]};
    border-radius: 8px;
    padding: 8px 12px;
    font-weight: 700;
}}
QPushButton#StopButton {{
    background: {COLORS["stop_bg"]};
    border: 1px solid {COLORS["danger_border"]};
    color: {COLORS["danger"]};
    border-radius: 8px;
    padding: 9px 14px;
    font-weight: 700;
}}
QPushButton#GhostIconButton {{
    background: transparent;
    border: none;
    color: {COLORS["text_sub"]};
    padding: 6px;
}}
QComboBox, QLineEdit, QTextEdit, QPlainTextEdit {{
    background: white;
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {COLORS["primary_soft"]};
}}
QComboBox:focus, QLineEdit:focus, QTextEdit:focus {{
    border: 1px solid {COLORS["primary"]};
}}
QComboBox QAbstractItemView {{
    background: white;
    border: 1px solid {COLORS["border"]};
    selection-background-color: {COLORS["primary_soft"]};
    selection-color: {COLORS["text_main"]};
}}
QFrame#DropArea {{
    background: #F8FAFF;
    border: 1.5px dashed #B9CCF2;
    border-radius: 10px;
}}
QFrame#FileRow {{
    background: #F3F7FF;
    border: 1px solid {COLORS["primary_border"]};
    border-radius: 8px;
}}
QFrame#FileRowPlain {{
    background: white;
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
}}
QFrame#RecommendBox {{
    background: {COLORS["warning_bg"]};
    border: 1px solid {COLORS["warning_border"]};
    border-radius: 8px;
}}
QFrame#StatusBox {{
    background: {COLORS["input_bg"]};
    border: 1px solid {COLORS["border_soft"]};
    border-radius: 8px;
}}
QFrame#PromptCard {{
    background: white;
    border: 1px solid {COLORS["border"]};
    border-radius: 10px;
}}
QFrame#PromptCard[selected="true"] {{
    border: 1.5px solid {COLORS["primary"]};
    background: #F6F9FF;
}}
QProgressBar {{
    background: #E6ECF5;
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {COLORS["primary"]};
    border-radius: 5px;
}}
QLabel#BadgeGreen {{
    background: {COLORS["success_bg"]};
    color: {COLORS["success"]};
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#BadgeBlue {{
    background: {COLORS["primary_soft"]};
    color: {COLORS["primary"]};
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#ExtCSV {{
    background: {COLORS["primary"]};
    color: white;
    border-radius: 6px;
    padding: 6px 7px;
    font-size: 10px;
    font-weight: 800;
}}
QLabel#ExtJSON {{
    background: #E8EDF3;
    color: {COLORS["text_sub"]};
    border-radius: 6px;
    padding: 6px 6px;
    font-size: 10px;
    font-weight: 800;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
}}
QScrollBar::handle:vertical {{
    background: #CBD5E1;
    border-radius: 4px;
    min-height: 30px;
}}
QCheckBox {{
    spacing: 8px;
}}
"""
