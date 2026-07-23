"""Responsive smooth scrolling for JWDM's page-style viewports."""

from __future__ import annotations

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget


class SmoothScrollArea(QScrollArea):
    """Ease discrete wheel notches while preserving native precision scrolling."""

    WHEEL_ANIMATION_DURATION_MS = 170

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wheel_target = 0
        self._wheel_animation = QPropertyAnimation(
            self.verticalScrollBar(), b"value", self
        )
        self._wheel_animation.setDuration(self.WHEEL_ANIMATION_DURATION_MS)
        self._wheel_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.verticalScrollBar().sliderPressed.connect(self._cancel_wheel_animation)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Animate mouse-wheel steps and leave pixel-based touchpads native."""

        if not event.pixelDelta().isNull():
            self._cancel_wheel_animation()
            super().wheelEvent(event)
            return

        angle = event.angleDelta().y()
        scroll_bar = self.verticalScrollBar()
        if angle == 0 or scroll_bar.maximum() <= scroll_bar.minimum():
            super().wheelEvent(event)
            return

        scroll_lines = max(QApplication.wheelScrollLines(), 1)
        distance = round(
            -angle / 120 * scroll_lines * max(scroll_bar.singleStep(), 1)
        )
        if distance == 0:
            super().wheelEvent(event)
            return

        if (
            self._wheel_animation.state()
            is QAbstractAnimation.State.Running
        ):
            start_target = self._wheel_target
        else:
            start_target = scroll_bar.value()
        target = max(
            scroll_bar.minimum(),
            min(scroll_bar.maximum(), start_target + distance),
        )
        self._wheel_target = target
        self._wheel_animation.stop()
        self._wheel_animation.setStartValue(scroll_bar.value())
        self._wheel_animation.setEndValue(target)
        self._wheel_animation.start()
        event.accept()

    def _cancel_wheel_animation(self) -> None:
        self._wheel_animation.stop()
        self._wheel_target = self.verticalScrollBar().value()
