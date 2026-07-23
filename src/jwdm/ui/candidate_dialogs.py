"""Detailed, read-only review UI for automatic candidates."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jwdm.pipeline.candidate import CandidateSnapshot, CandidateState
from jwdm.services.rule_suggestions import suggested_extension


class CandidateReviewDialog(QDialog):
    """Explain one automatic candidate without changing the filesystem."""

    def __init__(
        self, candidate: CandidateSnapshot, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review automatic candidate")
        self.resize(680, 360)
        self._add_rule_requested = False

        explanation = QLabel(
            "JWDM has not bypassed readiness or move safety. Review the classification "
            "details below; creating a rule sends this item through the full readiness "
            "pipeline again."
        )
        explanation.setWordWrap(True)

        form = QFormLayout()
        form.addRow("File", self._value(str(candidate.source_path)))
        form.addRow("Incoming folder", self._value(str(candidate.incoming_root)))
        form.addRow("State", self._value(candidate.state.value))
        form.addRow("Detail", self._value(candidate.detail))
        form.addRow("Suggested category", self._value(candidate.proposed_category or "—"))
        form.addRow("Confidence", self._value(candidate.confidence or "—"))
        form.addRow(
            "Proposed destination",
            self._value(str(candidate.proposed_destination) if candidate.proposed_destination else "—"),
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        extension = suggested_extension(candidate.source_path)
        self.add_rule_button: QPushButton | None = None
        if candidate.state is CandidateState.NEEDS_REVIEW and extension is not None:
            self.add_rule_button = buttons.addButton(
                f"Add rule for {extension}…",
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            self.add_rule_button.setObjectName("candidateAddRule")
            self.add_rule_button.clicked.connect(self._request_rule)

        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addLayout(form)
        layout.addStretch()
        layout.addWidget(buttons)

    @property
    def add_rule_requested(self) -> bool:
        return self._add_rule_requested

    def _request_rule(self) -> None:
        self._add_rule_requested = True
        self.accept()

    @staticmethod
    def _value(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label
