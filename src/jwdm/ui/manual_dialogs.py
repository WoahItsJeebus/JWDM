"""Dialogs for source selection, preview review, and history display."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
from jwdm.services.rule_suggestions import CategoryCorrection, suggested_extension


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


class CategoryCorrectionDialog(QDialog):
    """Collect one reviewed category and an explicit optional rule request."""

    def __init__(self, item: PlanItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = item.source
        self._validated_category: str | None = None
        self.setWindowTitle("Set destination category")

        explanation = QLabel(
            "Choose a category path inside the library, such as Documents or "
            "Blender/Projects."
        )
        explanation.setWordWrap(True)
        self.category = QLineEdit(item.category or "")
        self.category.setObjectName("correctionCategory")
        extension = suggested_extension(item.source)
        if extension is None:
            self.create_rule = QCheckBox("This filename has no supported rule extension")
            self.create_rule.setEnabled(False)
        else:
            self.create_rule = QCheckBox(
                f"Suggested: create or update a rule for future {extension} files"
            )
            self.create_rule.setToolTip(
                "The rule is saved only if you check this option and approve the plan."
            )
        self.create_rule.setObjectName("createCorrectionRule")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addWidget(self.category)
        layout.addWidget(self.create_rule)
        layout.addWidget(buttons)

    def correction(self) -> CategoryCorrection:
        if self._validated_category is None:
            raise RuntimeError("Correction is available only after the dialog is accepted.")
        return CategoryCorrection(
            self._source,
            self._validated_category,
            self.create_rule.isChecked(),
        )

    def accept(self) -> None:
        try:
            self._validated_category = validate_category(self.category.text())
        except CategoryValidationError as error:
            QMessageBox.warning(self, "Invalid category", str(error))
            return
        super().accept()


class ReviewDialog(QDialog):
    """Show the read-only plan and collect explicit per-file approvals."""

    _DESIRED_COLUMN_WIDTHS = (52, 110, 280, 130, 360, 90, 420)
    _MINIMUM_COLUMN_WIDTHS = (48, 75, 105, 80, 130, 65, 180)
    _TABLE_OVERHEAD = 84

    def __init__(self, plan: ScanPlan, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plan = plan
        self._items = list(plan.items)
        self._corrections: dict[Path, CategoryCorrection] = {}
        self.setWindowTitle("Review organization plan")
        self.setMinimumSize(720, 480)

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
        self._fit_to_screen()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        screen = None
        if self.parentWidget() is not None:
            parent = self.parentWidget()
            screen = QGuiApplication.screenAt(
                parent.mapToGlobal(parent.rect().center())
            )
        if screen is None:
            screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            available_width, available_height = 1280, 720
        else:
            available = screen.availableGeometry()
            available_width, available_height = available.width(), available.height()

        maximum_width = max(720, int(available_width * 0.96))
        maximum_height = max(480, int(available_height * 0.9))
        desired_width = sum(self._DESIRED_COLUMN_WIDTHS) + self._TABLE_OVERHEAD
        dialog_width = min(desired_width, maximum_width)
        dialog_height = min(620, maximum_height)
        column_budget = max(0, dialog_width - self._TABLE_OVERHEAD)
        for column, width in enumerate(self._fit_column_widths(column_budget)):
            self.table.setColumnWidth(column, width)
        self.resize(dialog_width, dialog_height)

    @classmethod
    def _fit_column_widths(cls, budget: int) -> tuple[int, ...]:
        desired = cls._DESIRED_COLUMN_WIDTHS
        minimum = cls._MINIMUM_COLUMN_WIDTHS
        if budget >= sum(desired):
            return desired
        if budget <= sum(minimum):
            ratio = budget / sum(minimum) if budget else 0
            return tuple(max(36, int(width * ratio)) for width in minimum)

        available_extra = budget - sum(minimum)
        desired_extra = sum(desired) - sum(minimum)
        widths = [
            floor + int((target - floor) * available_extra / desired_extra)
            for floor, target in zip(minimum, desired, strict=True)
        ]
        remainder = budget - sum(widths)
        priorities = (6, 4, 2, 3, 1, 5, 0)
        for offset in range(remainder):
            widths[priorities[offset % len(priorities)]] += 1
        return tuple(widths)

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

    def corrections(self) -> tuple[CategoryCorrection, ...]:
        return tuple(self._corrections.values())

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
        editor = CategoryCorrectionDialog(current, self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        correction = editor.correction()
        try:
            reserved = {
                os.path.normcase(str(item.proposed_destination.resolve(strict=False)))
                for index, item in enumerate(self._items)
                if index != row and item.proposed_destination is not None
            }
            base = destination_for(
                self._plan.library_root, correction.category, current.source.name
            )
            proposed, collision = resolve_collision(base, reserved)
        except CategoryValidationError as error:
            QMessageBox.warning(self, "Invalid category", str(error))
            return
        self._items[row] = replace(
            current,
            status=PlanItemStatus.READY,
            category=correction.category,
            confidence="user",
            reason=(
                "Category selected during review; extension rule requested"
                if correction.create_rule
                else "Category selected during review"
            ),
            proposed_destination=proposed,
            collision_behavior=collision,
        )
        self._corrections[current.source] = correction
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
