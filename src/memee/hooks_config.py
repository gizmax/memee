"""Hook installation for Claude Code (and Claude-Code-compatible clients).

This is the "loop disappears" piece: writing settings.json `hooks` so the
agent's harness — not the agent itself — calls Memee on session start, on
every user prompt, and on Stop. The agent doesn't have to remember anything;
the runtime injects briefings and runs the post-task review automatically.

Real Claude Code hooks shape (verified against ~/.claude/settings.json):

    {
      "hooks": {
        "SessionStart": [
          {
            "matcher": "",
            "hooks": [
              {"type": "command", "command": "memee brief --project ..."}
            ]
          }
        ],
        "UserPromptSubmit": [...],
        "Stop": [...]
      }
    }

Each event maps to a list of {matcher, hooks} blocks; each block has a list
of {type, command} entries. We tag every Memee-installed entry with a
``"_memee": true`` marker so re-runs replace cleanly without clobbering the
user's other hooks.

Cursor and Continue: as of 2026-04 their MCP config files (``~/.cursor/mcp.json``,
Continue's config.json) accept ``mcpServers`` but not a ``hooks`` block — they
have no notion of harness-level command hooks. We document that in the doctor
output rather than silently failing.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Stable marker so we can recognise (and replace / remove) hooks Memee owns
# without touching anyone else's commands. Must be a JSON-friendly key; we
# attach it on every command dict we write.
MEMEE_MARK = "_memee"


def memee_hook_definitions() -> dict[str, list[dict]]:
    """The canonical set of hooks Memee installs, keyed by event name.

    Each value is a list of ``{type, command, _memee}`` entries — the inner
    ``hooks`` array of one matcher block. Commands reference ``$CLAUDE_*``
    env vars Claude Code provides at hook execution time. The Stop hook
    redirects everything to /dev/null so a slow or noisy `learn --auto`
    can't pollute the agent's terminal — `learn --auto` is also designed
    to be silent on no-op, but defence in depth.

    The ``UserPromptSubmit`` brief writes to stderr with ``--format compact``
    so it's small and inert; Claude Code surfaces it in the context. If the
    client doesn't render hook stdout we still pay nothing — the briefing
    runs against the local DB and exits.
    """
    return {
        "SessionStart": [
            {
                "type": "command",
                "command": (
                    'memee brief --project "$CLAUDE_PROJECT_DIR" '
                    "--format compact --budget 300"
                ),
                MEMEE_MARK: True,
            }
        ],
        "UserPromptSubmit": [
            {
                "type": "command",
                "command": (
                    'memee brief --task "$CLAUDE_USER_PROMPT" '
                    "--budget 200 --format compact"
                ),
                MEMEE_MARK: True,
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": "memee learn --auto",
                MEMEE_MARK: True,
            }
        ],
    }


def _is_memee_entry(entry: dict) -> bool:
    """True if this command dict was written by Memee (has the marker)."""
    return isinstance(entry, dict) and entry.get(MEMEE_MARK) is True


def merge_hooks(
    config: dict,
    hook_defs: dict[str, list[dict]] | None = None,
) -> dict:
    """Merge Memee hooks into a settings.json-shaped config.

    Idempotent: existing Memee entries (marked) are replaced, all other
    entries (including the user's own hooks under the same event) are
    preserved. The user's matcher blocks survive untouched; we just add or
    update one Memee-owned matcher block per event.

    Behaviour:
      * If ``config["hooks"]`` is absent, create it.
      * For each Memee event, find a matcher block whose inner ``hooks``
        contains a Memee-marked entry. If found, replace its inner ``hooks``
        with the fresh definition (this updates the command on a re-run
        when, say, the budget changes).
      * If no Memee-owned block exists for that event, append a new
        ``{matcher: "", hooks: [...]}`` block. The user's existing blocks
        keep running too — Claude Code fires every matching block.

    The function mutates and returns ``config``. Callers are expected to
    deep-copy beforehand if they need the pre-merge state for diffing.
    """
    if hook_defs is None:
        hook_defs = memee_hook_definitions()

    hooks_root = config.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        # User had `hooks: null` or a list — bail to dict, but keep the old
        # value under a side key so they can recover if they care.
        config["_hooks_legacy"] = hooks_root
        hooks_root = config["hooks"] = {}

    for event, mem_entries in hook_defs.items():
        blocks = hooks_root.setdefault(event, [])
        if not isinstance(blocks, list):
            # Same defensive recovery as above, scoped to one event.
            hooks_root[f"_{event}_legacy"] = blocks
            blocks = hooks_root[event] = []

        # Try to find an existing Memee block to replace.
        replaced = False
        for block in blocks:
            if not isinstance(block, dict):
                continue
            inner = block.get("hooks", [])
            if not isinstance(inner, list):
                continue
            if any(_is_memee_entry(e) for e in inner):
                # Replace just the Memee-marked entries; preserve any
                # foreign entries the user manually added inside the same
                # block (rare but possible).
                block["hooks"] = [e for e in inner if not _is_memee_entry(e)] + list(
                    mem_entries
                )
                # Normalise matcher to "" if absent so Claude Code sees a
                # well-formed block.
                block.setdefault("matcher", "")
                replaced = True
                break

        if not replaced:
            blocks.append({"matcher": "", "hooks": list(mem_entries)})

    return config


def remove_memee_hooks(config: dict) -> dict:
    """Strip every Memee-marked entry, leaving the user's hooks intact.

    If a matcher block ends up with an empty ``hooks`` list after removal,
    the block itself is dropped (a no-op block is just noise). If an event
    ends up with no blocks, the event key is removed. If ``hooks`` ends up
    empty, the top-level key is removed too — the file looks the same as
    before Memee ever touched it.
    """
    hooks_root = config.get("hooks")
    if not isinstance(hooks_root, dict):
        return config

    for event in list(hooks_root.keys()):
        blocks = hooks_root.get(event)
        if not isinstance(blocks, list):
            continue
        new_blocks: list[dict] = []
        for block in blocks:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue
            inner = block.get("hooks", [])
            if not isinstance(inner, list):
                new_blocks.append(block)
                continue
            kept = [e for e in inner if not _is_memee_entry(e)]
            if kept:
                block["hooks"] = kept
                new_blocks.append(block)
            # else: drop the now-empty block entirely
        if new_blocks:
            hooks_root[event] = new_blocks
        else:
            del hooks_root[event]

    if not hooks_root:
        del config["hooks"]
    return config


def diff_hooks(before: dict, after: dict) -> dict:
    """Return a small summary of what changed in the hooks block.

    Used by ``--dry-run`` so the user sees concretely what we would write.
    Compares only Memee-owned entries (marker present) for clarity.
    """
    def _memee_commands(cfg: dict) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for event, blocks in (cfg.get("hooks") or {}).items():
            cmds: list[str] = []
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                for entry in block.get("hooks", []) or []:
                    if _is_memee_entry(entry):
                        cmds.append(entry.get("command", ""))
            if cmds:
                out[event] = cmds
        return out

    before_map = _memee_commands(before)
    after_map = _memee_commands(after)
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    changed: dict[str, list[tuple[str, str]]] = {}

    all_events = set(before_map) | set(after_map)
    for event in all_events:
        b = before_map.get(event, [])
        a = after_map.get(event, [])
        if b and not a:
            removed[event] = b
        elif a and not b:
            added[event] = a
        elif a != b:
            changed[event] = list(zip(b, a))
    return {"added": added, "removed": removed, "changed": changed}


# ── File-level helpers (read / backup / write) ──


def read_settings(path: Path) -> tuple[dict, bool]:
    """Read a settings.json file, returning (config, existed).

    Missing file → ``({}, False)``. Existing-but-broken JSON raises
    ``ValueError`` — the caller is expected to back the file up and bail,
    matching the existing 1.0.4 behaviour in ``doctor.configure_tool``.
    """
    if not path.exists():
        return {}, False
    raw = path.read_text()
    if not raw.strip():
        return {}, True
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: top-level must be an object, got {type(cfg).__name__}")
    return cfg, True


def backup_settings(path: Path) -> Path | None:
    """Copy ``path`` to ``path.bak.<UTC-timestamp>`` and return the backup path.

    Returns None if the source file doesn't exist (nothing to back up).
    Backups always include a timestamp so re-runs don't overwrite each other —
    we'd rather leak a few KB than lose a user's pre-Memee config.
    """
    if not path.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak.{ts}")
    # On some filesystems (network mounts) shutil.copy2 metadata copy fails.
    # We only need byte fidelity; .read_bytes / .write_bytes is enough.
    backup.write_bytes(path.read_bytes())
    return backup


def atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` as pretty JSON to ``path`` atomically.

    Mirrors ``doctor.configure_tool``'s safety: tmp file + os.replace so a
    Ctrl-C between open and close can't leave the user's file truncated.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def install_hooks_for_tool(
    config_path: Path,
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> dict:
    """Install Memee hooks into the given settings.json-style file.

    Returns a result dict::

        {
          "path": str,
          "existed": bool,
          "backup_path": str | None,
          "wrote": bool,
          "diff": {...},          # output of diff_hooks
          "skipped_reason": str | None,
        }

    Designed to be safe to call multiple times: re-running just normalises
    Memee entries to whatever ``memee_hook_definitions()`` currently says.
    """
    result: dict = {
        "path": str(config_path),
        "existed": False,
        "backup_path": None,
        "wrote": False,
        "diff": {"added": {}, "removed": {}, "changed": {}},
        "skipped_reason": None,
    }

    try:
        before, existed = read_settings(config_path)
    except ValueError as e:
        # Bad JSON → back up and refuse to overwrite. Surfaces as a doctor
        # warning; the user fixes their syntax and reruns.
        result["existed"] = config_path.exists()
        result["skipped_reason"] = str(e)
        if backup and config_path.exists():
            result["backup_path"] = str(backup_settings(config_path))
        return result

    result["existed"] = existed
    # Snapshot pre-merge state for diffing — json round-trip is the cheapest
    # deep-copy we have and avoids importing copy module.
    before_snapshot = json.loads(json.dumps(before))
    after = merge_hooks(before)
    result["diff"] = diff_hooks(before_snapshot, after)

    if dry_run:
        return result

    if existed and backup:
        result["backup_path"] = (
            str(backup_settings(config_path))
            if config_path.exists()
            else None
        )
    atomic_write_json(config_path, after)
    result["wrote"] = True
    return result


def uninstall_hooks_for_tool(
    config_path: Path,
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> dict:
    """Remove Memee hooks from ``config_path``.

    Mirrors ``install_hooks_for_tool`` shape. If the file is missing or
    has no Memee hooks, returns ``wrote=False`` with no backup.
    """
    result: dict = {
        "path": str(config_path),
        "existed": config_path.exists(),
        "backup_path": None,
        "wrote": False,
        "diff": {"added": {}, "removed": {}, "changed": {}},
        "skipped_reason": None,
    }
    if not config_path.exists():
        result["skipped_reason"] = "config file does not exist"
        return result

    try:
        before, _ = read_settings(config_path)
    except ValueError as e:
        result["skipped_reason"] = str(e)
        return result

    before_snapshot = json.loads(json.dumps(before))
    after = remove_memee_hooks(json.loads(json.dumps(before)))
    diff = diff_hooks(before_snapshot, after)
    result["diff"] = diff

    # Fast path: nothing to remove.
    if not diff["removed"] and not diff["changed"]:
        result["skipped_reason"] = "no Memee hooks present"
        return result

    if dry_run:
        return result

    if backup:
        result["backup_path"] = str(backup_settings(config_path))
    atomic_write_json(config_path, after)
    result["wrote"] = True
    return result
