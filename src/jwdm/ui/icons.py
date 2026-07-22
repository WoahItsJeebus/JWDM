"""Small code-generated placeholder icon used during Phase 0."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap


def build_application_icon() -> QIcon:
    """Create a temporary JWDM icon until final artwork is selected."""

    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#2563eb"))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.GlobalColor.white)
    font = QFont("Segoe UI", 34)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "J")
    painter.end()

    return QIcon(pixmap)

