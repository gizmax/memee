"""Tests for hook installation in `memee setup` / `memee doctor`.

Covers the merge logic in ``memee.hooks_config`` plus the doctor wrappers,
without needing a real AI tool installed. We point install/uninstall at a
temp file and verify the JSON shape, idempotency, and backup behaviour.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from memee.cli import cli
from memee.hooks_config import (
    MEMEE_MARK,
    diff_hooks,
    install_hooks_for_tool,
    merge_hooks,
    remove_memee_hooks,
    uninstall_hooks_for_tool,
)


# ── Pure merge logic ──


def test_merge_into_empty_config():
    """A bare config gets every Memee event written."""
    cfg = merge_hooks({})
    events = set(cfg["hooks"].keys())
    assert events == {"SessionStart", "UserPromptSubmit", "Stop"}
    for event, blocks in cfg["hooks"].items():
        assert isinstance(blocks, list) and len(blocks) == 1
        block = blocks[0]
        assert block["matcher"] == ""
        assert any(e.get(MEMEE_MARK) is True for e in block["hooks"])


def test_merge_preserves_user_hooks():
    """User's pre-existing hook commands survive the merge."""
    cfg = {
        "hooks": {
            "SessionStart": [
                {"matcher": "", "hooks": [{"type": "command", "command": "true"}]}
            ],
            "Stop": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
            ],
        }
    }
    merged = merge_hooks(cfg)

    # User's "true" still there.
    assert any(
        e.get("command") == "true"
        for block in merged["hooks"]["SessionStart"]
        for e in block.get("hooks", [])
    )
    # User's Stop block with matcher "Bash" still there.
    bash_blocks = [
        b for b in merged["hooks"]["Stop"] if b.get("matcher") == "Bash"
    ]
    assert bash_blocks and bash_blocks[0]["hooks"][0]["command"] == "echo hi"

    # Memee Stop hook added in a *new* block, not the user's matcher=Bash one.
    memee_blocks = [
        b for b in merged["hooks"]["Stop"]
        if any(e.get(MEMEE_MARK) for e in b.get("hooks", []))
    ]
    assert len(memee_blocks) == 1
    assert memee_blocks[0]["matcher"] == ""


def test_merge_is_idempotent():
    """Running merge twice doesn't duplicate Memee blocks."""
    cfg = merge_hooks(merge_hooks({}))
    for event in ("SessionStart", "UserPromptSubmit", "Stop"):
        memee_blocks = [
            b for b in cfg["hooks"][event]
            if any(e.get(MEMEE_MARK) for e in b.get("hooks", []))
        ]
        assert len(memee_blocks) == 1


def test_merge_replaces_existing_memee_hook_on_re_run():
    """If we change the canonical command, re-merge replaces the old one."""
    cfg = merge_hooks({})

    # Pretend a previous Memee version wrote a different command.
    for block in cfg["hooks"]["SessionStart"]:
        for entry in block["hooks"]:
            if entry.get(MEMEE_MARK):
                entry["command"] = "memee brief --old-flag"

    # Re-merge with the current definitions — old command should be gone.
    cfg = merge_hooks(cfg)
    commands = [
        e["command"]
        for block in cfg["hooks"]["SessionStart"]
        for e in block["hooks"]
        if e.get(MEMEE_MARK)
    ]
    assert all("--old-flag" not in c for c in commands)
    assert any("memee brief" in c for c in commands)


def test_remove_memee_hooks_leaves_user_hooks_intact():
    """Uninstall removes only Memee-marked entries."""
    cfg = {
        "hooks": {
            "SessionStart": [
                {"matcher": "", "hooks": [{"type": "command", "command": "true"}]}
            ]
        }
    }
    cfg = merge_hooks(cfg)
    cfg = remove_memee_hooks(cfg)

    # User's "true" still there.
    cmds = [
        e["command"]
        for block in cfg["hooks"]["SessionStart"]
        for e in block.get("hooks", [])
    ]
    assert "true" in cmds
    # No Memee marker anywhere.
    assert not any(
        e.get(MEMEE_MARK)
        for blocks in cfg["hooks"].values()
        for block in blocks
        for e in block.get("hooks", [])
    )


def test_remove_strips_empty_root_when_only_memee_was_there():
    """Pristine pre-Memee state restored when nothing else was installed."""
    cfg = merge_hooks({})
    cfg = remove_memee_hooks(cfg)
    assert "hooks" not in cfg


def test_diff_hooks_reports_added_on_fresh_install():
    """diff_hooks shows the three Memee events as added when starting empty."""
    after = merge_hooks({})
    diff = diff_hooks({}, after)
    assert set(diff["added"].keys()) == {
        "SessionStart", "UserPromptSubmit", "Stop"
    }


# ── File-level install / uninstall ──


def _settings_path(tmp_path):
    return tmp_path / "settings.json"


def test_install_into_missing_file_creates_file(tmp_path):
    path = _settings_path(tmp_path)
    res = install_hooks_for_tool(path)
    assert res["wrote"] is True
    assert res["existed"] is False
    assert res["backup_path"] is None  # nothing to back up
    cfg = json.loads(path.read_text())
    assert "hooks" in cfg
    assert set(cfg["hooks"].keys()) == {
        "SessionStart", "UserPromptSubmit", "Stop"
    }


def test_install_backs_up_existing_file(tmp_path):
    path = _settings_path(tmp_path)
    pre = {"mcpServers": {"memee": {"command": "memee", "args": ["serve"]}}}
    path.write_text(json.dumps(pre))

    res = install_hooks_for_tool(path)

    assert res["wrote"] is True
    assert res["backup_path"] is not None
    # Backup contains the pre-merge content.
    backup = json.loads(open(res["backup_path"]).read())
    assert backup == pre
    # New file has both mcpServers AND hooks.
    after = json.loads(path.read_text())
    assert after["mcpServers"]["memee"]["command"] == "memee"
    assert "hooks" in after


def test_install_does_not_clobber_user_hooks(tmp_path):
    """Re-running setup leaves the user's other hooks alone."""
    path = _settings_path(tmp_path)
    pre = {
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "user-cmd"}]}
            ]
        }
    }
    path.write_text(json.dumps(pre))

    install_hooks_for_tool(path)
    after = json.loads(path.read_text())

    # User's command still in the Stop block list.
    user_cmds = [
        e["command"]
        for block in after["hooks"]["Stop"]
        for e in block.get("hooks", [])
        if not e.get(MEMEE_MARK)
    ]
    assert "user-cmd" in user_cmds


def test_install_dry_run_writes_nothing(tmp_path):
    path = _settings_path(tmp_path)
    pre = {"foo": "bar"}
    path.write_text(json.dumps(pre))

    res = install_hooks_for_tool(path, dry_run=True)
    assert res["wrote"] is False
    assert res["backup_path"] is None
    # File is unchanged.
    assert json.loads(path.read_text()) == pre
    # Diff still describes what would change.
    assert res["diff"]["added"]


def test_install_handles_invalid_json_safely(tmp_path):
    """Bad JSON: file gets backed up, nothing overwritten."""
    path = _settings_path(tmp_path)
    path.write_text("{ this is not json")

    res = install_hooks_for_tool(path)
    assert res["wrote"] is False
    assert res["skipped_reason"] is not None
    assert res["backup_path"] is not None
    # Original file untouched (or backed up — whichever).
    assert "is not valid JSON" in res["skipped_reason"]


def test_uninstall_removes_only_memee(tmp_path):
    path = _settings_path(tmp_path)
    pre = {
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "user-cmd"}]}
            ]
        }
    }
    path.write_text(json.dumps(pre))

    install_hooks_for_tool(path)
    res = uninstall_hooks_for_tool(path)

    assert res["wrote"] is True
    after = json.loads(path.read_text())
    user_cmds = [
        e["command"]
        for block in after.get("hooks", {}).get("Stop", [])
        for e in block.get("hooks", [])
    ]
    assert "user-cmd" in user_cmds
    # No Memee marker anywhere.
    assert not _has_memee_anywhere(after)


def _has_memee_anywhere(cfg: dict) -> bool:
    for blocks in (cfg.get("hooks") or {}).values():
        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            for entry in block.get("hooks", []) or []:
                if isinstance(entry, dict) and entry.get(MEMEE_MARK):
                    return True
    return False


def test_uninstall_no_op_when_nothing_to_remove(tmp_path):
    path = _settings_path(tmp_path)
    pre = {"hooks": {"Stop": [{"matcher": "", "hooks": [{"command": "x"}]}]}}
    path.write_text(json.dumps(pre))

    res = uninstall_hooks_for_tool(path)
    assert res["wrote"] is False
    assert res["skipped_reason"] == "no Memee hooks present"
    # Original file is byte-identical (no spurious rewrite).
    assert json.loads(path.read_text()) == pre


# ── CLI integration: `memee doctor --no-hooks` etc. ──


def _patch_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def test_doctor_no_hooks_skips_hook_install(tmp_path, monkeypatch):
    """`memee doctor --no-hooks` runs but does not call install_hooks_all."""
    _patch_db(tmp_path, monkeypatch)

    called = {"install": 0, "uninstall": 0}

    import memee.doctor as doctor_mod

    def fake_install(*args, **kwargs):
        called["install"] += 1
        return []

    def fake_uninstall(*args, **kwargs):
        called["uninstall"] += 1
        return []

    monkeypatch.setattr(doctor_mod, "install_hooks_all", fake_install)
    monkeypatch.setattr(doctor_mod, "uninstall_hooks_all", fake_uninstall)

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--no-hooks", "--no-fix"])
    assert result.exit_code == 0
    assert called["install"] == 0
    assert called["uninstall"] == 0


def test_doctor_default_calls_install_hooks(tmp_path, monkeypatch):
    """`memee doctor` (default) installs hooks — at least attempts to."""
    _patch_db(tmp_path, monkeypatch)

    called = {"install": 0}
    import memee.doctor as doctor_mod

    def fake_install(*args, **kwargs):
        called["install"] += 1
        return []

    monkeypatch.setattr(doctor_mod, "install_hooks_all", fake_install)
    monkeypatch.setattr(doctor_mod, "uninstall_hooks_all", lambda **k: [])

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--no-fix"])
    assert result.exit_code == 0
    assert called["install"] == 1


def test_doctor_uninstall_hooks_calls_uninstall(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)

    called = {"install": 0, "uninstall": 0}
    import memee.doctor as doctor_mod

    def fake_install(*args, **kwargs):
        called["install"] += 1
        return []

    def fake_uninstall(*args, **kwargs):
        called["uninstall"] += 1
        return []

    monkeypatch.setattr(doctor_mod, "install_hooks_all", fake_install)
    monkeypatch.setattr(doctor_mod, "uninstall_hooks_all", fake_uninstall)

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--uninstall-hooks", "--no-fix"])
    assert result.exit_code == 0
    assert called["uninstall"] == 1
    assert called["install"] == 0


def test_doctor_dry_run_does_not_write_settings(tmp_path, monkeypatch):
    """A dry-run doctor run reports diff but writes nothing."""
    _patch_db(tmp_path, monkeypatch)

    # Point the Claude Code config at a temp file we own, then re-run doctor
    # in dry-run mode and verify the file didn't change.
    fake_config = tmp_path / "fake_settings.json"
    fake_config.write_text(json.dumps({"mcpServers": {}}))

    import memee.doctor as doctor_mod
    # Monkeypatch the AI_TOOLS entry for claude_code so it points at our
    # temp file. We restore by relying on monkeypatch teardown.
    new_tools = []
    for t in doctor_mod.AI_TOOLS:
        copy = dict(t)
        if t["id"] == "claude_code":
            copy["detect_path"] = tmp_path  # exists
            copy["config_path"] = fake_config
        else:
            copy["detect_path"] = tmp_path / "missing"
        new_tools.append(copy)
    monkeypatch.setattr(doctor_mod, "AI_TOOLS", new_tools)

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--dry-run", "--no-fix"])
    assert result.exit_code == 0

    # File still exactly what we wrote pre-run.
    assert json.loads(fake_config.read_text()) == {"mcpServers": {}}
