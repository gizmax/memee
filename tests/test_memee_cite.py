"""Tests for `memee cite <hash>` — citation resolver.

Coverage:
  * Resolve via 8-char short hash, dashed prefix, full UUID.
  * Lineage rendering (recorded → validated → promoted).
  * Ambiguous prefix fails cleanly.
  * `--confirm` bumps application_count and appends evidence.
  * `--format json` returns a parseable payload.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from memee.cli import cli
from memee.engine.citations import (
    cite_token,
    confirm_citation,
    lineage,
    resolve,
    short_hash,
)
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryType,
    MemoryValidation,
)


def _patch_db(tmp_path, monkeypatch):
    db_path = tmp_path / "cite.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def _seed_eval_with_lineage(session, org_id):
    """Seed the canonical eval AP plus 2 validations and a maturity stamp."""
    now = datetime.now(timezone.utc)
    m = Memory(
        organization_id=org_id,
        type=MemoryType.ANTI_PATTERN.value,
        title="never use eval() on user input",
        content="Trigger: parsing user math.\nConsequence: RCE.",
        tags=["python", "security", "eval"],
        confidence_score=0.94,
        maturity=MaturityLevel.CANON.value,
        source_agent="claude",
        source_model="sonnet",
        created_at=now - timedelta(days=15),
        last_validated_at=now - timedelta(days=5),
        project_count=5,
        validation_count=12,
        source_url="https://github.com/gizmax/memee-team/pull/47",
    )
    session.add(m)
    session.flush()
    session.add(
        AntiPattern(
            memory_id=m.id,
            severity="critical",
            trigger="needing to parse user math",
            consequence="arbitrary code execution",
            alternative="ast.literal_eval",
        )
    )
    # Two validations: cross-project, cross-model.
    session.add(
        MemoryValidation(
            memory_id=m.id,
            validated=True,
            evidence="caught in code review",
            validator_model="gpt-4o",
            created_at=now - timedelta(days=10),
        )
    )
    session.add(
        MemoryValidation(
            memory_id=m.id,
            validated=True,
            evidence="prevented in impact-tracker",
            validator_model="claude-sonnet-4",
            created_at=now - timedelta(days=7),
        )
    )
    session.commit()
    return m


# ── Pure engine API ──


def test_resolve_by_short_hash(session, org):
    m = _seed_eval_with_lineage(session, org.id)
    short = short_hash(m.id)
    assert len(short) == 8

    found = resolve(session, short)
    assert found is not None
    assert found.id == m.id


def test_resolve_by_full_uuid(session, org):
    m = _seed_eval_with_lineage(session, org.id)
    found = resolve(session, m.id)
    assert found is not None
    assert found.id == m.id


def test_resolve_by_dashed_prefix(session, org):
    m = _seed_eval_with_lineage(session, org.id)
    # First 12 chars of UUID include first dash.
    found = resolve(session, m.id[:12])
    assert found is not None
    assert found.id == m.id


def test_resolve_unwraps_cite_token(session, org):
    m = _seed_eval_with_lineage(session, org.id)
    token = cite_token(m.id)
    assert token.startswith("[mem:") and token.endswith("]")
    found = resolve(session, token)
    assert found is not None
    assert found.id == m.id


def test_resolve_returns_none_for_unknown(session, org):
    _seed_eval_with_lineage(session, org.id)
    assert resolve(session, "ffffffff") is None


def test_lineage_contains_recorded_and_validations(session, org):
    m = _seed_eval_with_lineage(session, org.id)
    lin = lineage(session, m)
    kinds = [e["kind"] for e in lin]
    assert "recorded" in kinds, f"expected 'recorded' in lineage, got {kinds}"
    assert "validated" in kinds, f"expected 'validated' in lineage, got {kinds}"
    # Promotion synthesised because last_validated_at + maturity=canon are set.
    assert "promoted" in kinds


def test_confirm_citation_bumps_application_count(session, org):
    m = _seed_eval_with_lineage(session, org.id)
    before = m.application_count or 0
    result = confirm_citation(session, m, note="cited in test reply")
    assert result["application_count"] == before + 1
    # Evidence chain has the citation entry.
    chain = m.evidence_chain or []
    assert any(e.get("kind") == "citation" for e in chain)


# ── CLI ──


def test_cite_cli_renders_lineage(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization

    engine = init_db()
    session = get_session(engine)
    org = session.query(Organization).first()
    m = _seed_eval_with_lineage(session, org.id)
    short = short_hash(m.id)
    session.close()

    result = runner.invoke(cli, ["cite", short])
    assert result.exit_code == 0, result.output

    # Header includes the cite token + title.
    assert f"[mem:{short}]" in result.output
    assert "never use eval" in result.output

    # Lineage section present with the seeded events.
    assert "Lineage:" in result.output
    assert "recorded" in result.output
    assert "validated" in result.output
    assert "gpt-4o" in result.output  # cross-model

    # Type/severity/maturity line.
    assert "anti_pattern" in result.output
    assert "critical" in result.output
    assert "canon" in result.output

    # Source URL is rendered.
    assert "github.com/gizmax/memee-team/pull/47" in result.output


def test_cite_cli_unknown_hash_clean_error(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(cli, ["cite", "deadbeef"])
    assert result.exit_code != 0
    err = result.output + (
        result.stderr_bytes.decode() if result.stderr_bytes else ""
    )
    assert "no unique memory matches" in err.lower() or "ambiguous" in err.lower()


def test_cite_cli_confirm_bumps_count(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    from memee.storage.database import get_session, init_db
    from memee.storage.models import Memory, Organization

    engine = init_db()
    session = get_session(engine)
    org = session.query(Organization).first()
    m = _seed_eval_with_lineage(session, org.id)
    short = short_hash(m.id)
    before = m.application_count or 0
    session.close()

    result = runner.invoke(
        cli, ["cite", short, "--confirm", "--note", "applied in feature/x"]
    )
    assert result.exit_code == 0, result.output
    assert "Confirmed citation" in result.output

    # Re-open and check the count bumped.
    session2 = get_session(init_db())
    refreshed = session2.get(Memory, m.id)
    assert refreshed.application_count == before + 1
    chain = refreshed.evidence_chain or []
    assert any(e.get("kind") == "citation" for e in chain)
    assert any("applied in feature/x" in (e.get("note") or "") for e in chain)


def test_cite_cli_json_format(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization

    engine = init_db()
    session = get_session(engine)
    org = session.query(Organization).first()
    m = _seed_eval_with_lineage(session, org.id)
    short = short_hash(m.id)
    session.close()

    result = runner.invoke(cli, ["cite", short, "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["id"] == m.id
    assert payload["cite"] == f"[mem:{short}]"
    assert payload["title"].startswith("never use eval")
    assert payload["severity"] == "critical"
    assert payload["maturity"] == "canon"
    assert isinstance(payload["lineage"], list)
    kinds = [e.get("kind") for e in payload["lineage"]]
    assert "recorded" in kinds
    assert "validated" in kinds
