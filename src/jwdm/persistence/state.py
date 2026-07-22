"""SQLite persistence and migrations for Phase 3 application state."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from jwdm.config import (
    AppSettings,
    ConfidencePolicy,
    ExtensionRule,
    RuleAction,
    VolumeBinding,
    normalize_extension,
)
from jwdm.pipeline.candidate import CandidateSnapshot, CandidateState
from jwdm.services.destinations import validate_category

STATE_SCHEMA_VERSION: Final = 2


class StateError(RuntimeError):
    """Persistent state could not be migrated, read, or written safely."""


def default_state_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path(tempfile.gettempdir())
    return base / "JWDM" / "state.db"


def _identity(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


class StateRepository:
    """Own settings, rules, exclusions, and restart-safe candidate paths."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else default_state_path()
        self._lock = threading.RLock()
        self._migrate()

    def settings(self) -> AppSettings:
        with self._lock:
            try:
                with self._connection() as connection:
                    values = dict(connection.execute("SELECT key, value FROM settings"))
                    exclusions = tuple(
                        Path(row[0])
                        for row in connection.execute(
                            "SELECT path FROM exclusions ORDER BY path COLLATE NOCASE"
                        )
                    )
            except (OSError, sqlite3.Error) as error:
                raise StateError(f"Cannot read settings from {self.path}: {error}") from error

        defaults = AppSettings()
        try:
            return AppSettings(
                library_path=self._optional_path(values.get("library_path")),
                incoming_path=self._optional_path(values.get("incoming_path")),
                start_with_windows=self._boolean(
                    values.get("start_with_windows"), defaults.start_with_windows
                ),
                launch_minimized=self._boolean(
                    values.get("launch_minimized"), defaults.launch_minimized
                ),
                minimize_to_tray=self._boolean(
                    values.get("minimize_to_tray"), defaults.minimize_to_tray
                ),
                close_notice_shown=self._boolean(
                    values.get("close_notice_shown"), defaults.close_notice_shown
                ),
                start_automatic=self._boolean(
                    values.get("start_automatic"), defaults.start_automatic
                ),
                process_existing_on_start=self._boolean(
                    values.get("process_existing_on_start"),
                    defaults.process_existing_on_start,
                ),
                confidence_policy=ConfidencePolicy(
                    values.get("confidence_policy", defaults.confidence_policy.value)
                ),
                exclusions=exclusions,
            )
        except ValueError as error:
            raise StateError(f"Settings in {self.path} contain an invalid value: {error}") from error

    def save_settings(self, settings: AppSettings) -> None:
        values = {
            "library_path": str(settings.library_path) if settings.library_path else "",
            "incoming_path": str(settings.incoming_path) if settings.incoming_path else "",
            "start_with_windows": self._encode_boolean(settings.start_with_windows),
            "launch_minimized": self._encode_boolean(settings.launch_minimized),
            "minimize_to_tray": self._encode_boolean(settings.minimize_to_tray),
            "close_notice_shown": self._encode_boolean(settings.close_notice_shown),
            "start_automatic": self._encode_boolean(settings.start_automatic),
            "process_existing_on_start": self._encode_boolean(
                settings.process_existing_on_start
            ),
            "confidence_policy": settings.confidence_policy.value,
        }
        exclusions = self._normalized_exclusions(settings.exclusions)
        with self._lock:
            try:
                with self._connection() as connection:
                    connection.executemany(
                        "INSERT INTO settings(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        values.items(),
                    )
                    connection.execute("DELETE FROM exclusions")
                    connection.executemany(
                        "INSERT INTO exclusions(path, path_identity) VALUES(?, ?)",
                        ((str(path), _identity(path)) for path in exclusions),
                    )
            except (OSError, sqlite3.Error) as error:
                raise StateError(f"Cannot save settings to {self.path}: {error}") from error

    def update_paths(
        self, library_path: Path | None, incoming_path: Path | None
    ) -> AppSettings:
        updated = replace(
            self.settings(), library_path=library_path, incoming_path=incoming_path
        )
        self.save_settings(updated)
        return updated

    def rules(self) -> tuple[ExtensionRule, ...]:
        with self._lock:
            try:
                with self._connection() as connection:
                    rows = connection.execute(
                        "SELECT rule_id, extension, action, category, enabled, priority "
                        "FROM extension_rules ORDER BY priority, rule_id"
                    ).fetchall()
            except (OSError, sqlite3.Error) as error:
                raise StateError(f"Cannot read rules from {self.path}: {error}") from error
        try:
            return tuple(
                ExtensionRule(
                    rule_id=int(row[0]),
                    extension=str(row[1]),
                    action=RuleAction(str(row[2])),
                    category=str(row[3]) if row[3] is not None else None,
                    enabled=bool(row[4]),
                    priority=int(row[5]),
                )
                for row in rows
            )
        except (TypeError, ValueError) as error:
            raise StateError(f"Rules in {self.path} contain an invalid value: {error}") from error

    def replace_rules(self, rules: tuple[ExtensionRule, ...]) -> None:
        normalized: list[ExtensionRule] = []
        extensions: set[str] = set()
        for rule in rules:
            try:
                extension = normalize_extension(rule.extension)
                category = (
                    validate_category(rule.category)
                    if rule.action is RuleAction.ROUTE and rule.category
                    else None
                )
            except ValueError as error:
                raise StateError(f"Invalid extension rule: {error}") from error
            if extension in extensions:
                raise StateError(f"Only one user rule may target {extension}.")
            extensions.add(extension)
            if rule.action is RuleAction.ROUTE and category is None:
                raise StateError(f"Route rule {extension} requires a category.")
            normalized.append(
                replace(rule, extension=extension, category=category, rule_id=None)
            )
        with self._lock:
            try:
                with self._connection() as connection:
                    connection.execute("DELETE FROM extension_rules")
                    connection.executemany(
                        "INSERT INTO extension_rules(extension, action, category, enabled, priority) "
                        "VALUES(?, ?, ?, ?, ?)",
                        (
                            (
                                rule.extension,
                                rule.action.value,
                                rule.category,
                                int(rule.enabled),
                                rule.priority,
                            )
                            for rule in normalized
                        ),
                    )
            except (OSError, sqlite3.Error) as error:
                raise StateError(f"Cannot save rules to {self.path}: {error}") from error

    def save_candidates(
        self, incoming_root: Path, candidates: tuple[CandidateSnapshot, ...]
    ) -> None:
        root_identity = _identity(incoming_root)
        terminal = {
            CandidateState.MOVED,
            CandidateState.FAILED,
            CandidateState.EXCLUDED,
        }
        pending = tuple(candidate for candidate in candidates if candidate.state not in terminal)
        timestamp = datetime.now(UTC).isoformat()
        with self._lock:
            try:
                with self._connection() as connection:
                    connection.execute(
                        "DELETE FROM candidate_queue WHERE incoming_identity = ?",
                        (root_identity,),
                    )
                    connection.executemany(
                        "INSERT INTO candidate_queue("
                        "source_identity, source_path, incoming_identity, incoming_root, "
                        "state, updated_at"
                        ") VALUES(?, ?, ?, ?, ?, ?)",
                        (
                            (
                                _identity(candidate.source_path),
                                str(candidate.source_path),
                                root_identity,
                                str(incoming_root),
                                candidate.state.value,
                                timestamp,
                            )
                            for candidate in pending
                        ),
                    )
            except (OSError, sqlite3.Error) as error:
                raise StateError(f"Cannot persist candidate queue to {self.path}: {error}") from error

    def pending_paths(self, incoming_root: Path) -> tuple[Path, ...]:
        with self._lock:
            try:
                with self._connection() as connection:
                    rows = connection.execute(
                        "SELECT source_path FROM candidate_queue "
                        "WHERE incoming_identity = ? ORDER BY updated_at, source_path",
                        (_identity(incoming_root),),
                    ).fetchall()
            except (OSError, sqlite3.Error) as error:
                raise StateError(f"Cannot restore candidate queue from {self.path}: {error}") from error
        return tuple(Path(str(row[0])) for row in rows)

    def volume_binding(self, role: str) -> VolumeBinding | None:
        with self._lock:
            try:
                with self._connection() as connection:
                    row = connection.execute(
                        "SELECT volume_id, relative_path, last_mount_path, serial_number, "
                        "filesystem, label FROM volume_bindings WHERE role = ?",
                        (role,),
                    ).fetchone()
            except (OSError, sqlite3.Error) as error:
                raise StateError(
                    f"Cannot read volume binding from {self.path}: {error}"
                ) from error
        if row is None:
            return None
        return VolumeBinding(
            volume_id=str(row[0]),
            relative_path=str(row[1]),
            last_mount_path=Path(str(row[2])),
            serial_number=int(row[3]) if row[3] is not None else None,
            filesystem=str(row[4]) if row[4] is not None else None,
            label=str(row[5]) if row[5] is not None else None,
        )

    def save_volume_binding(self, role: str, binding: VolumeBinding) -> None:
        if not role.strip():
            raise StateError("Volume binding role cannot be empty.")
        with self._lock:
            try:
                with self._connection() as connection:
                    connection.execute(
                        "INSERT INTO volume_bindings("
                        "role, volume_id, relative_path, last_mount_path, serial_number, "
                        "filesystem, label) VALUES(?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(role) DO UPDATE SET "
                        "volume_id = excluded.volume_id, "
                        "relative_path = excluded.relative_path, "
                        "last_mount_path = excluded.last_mount_path, "
                        "serial_number = excluded.serial_number, "
                        "filesystem = excluded.filesystem, label = excluded.label",
                        (
                            role,
                            binding.volume_id,
                            binding.relative_path,
                            str(binding.last_mount_path),
                            binding.serial_number,
                            binding.filesystem,
                            binding.label,
                        ),
                    )
            except (OSError, sqlite3.Error) as error:
                raise StateError(
                    f"Cannot save volume binding to {self.path}: {error}"
                ) from error

    def _migrate(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self._connection() as connection:
                    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                    if version > STATE_SCHEMA_VERSION:
                        raise StateError(
                            f"State database schema {version} is newer than this JWDM build "
                            f"supports ({STATE_SCHEMA_VERSION})."
                        )
                    if version == 0:
                        connection.executescript(
                            """
                            CREATE TABLE settings(
                                key TEXT PRIMARY KEY,
                                value TEXT NOT NULL
                            );
                            CREATE TABLE exclusions(
                                path TEXT NOT NULL,
                                path_identity TEXT PRIMARY KEY
                            );
                            CREATE TABLE extension_rules(
                                rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                                extension TEXT NOT NULL UNIQUE COLLATE NOCASE,
                                action TEXT NOT NULL CHECK(action IN ('route', 'review', 'ignore')),
                                category TEXT,
                                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                                priority INTEGER NOT NULL
                            );
                            CREATE TABLE candidate_queue(
                                source_identity TEXT PRIMARY KEY,
                                source_path TEXT NOT NULL,
                                incoming_identity TEXT NOT NULL,
                                incoming_root TEXT NOT NULL,
                                state TEXT NOT NULL,
                                updated_at TEXT NOT NULL
                            );
                            CREATE INDEX candidate_queue_incoming
                                ON candidate_queue(incoming_identity);
                            PRAGMA user_version = 1;
                            """
                        )
                        version = 1
                    if version == 1:
                        connection.executescript(
                            """
                            CREATE TABLE volume_bindings(
                                role TEXT PRIMARY KEY,
                                volume_id TEXT NOT NULL,
                                relative_path TEXT NOT NULL,
                                last_mount_path TEXT NOT NULL,
                                serial_number INTEGER,
                                filesystem TEXT,
                                label TEXT
                            );
                            PRAGMA user_version = 2;
                            """
                        )
                        version = 2
                    if version != STATE_SCHEMA_VERSION:
                        raise StateError(
                            f"State database migration stopped at schema {version}; "
                            f"expected {STATE_SCHEMA_VERSION}."
                        )
            except StateError:
                raise
            except (OSError, sqlite3.Error) as error:
                raise StateError(f"Cannot initialize state database {self.path}: {error}") from error

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _optional_path(value: str | None) -> Path | None:
        return Path(value) if value else None

    @staticmethod
    def _boolean(value: str | None, default: bool) -> bool:
        if value is None:
            return default
        if value not in {"0", "1"}:
            raise ValueError(f"expected 0 or 1, got {value!r}")
        return value == "1"

    @staticmethod
    def _encode_boolean(value: bool) -> str:
        return "1" if value else "0"

    @staticmethod
    def _normalized_exclusions(paths: tuple[Path, ...]) -> tuple[Path, ...]:
        unique: dict[str, Path] = {}
        for path in paths:
            normalized = path.expanduser().resolve(strict=False)
            unique.setdefault(_identity(normalized), normalized)
        return tuple(sorted(unique.values(), key=lambda path: str(path).casefold()))
