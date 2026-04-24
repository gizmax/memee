"""Regression: MCP decision/antipattern record must honour the dedup gate.

Before the fix, when the quality gate reported ``merged=True`` (i.e. a near-
duplicate existed), ``decision_record`` and ``antipattern_record`` fell
through and created a second Memory anyway, so repeated calls with the same
title produced duplicates. Both must now merge into the existing row
instead.
"""

from __future__ import annotations

import asyncio
import json

from memee.mcp_server import antipattern_record, decision_record
from memee.storage.models import Memory


def _run(coro):
    return asyncio.run(coro)


def _inject_session(monkeypatch, session):
    """Point the MCP helper at our test session."""
    import memee.mcp_server as mod

    monkeypatch.setattr(mod, "_get_session", lambda: session)


def test_antipattern_record_merges_duplicate(monkeypatch, session):
    _inject_session(monkeypatch, session)

    first = _run(
        antipattern_record(
            title="Never store API keys in source code",
            trigger="Hardcoding a secret",
            consequence="Key leaks via git history",
            severity="critical",
            tags="security,secrets",
        )
    )
    first_payload = json.loads(first)
    assert first_payload["status"] == "recorded"

    # Same anti-pattern arriving via a near-identical title — must merge.
    second = _run(
        antipattern_record(
            title="Never store API keys in source code",
            trigger="Hardcoding a secret (variant)",
            consequence="Key leaks via git history",
            severity="critical",
            tags="security,secrets",
        )
    )
    second_payload = json.loads(second)
    assert second_payload["status"] == "merged", second_payload
    assert second_payload["existing_id"] == first_payload["anti_pattern"]["memory_id"]

    # Only one Memory row exists for the anti-pattern.
    count = (
        session.query(Memory)
        .filter(Memory.type == "anti_pattern")
        .filter(Memory.title == "Never store API keys in source code")
        .count()
    )
    assert count == 1


def test_decision_record_merges_duplicate(monkeypatch, session):
    _inject_session(monkeypatch, session)

    first = _run(
        decision_record(
            chosen="PostgreSQL",
            title="Choose PostgreSQL over SQLite for production",
            alternatives='[{"name":"SQLite","reason_rejected":"single writer"}]',
        )
    )
    first_payload = json.loads(first)
    assert first_payload["status"] == "recorded"

    second = _run(
        decision_record(
            chosen="PostgreSQL",
            title="Choose PostgreSQL over SQLite for production",
            alternatives='[{"name":"SQLite","reason_rejected":"single writer, locking"}]',
        )
    )
    second_payload = json.loads(second)
    assert second_payload["status"] == "merged", second_payload
    assert second_payload["existing_id"] == first_payload["decision"]["memory_id"]

    count = (
        session.query(Memory)
        .filter(Memory.type == "decision")
        .filter(Memory.title == "Choose PostgreSQL over SQLite for production")
        .count()
    )
    assert count == 1
