"""GUI 전용 실행 진입점입니다. ``python -m script.gui.app``으로 실행합니다."""

from pathlib import Path
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtWidgets import QApplication

from script.gui.main_window import MainWindow
from script.gui.styles import get_stylesheet


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Local Translate Chat")
    app.setStyleSheet(get_stylesheet())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
