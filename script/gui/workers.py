"""향후 backend 연결용 Worker 뼈대입니다.

이번 단계에서는 실제 Translator / Gemini / Ollama 호출을 연결하지 않습니다.
시간이 걸리는 작업은 main thread가 아닌 QThread + QObject Worker로 붙일 수
있도록 signal 구조만 미리 정의합니다.
"""

from PySide6.QtCore import QObject, Signal


class TranslationWorkerSignals(QObject):
    """Translator.translate_all 진행 상황을 GUI로 전달하는 signal 모음입니다."""

    progress = Signal(int)  # 0-100
    current_file = Signal(str)
    status = Signal(str)
    finished = Signal(object)  # TranslationSummary (backend 타입, 미연결 단계에서는 dict 가능)
    failed = Signal(str)


class ChatWorkerSignals(QObject):
    """ChatEngine.chat 비동기 호출용 signal 모음입니다."""

    answer_ready = Signal(str)
    failed = Signal(str)


class PromptWorkerSignals(QObject):
    """Prompt 생성/분석 같은 느린 작업용 signal 모음입니다."""

    draft_ready = Signal(str)
    failed = Signal(str)
