"""v2.4.16 — CLI UX hygiene bundle.

Three small, unrelated fixes ride in one patch:

* ``memee validate --project <unregistered>`` used to crash with
  ``AttributeError: 'NoneType' object has no attribute 'id'`` because
  ``_get_or_create_project`` lied — it never created anything. Renamed
  to ``_get_project_by_path`` and the caller now raises a clean
  ``click.ClickException``.

* ``memee feedback`` says EVENT_ID is "printed by ``memee search``", but
  search never passed ``return_event_id=True`` to the underlying
  ``search_memories`` call and never printed the id. ``search`` now
  surfaces ``query_event_id`` after the results so the documented
  feedback loop is actually one keystroke away.

The third member of the bundle — ``test_doctor_returns_ok_on_macos_with_deps``
gaining ``pytest.importorskip("rumps")`` — lives in ``tests/test_bar.py``
where the offending assertion is.
"""

from __future__ import annotations

import re

from click.testing import CliRunner

from memee.cli import cli
from memee.storage.models import MaturityLevel, Memory, MemoryType


def test_validate_unregistered_project_returns_clean_error(tmp_path, monkeypatch):
    """No more AttributeError — a click error explains the next step."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path / ".memee"))
    runner = CliRunner()
    runner.invoke(cli, ["init"])

    # Seed one memory so the lookup half of `validate` succeeds.
    from memee.storage.database import get_session, init_db
    engine = init_db()
    session = get_session(engine)
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="Some pattern",
        content="content",
        maturity=MaturityLevel.HYPOTHESIS.value,
    )
    session.add(m)
    session.commit()
    short = m.id[:8]
    session.close()

    bogus = str(tmp_path / "never-registered")
    result = runner.invoke(cli, ["validate", short, "-p", bogus])
    assert result.exit_code != 0
    combined = result.output + (result.stderr_bytes or b"").decode(errors="ignore")
    assert "not registered" in combined.lower(), combined
    # Concrete next step is mentioned so the user isn't stuck.
    assert "memee project add" in combined


def test_search_prints_query_event_id(tmp_path, monkeypatch):
    """`memee search` surfaces the event_id so `memee feedback` works."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path / ".memee"))
    monkeypatch.setenv("MEMEE_TELEMETRY", "1")
    runner = CliRunner()
    runner.invoke(cli, ["init"])

    from memee.storage.database import get_session, init_db
    engine = init_db()
    session = get_session(engine)
    session.add(
        Memory(
            type=MemoryType.PATTERN.value,
            title="Use timeout on HTTP requests",
            content="Always pass timeout=10 to requests.get.",
            tags=["python", "http"],
            maturity=MaturityLevel.VALIDATED.value,
            confidence_score=0.9,
        )
    )
    session.commit()
    session.close()

    result = runner.invoke(cli, ["search", "timeout HTTP"])
    assert result.exit_code == 0, result.output
    assert "query_event_id:" in result.output
    # The id is a UUID-shaped string — check the format so a future
    # change of the print template doesn't silently break the feedback
    # loop the user is following.
    match = re.search(r"query_event_id:\s+([0-9a-f-]{8,})", result.output)
    assert match, result.output


def test_search_no_results_still_prints_event_id(tmp_path, monkeypatch):
    """Empty result sets still log a SearchEvent — surface its id too so
    `memee feedback` works after an exploratory zero-hit query."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path / ".memee"))
    monkeypatch.setenv("MEMEE_TELEMETRY", "1")
    runner = CliRunner()
    runner.invoke(cli, ["init"])
    result = runner.invoke(cli, ["search", "zzz-no-match-zzz"])
    assert result.exit_code == 0
    assert "No memories found" in result.output
    assert "query_event_id:" in result.output


def test_search_silent_when_telemetry_disabled(tmp_path, monkeypatch):
    """With telemetry off, no event id to print → don't print a misleading
    empty 'query_event_id:' line."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path / ".memee"))
    monkeypatch.setenv("MEMEE_TELEMETRY", "0")
    runner = CliRunner()
    runner.invoke(cli, ["init"])

    from memee.storage.database import get_session, init_db
    engine = init_db()
    session = get_session(engine)
    session.add(
        Memory(
            type=MemoryType.PATTERN.value,
            title="Some pattern",
            content="content",
            tags=["any"],
            maturity=MaturityLevel.HYPOTHESIS.value,
        )
    )
    session.commit()
    session.close()

    result = runner.invoke(cli, ["search", "pattern"])
    assert result.exit_code == 0
    assert "query_event_id:" not in result.output
