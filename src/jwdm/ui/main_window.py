"""Phase 0 main window shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jwdm.ui.icons import build_application_icon


class MainWindow(QMainWindow):
    """Minimal window proving the desktop application foundation."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle("JWDM")
        self.setWindowIcon(build_application_icon())
        self.setMinimumSize(520, 320)

        title = QLabel("JWDM")
        title.setObjectName("applicationTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = title.font()
        title_font.setPointSize(28)
        title_font.setBold(True)
        title.setFont(title_font)

        subtitle = QLabel("Jeebus' Windows Download Manager")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        phase = QLabel("Phase 0 foundation is running.")
        phase.setAlignment(Qt.AlignmentFlag.AlignCenter)

        organize_button = QPushButton("Organize")
        organize_button.setObjectName("organizeButton")
        organize_button.setMinimumHeight(52)
        organize_button.setEnabled(False)
        organize_button.setToolTip("Folder organization will be added in Phase 1.")

        scope_note = QLabel("Organizer features are intentionally unavailable in this build.")
        scope_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scope_note.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(16)
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(12)
        layout.addWidget(phase)
        layout.addWidget(organize_button)
        layout.addWidget(scope_note)
        layout.addStretch()

        content = QWidget()
        content.setLayout(layout)
        self.setCentralWidget(content)

    def bring_to_front(self) -> None:
        """Show and focus the existing main window."""

        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

