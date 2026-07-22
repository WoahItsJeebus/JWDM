"""Dialogs for source selection, preview review, and history display."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jwdm.persistence.history import HistoryOperation
from jwdm.pipeline.models import PlanItem, PlanItemStatus, ScanPlan, ScanRoot
from jwdm.services.destinations import (
    CategoryValidationError,
    destination_for,
    resolve_collision,
    validate_category,
)


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


class FolderSelectionDialog(QDialog):
    """Collect one or more manual sources and a recursion choice for each."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select folders to organize")
        self.resize(720, 360)

        explanation = QLabel(
            "Add one or more folders. Enable subfolders only where you want a recursive scan."
        )
        explanation.setWordWrap(True)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Source folder", "Include subfolders"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        add_button = QPushButton("Add folder…")
        add_button.clicked.connect(self._add_folder)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self._remove_selected)
        controls = QHBoxLayout()
        controls.addWidget(add_button)
        controls.addWidget(remove_button)
        controls.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Scan and preview")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addWidget(self.table)
        layout.addLayout(controls)
        layout.addWidget(buttons)

    def scan_roots(self) -> tuple[ScanRoot, ...]:
        roots: list[ScanRoot] = []
        for row in range(self.table.rowCount()):
            path_item = self.table.item(row, 0)
            recursive = self.table.cellWidget(row, 1)
            if path_item is not None and isinstance(recursive, QCheckBox):
                roots.append(ScanRoot(Path(path_item.text()), recursive.isChecked()))
        return tuple(roots)

    def accept(self) -> None:
        if not self.scan_roots():
            QMessageBox.warning(self, "No folders selected", "Add at least one source folder.")
            return
        super().accept()

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a source folder")
        if not folder:
            return
        identity = os.path.normcase(str(Path(folder).resolve(strict=False)))
        existing = {
            os.path.normcase(str(Path(self.table.item(row, 0).text()).resolve(strict=False)))
            for row in range(self.table.rowCount())
            if self.table.item(row, 0) is not None
        }
        if identity in existing:
            QMessageBox.information(self, "Already selected", "That folder is already listed.")
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        path_item = QTableWidgetItem(folder)
        path_item.setFlags(path_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, path_item)
        recursive = QCheckBox()
        recursive.setToolTip("Scan all ordinary subfolders")
        self.table.setCellWidget(row, 1, recursive)

    def _remove_selected(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            self.table.removeRow(selected_rows[0].row())


class ReviewDialog(QDialog):
    """Show the read-only plan and collect explicit per-file approvals."""

    def __init__(self, plan: ScanPlan, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plan = plan
        self._items = list(plan.items)
        self.setWindowTitle("Review organization plan")
        self.resize(1100, 620)

        summary = QLabel(
            f"{len(plan.items)} items • {_format_bytes(plan.total_bytes)} • "
            f"{len(plan.ready_items)} ready • {len(plan.review_items)} need review • "
            f"{len(plan.issues)} scan issues"
        )
        summary.setWordWrap(True)

        self.table = QTableWidget(len(self._items), 7)
        self.table.setHorizontalHeaderLabels(
            ["Move", "Status", "File", "Category", "Destination", "Size", "Reason"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(48)
        header.setStretchLastSection(False)
        for column, width in enumerate((52, 110, 280, 130, 360, 90, 360)):
            self.table.setColumnWidth(column, width)
        for row in range(len(self._items)):
            self._render_row(row)

        approve_all = QPushButton("Approve all ready")
        approve_all.clicked.connect(self._approve_all_ready)
        change_category = QPushButton("Set category for selected…")
        change_category.clicked.connect(self._change_category)
        controls = QHBoxLayout()
        controls.addWidget(approve_all)
        controls.addWidget(change_category)
        controls.addStretch()

        if plan.issues:
            issue_text = "\n".join(f"• {issue.path}: {issue.message}" for issue in plan.issues[:5])
            issues = QLabel(f"Scan issues (nothing at these paths will move):\n{issue_text}")
            issues.setWordWrap(True)
        else:
            issues = QLabel("No scan-access issues were found.")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Move approved files")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.table)
        layout.addLayout(controls)
        layout.addWidget(issues)
        layout.addWidget(buttons)

    def selected_items(self) -> tuple[PlanItem, ...]:
        approved: list[PlanItem] = []
        for row, item in enumerate(self._items):
            check_item = self.table.item(row, 0)
            if (
                item.status is PlanItemStatus.READY
                and check_item is not None
                and check_item.checkState() == Qt.CheckState.Checked
            ):
                approved.append(item)
        return tuple(approved)

    def accept(self) -> None:
        if not self.selected_items():
            QMessageBox.warning(
                self, "Nothing approved", "Select at least one ready file to move."
            )
            return
        super().accept()

    def _render_row(self, row: int) -> None:
        item = self._items[row]
        approval = QTableWidgetItem()
        approval.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        approval.setCheckState(
            Qt.CheckState.Checked
            if item.status is PlanItemStatus.READY
            else Qt.CheckState.Unchecked
        )
        if item.status is not PlanItemStatus.READY:
            approval.setFlags(Qt.ItemFlag.NoItemFlags)
        self.table.setItem(row, 0, approval)

        source_display = self._relative_path(item.source, item.source_root)
        destination_display = (
            self._relative_path(item.proposed_destination, self._plan.library_root)
            if item.proposed_destination is not None
            else "—"
        )
        values = (
            (item.status.value, item.status.value),
            (source_display, str(item.source)),
            (item.category or "—", item.category or "No category assigned"),
            (
                destination_display,
                str(item.proposed_destination)
                if item.proposed_destination is not None
                else "No destination assigned",
            ),
            (_format_bytes(item.size), f"{item.size:,} bytes"),
            (item.reason, item.reason),
        )
        for column, (value, tooltip) in enumerate(values, start=1):
            cell = QTableWidgetItem(value)
            cell.setToolTip(tooltip)
            self.table.setItem(row, column, cell)

    @staticmethod
    def _relative_path(path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    def _approve_all_ready(self) -> None:
        for row, item in enumerate(self._items):
            if item.status is PlanItemStatus.READY:
                self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)

    def _change_category(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "No item selected", "Select one file first.")
            return
        row = selected[0].row()
        current = self._items[row]
        category, accepted = QInputDialog.getText(
            self,
            "Set destination category",
            "Category path inside the library (for example, Documents or Blender/Projects):",
            text=current.category or "",
        )
        if not accepted:
            return
        try:
            safe_category = validate_category(category)
            reserved = {
                os.path.normcase(str(item.proposed_destination.resolve(strict=False)))
                for index, item in enumerate(self._items)
                if index != row and item.proposed_destination is not None
            }
            base = destination_for(self._plan.library_root, safe_category, current.source.name)
            proposed, collision = resolve_collision(base, reserved)
        except CategoryValidationError as error:
            QMessageBox.warning(self, "Invalid category", str(error))
            return
        self._items[row] = replace(
            current,
            status=PlanItemStatus.READY,
            category=safe_category,
            confidence="user",
            reason="Category selected during review",
            proposed_destination=proposed,
            collision_behavior=collision,
        )
        self._render_row(row)
        self.table.selectRow(row)


class HistoryDialog(QDialog):
    def __init__(
        self, operations: tuple[HistoryOperation, ...], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Move history")
        self.resize(950, 420)

        table = QTableWidget(len(operations), 5)
        table.setHorizontalHeaderLabels(["Time", "Status", "Source", "Destination", "Category"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for row, operation in enumerate(reversed(operations)):
            values = (
                operation.planned_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                operation.status.value.replace("_", " ").title(),
                str(operation.source),
                str(operation.destination),
                operation.category,
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(table)
        layout.addWidget(buttons)
