from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jwdm.config import (
    AppSettings,
    ConfidencePolicy,
    ExtensionRule,
    RuleAction,
)
from jwdm.persistence.state import STATE_SCHEMA_VERSION, StateError, StateRepository
from jwdm.pipeline.candidate import CandidateState
from jwdm.services.candidate_registry import CandidateRegistry


def test_state_database_migrates_and_round_trips_settings(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    repository = StateRepository(path)
    library = tmp_path / "library"
    incoming = tmp_path / "incoming"
    exclusion = incoming / "private"
    settings = AppSettings(
        library_path=library,
        incoming_path=incoming,
        start_with_windows=True,
        launch_minimized=True,
        minimize_to_tray=False,
        close_notice_shown=True,
        start_automatic=True,
        process_existing_on_start=True,
        confidence_policy=ConfidencePolicy.REVIEW_ALL,
        exclusions=(exclusion, exclusion),
    )

    repository.save_settings(settings)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == STATE_SCHEMA_VERSION
    restored = repository.settings()
    assert restored == replace(
        settings,
        exclusions=(exclusion.resolve(strict=False),),
    )


def test_rules_are_normalized_replaced_and_ordered(tmp_path: Path) -> None:
    repository = StateRepository(tmp_path / "state.db")
    repository.replace_rules(
        (
            ExtensionRule("PDF", RuleAction.REVIEW, priority=20),
            ExtensionRule("*.asset", RuleAction.ROUTE, "Creator/Assets", priority=10),
            ExtensionRule(".ignore", RuleAction.IGNORE, priority=30),
        )
    )

    rules = repository.rules()

    assert [rule.extension for rule in rules] == [".asset", ".pdf", ".ignore"]
    assert rules[0].category == "Creator/Assets"
    assert all(rule.rule_id is not None for rule in rules)


def test_candidate_paths_survive_restart_until_terminal(tmp_path: Path) -> None:
    repository = StateRepository(tmp_path / "state.db")
    registry = CandidateRegistry()
    incoming = tmp_path / "incoming"
    source = incoming / "pending.pdf"
    snapshot = registry.register_event(
        source,
        incoming,
        "created",
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    repository.save_candidates(incoming, (snapshot,))
    assert repository.pending_paths(incoming) == (source,)

    completed = registry.transition(
        snapshot.candidate_id,
        CandidateState.MOVED,
        "done",
    )
    assert completed is not None
    repository.save_candidates(incoming, (completed,))
    assert repository.pending_paths(incoming) == ()


def test_newer_state_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {STATE_SCHEMA_VERSION + 1}")

    with pytest.raises(StateError, match="newer than"):
        StateRepository(path)
