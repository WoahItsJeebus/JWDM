"""Application settings and extension-rule editors."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from jwdm.config import (
    AppSettings,
    ConfidencePolicy,
    ExtensionRule,
    RuleAction,
    normalize_extension,
)
from jwdm.services.destinations import CategoryValidationError, validate_category


class SettingsDialog(QDialog):
    """Edit durable application behavior without performing filesystem work."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._initial = settings
        self.setWindowTitle("JWDM Settings")
        self.resize(650, 520)

        self.start_with_windows = QCheckBox("Start JWDM with Windows")
        self.start_with_windows.setObjectName("startWithWindows")
        self.start_with_windows.setChecked(settings.start_with_windows)
        self.launch_minimized = QCheckBox("Launch minimized to the system tray")
        self.launch_minimized.setObjectName("launchMinimized")
        self.launch_minimized.setChecked(settings.launch_minimized)
        self.minimize_to_tray = QCheckBox("Minimize to tray when the window is closed")
        self.minimize_to_tray.setObjectName("minimizeToTray")
        self.minimize_to_tray.setChecked(settings.minimize_to_tray)
        self.start_automatic = QCheckBox("Start automatic organization when JWDM launches")
        self.start_automatic.setObjectName("startAutomatic")
        self.start_automatic.setChecked(settings.start_automatic)

        general = QWidget()
        general_layout = QVBoxLayout(general)
        general_layout.addWidget(self.start_with_windows)
        general_layout.addWidget(self.launch_minimized)
        general_layout.addWidget(self.minimize_to_tray)
        general_layout.addWidget(self.start_automatic)
        general_layout.addStretch()

        self.confidence_policy = QComboBox()
        self.confidence_policy.setObjectName("confidencePolicy")
        self.confidence_policy.addItem(
            "Move recognized files after readiness passes",
            ConfidencePolicy.MOVE_RECOGNIZED.value,
        )
        self.confidence_policy.addItem(
            "Require review for every automatic candidate",
            ConfidencePolicy.REVIEW_ALL.value,
        )
        policy_index = self.confidence_policy.findData(settings.confidence_policy.value)
        self.confidence_policy.setCurrentIndex(max(policy_index, 0))
        self.process_existing = QCheckBox(
            "Process top-level files already present when automatic mode starts"
        )
        self.process_existing.setObjectName("processExisting")
        self.process_existing.setChecked(settings.process_existing_on_start)
        automation_note = QLabel(
            "Unknown formats always remain in place for review. Readiness and access checks "
            "still apply under every policy."
        )
        automation_note.setWordWrap(True)

        automation = QWidget()
        automation_layout = QFormLayout(automation)
        automation_layout.addRow("Automatic confidence policy", self.confidence_policy)
        automation_layout.addRow(self.process_existing)
        automation_layout.addRow(automation_note)

        self.exclusions = QListWidget()
        self.exclusions.setObjectName("exclusionList")
        for path in settings.exclusions:
            self.exclusions.addItem(str(path))
        add_exclusion = QPushButton("Add folder…")
        add_exclusion.clicked.connect(self._add_exclusion)
        remove_exclusion = QPushButton("Remove selected")
        remove_exclusion.clicked.connect(self._remove_exclusion)
        exclusion_actions = QHBoxLayout()
        exclusion_actions.addWidget(add_exclusion)
        exclusion_actions.addWidget(remove_exclusion)
        exclusion_actions.addStretch()
        exclusions_note = QLabel(
            "Excluded folders and their contents are never moved automatically and are "
            "omitted from recursive manual scans."
        )
        exclusions_note.setWordWrap(True)

        exclusions = QWidget()
        exclusions_layout = QVBoxLayout(exclusions)
        exclusions_layout.addWidget(exclusions_note)
        exclusions_layout.addWidget(self.exclusions)
        exclusions_layout.addLayout(exclusion_actions)

        self.downloads_path = QLabel("Current Downloads location: unavailable")
        self.downloads_path.setObjectName("downloadsCurrentPath")
        self.downloads_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.downloads_detail = QLabel(
            "Windows Downloads relocation status is unavailable."
        )
        self.downloads_detail.setObjectName("downloadsRelocationStatus")
        self.downloads_detail.setWordWrap(True)
        self.relocate_downloads_button = QPushButton("Relocate Windows Downloads\u2026")
        self.relocate_downloads_button.setObjectName("relocateDownloadsButton")
        self.relocate_downloads_button.setEnabled(False)
        self.restore_downloads_button = QPushButton("Restore recorded location")
        self.restore_downloads_button.setObjectName("restoreDownloadsButton")
        self.restore_downloads_button.setEnabled(False)
        downloads_actions = QHBoxLayout()
        downloads_actions.addWidget(self.relocate_downloads_button)
        downloads_actions.addWidget(self.restore_downloads_button)
        downloads_actions.addStretch()
        downloads_note = QLabel(
            "Relocation changes the per-user Windows known-folder path. JWDM does not "
            "move files already in either folder; organize them separately through the "
            "normal preview workflow. Some applications keep their own download path."
        )
        downloads_note.setWordWrap(True)
        downloads = QWidget()
        downloads_layout = QVBoxLayout(downloads)
        downloads_layout.addWidget(self.downloads_path)
        downloads_layout.addWidget(self.downloads_detail)
        downloads_layout.addLayout(downloads_actions)
        downloads_layout.addWidget(downloads_note)
        downloads_layout.addStretch()

        tabs = QTabWidget()
        tabs.addTab(general, "General")
        tabs.addTab(automation, "Automation")
        tabs.addTab(exclusions, "Exclusions")
        tabs.addTab(downloads, "Windows Downloads")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def selected_settings(self) -> AppSettings:
        exclusions = tuple(
            Path(self.exclusions.item(index).text())
            for index in range(self.exclusions.count())
        )
        return replace(
            self._initial,
            start_with_windows=self.start_with_windows.isChecked(),
            launch_minimized=self.launch_minimized.isChecked(),
            minimize_to_tray=self.minimize_to_tray.isChecked(),
            start_automatic=self.start_automatic.isChecked(),
            process_existing_on_start=self.process_existing.isChecked(),
            confidence_policy=ConfidencePolicy(self.confidence_policy.currentData()),
            exclusions=exclusions,
        )

    def set_downloads_status(
        self,
        current_path: Path | None,
        detail: str,
        *,
        can_relocate: bool,
        can_restore: bool,
    ) -> None:
        current = str(current_path) if current_path is not None else "unavailable"
        self.downloads_path.setText(f"Current Downloads location: {current}")
        self.downloads_detail.setText(detail)
        self.relocate_downloads_button.setEnabled(can_relocate)
        self.restore_downloads_button.setEnabled(can_restore)

    def set_base_settings(self, settings: AppSettings) -> None:
        """Keep path-only controller changes when the dialog is later saved."""

        self._initial = settings

    def _add_exclusion(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose folder to exclude")
        if not selected:
            return
        candidate = Path(selected).resolve(strict=False)
        existing = {
            Path(self.exclusions.item(index).text()).resolve(strict=False)
            for index in range(self.exclusions.count())
        }
        if candidate not in existing:
            self.exclusions.addItem(str(candidate))

    def _remove_exclusion(self) -> None:
        for item in self.exclusions.selectedItems():
            self.exclusions.takeItem(self.exclusions.row(item))


class DownloadsRelocationDialog(QDialog):
    """Collect an explicit target and confirmation for known-folder relocation."""

    def __init__(self, current_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Relocate Windows Downloads")
        self.resize(680, 330)

        explanation = QLabel(
            "Choose an existing local folder for future Windows downloads. This changes "
            "the per-user Windows known-folder path only. Existing files remain exactly "
            "where they are and are not copied, moved, merged, or deleted."
        )
        explanation.setWordWrap(True)
        current = QLineEdit(str(current_path))
        current.setReadOnly(True)
        current.setObjectName("currentDownloadsPath")
        self.target = QLineEdit()
        self.target.setReadOnly(True)
        self.target.setObjectName("proposedDownloadsPath")
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target, stretch=1)
        target_row.addWidget(browse)

        form = QFormLayout()
        form.addRow("Current Downloads", current)
        form.addRow("Proposed Downloads", target_row)

        self.use_as_incoming = QCheckBox(
            "Use the relocated Downloads folder as JWDM's incoming folder"
        )
        self.use_as_incoming.setObjectName("useRelocatedDownloadsAsIncoming")
        self.use_as_incoming.setChecked(True)
        warning = QLabel(
            "Applications with their own download preference may continue using their "
            "configured path. You can restore the recorded original location from Settings."
        )
        warning.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Relocate Downloads"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addLayout(form)
        layout.addWidget(self.use_as_incoming)
        layout.addWidget(warning)
        layout.addStretch()
        layout.addWidget(buttons)

    @property
    def target_path(self) -> Path | None:
        value = self.target.text().strip()
        return Path(value) if value else None

    def accept(self) -> None:
        if self.target_path is None:
            QMessageBox.warning(
                self,
                "No destination selected",
                "Choose the proposed Windows Downloads folder first.",
            )
            return
        super().accept()

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose the new Windows Downloads folder"
        )
        if selected:
            self.target.setText(selected)


class ExtensionRuleDialog(QDialog):
    """Collect and validate one extension rule."""

    def __init__(
        self, rule: ExtensionRule | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._original = rule
        self._validated_rule: ExtensionRule | None = None
        self.setWindowTitle("Extension rule")

        self.extension = QLineEdit(rule.extension if rule else "")
        self.extension.setObjectName("ruleExtension")
        self.extension.setPlaceholderText(".pdf")
        self.action = QComboBox()
        self.action.setObjectName("ruleAction")
        self.action.addItem("Route to category", RuleAction.ROUTE.value)
        self.action.addItem("Require review", RuleAction.REVIEW.value)
        self.action.addItem("Ignore", RuleAction.IGNORE.value)
        if rule is not None:
            self.action.setCurrentIndex(self.action.findData(rule.action.value))
        self.category = QLineEdit(rule.category or "" if rule else "")
        self.category.setObjectName("ruleCategory")
        self.category.setPlaceholderText("Documents or Blender/Projects")
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(rule.enabled if rule else True)
        self.priority = QSpinBox()
        self.priority.setRange(0, 10_000)
        self.priority.setValue(rule.priority if rule else 100)
        self.action.currentIndexChanged.connect(self._update_category_state)
        self._update_category_state()

        form = QFormLayout()
        form.addRow("Extension", self.extension)
        form.addRow("Action", self.action)
        form.addRow("Category", self.category)
        form.addRow("Priority", self.priority)
        form.addRow(self.enabled)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def rule(self) -> ExtensionRule:
        if self._validated_rule is None:
            raise RuntimeError("Rule is only available after the dialog is accepted.")
        return self._validated_rule

    def accept(self) -> None:
        try:
            extension = normalize_extension(self.extension.text())
            action = RuleAction(self.action.currentData())
            category = (
                validate_category(self.category.text())
                if action is RuleAction.ROUTE
                else None
            )
        except (ValueError, CategoryValidationError) as error:
            QMessageBox.warning(self, "Invalid rule", str(error))
            return
        self._validated_rule = ExtensionRule(
            rule_id=self._original.rule_id if self._original else None,
            extension=extension,
            action=action,
            category=category,
            enabled=self.enabled.isChecked(),
            priority=self.priority.value(),
        )
        super().accept()

    def _update_category_state(self) -> None:
        self.category.setEnabled(self.action.currentData() == RuleAction.ROUTE.value)


class RulesDialog(QDialog):
    """Add, edit, and remove the complete ordered basic-rule set."""

    def __init__(
        self, rules: tuple[ExtensionRule, ...], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._rules = list(rules)
        self.setWindowTitle("JWDM Rules")
        self.resize(760, 430)

        explanation = QLabel(
            "User rules are evaluated before JWDM's built-in extension categories. "
            "Lower priority numbers run first."
        )
        explanation.setWordWrap(True)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("rulesTable")
        self.table.setHorizontalHeaderLabels(
            ["Enabled", "Extension", "Action", "Category", "Priority"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )

        add_button = QPushButton("Add…")
        add_button.clicked.connect(self._add_rule)
        edit_button = QPushButton("Edit…")
        edit_button.clicked.connect(self._edit_rule)
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(self._remove_rule)
        actions = QHBoxLayout()
        actions.addWidget(add_button)
        actions.addWidget(edit_button)
        actions.addWidget(remove_button)
        actions.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addWidget(self.table)
        layout.addLayout(actions)
        layout.addWidget(buttons)
        self._render()

    def selected_rules(self) -> tuple[ExtensionRule, ...]:
        return tuple(sorted(self._rules, key=lambda rule: rule.priority))

    def _render(self) -> None:
        self.table.setRowCount(len(self._rules))
        labels = {
            RuleAction.ROUTE: "Route",
            RuleAction.REVIEW: "Review",
            RuleAction.IGNORE: "Ignore",
        }
        for row, rule in enumerate(self._rules):
            values = (
                "Yes" if rule.enabled else "No",
                rule.extension,
                labels[rule.action],
                rule.category or "—",
                str(rule.priority),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)

    def _add_rule(self) -> None:
        editor = ExtensionRuleDialog(parent=self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        rule = editor.rule()
        if self._duplicate(rule.extension):
            QMessageBox.warning(self, "Duplicate rule", f"A rule for {rule.extension} exists.")
            return
        self._rules.append(rule)
        self._render()

    def _edit_rule(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        editor = ExtensionRuleDialog(self._rules[row], self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        rule = editor.rule()
        if self._duplicate(rule.extension, except_row=row):
            QMessageBox.warning(self, "Duplicate rule", f"A rule for {rule.extension} exists.")
            return
        self._rules[row] = rule
        self._render()
        self.table.selectRow(row)

    def _remove_rule(self) -> None:
        row = self._selected_row()
        if row is not None:
            del self._rules[row]
            self._render()

    def _selected_row(self) -> int | None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "No rule selected", "Select one rule first.")
            return None
        return selected[0].row()

    def _duplicate(self, extension: str, except_row: int | None = None) -> bool:
        return any(
            index != except_row and rule.extension.casefold() == extension.casefold()
            for index, rule in enumerate(self._rules)
        )
