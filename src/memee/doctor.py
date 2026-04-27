"""Memee Doctor: health check, AI tool detection, auto-configuration.

Scans the system for:
- Database health (size, FTS index, embeddings)
- AI tools installed (Claude Code, Cursor, Windsurf, Claude Desktop, Ollama)
- MCP configuration status for each tool
- Team connection status
- Knowledge health (maturity distribution, stale memories)

Auto-fixes:
- Missing MCP configurations
- Missing embeddings
- Stale hypotheses warning
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# ── ANSI ──
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    BGREEN = "\033[92m"
    BYELLOW = "\033[93m"
    BRED = "\033[91m"
    BCYAN = "\033[96m"


# ── AI Tool Registry ──

AI_TOOLS = [
    {
        "id": "claude_code",
        "name": "Claude Code",
        "detect_path": Path.home() / ".claude",
        "config_path": Path.home() / ".claude" / "settings.json",
        "config_key": "mcpServers",
        "mcp_entry": {"memee": {"command": "memee", "args": ["serve"]}},
        # Hooks: Claude Code's harness runs commands on SessionStart /
        # UserPromptSubmit / Stop. This is what makes Memee fully automatic.
        "supports_hooks": True,
    },
    {
        "id": "claude_desktop",
        "name": "Claude Desktop",
        "detect_path": Path.home() / "Library" / "Application Support" / "Claude",
        "config_path": Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        "config_key": "mcpServers",
        "mcp_entry": {"memee": {"command": "memee", "args": ["serve"]}},
        # Claude Desktop has no hook system — purely MCP-driven.
        "supports_hooks": False,
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "detect_path": Path.home() / "Library" / "Application Support" / "Cursor",
        "config_path": Path.home() / ".cursor" / "mcp.json",
        "config_key": "mcpServers",
        "mcp_entry": {"memee": {"command": "memee", "args": ["serve"]}},
        # Cursor's mcp.json (~/.cursor/mcp.json) accepts mcpServers but has
        # no harness-level hooks block as of 2026-04. MCP-only.
        "supports_hooks": False,
    },
    {
        "id": "windsurf",
        "name": "Windsurf",
        "detect_path": Path.home() / "Library" / "Application Support" / "Windsurf",
        "config_path": Path.home() / ".codeium" / "windsurf" / "mcp_config.json",
        "config_key": "mcpServers",
        "mcp_entry": {"memee": {"command": "memee", "args": ["serve"]}},
        # Windsurf's mcp_config.json is MCP-only — no hook system.
        "supports_hooks": False,
    },
]

CLI_TOOLS = [
    {
        "id": "ollama",
        "name": "Ollama",
        "detect_cmd": "ollama",
        "note": "Use via CLI: memee search 'query' — pipe to ollama",
    },
]


# ── Multi-install detection ──
#
# v2.0.1: many users hit "No such command 'pack'" errors after upgrading
# memee via pipx — because their shell still resolves an older
# ``/opt/homebrew/bin/memee`` (installed earlier with ``pip install`` against
# Homebrew Python) before the pipx shim. Detecting this and pointing at the
# specific Python whose pip needs to drop the shadow is more useful than
# any other doctor output we ship.

# Cache the scan within a single Python invocation. The detector runs in
# both ``--version`` and ``setup`` pre-flight; doing the PATH walk once is
# enough.
_MEMEE_INSTALL_CACHE: list[dict] | None = None


def _classify_install(path: str, shebang_python: str | None) -> str:
    """Heuristic — ``pipx`` / ``homebrew-python`` / ``user-pip`` / ``system-python`` / ``unknown``.

    We classify on the path of the *Python* the binary is bound to (read
    from the shebang), falling back to the binary path itself when the
    shebang is unreadable.
    """
    home = str(Path.home())
    candidates = [shebang_python, path]
    for cand in candidates:
        if not cand:
            continue
        # pipx venvs live under ~/.local/pipx (Linux/macOS) or
        # ~/Library/Application Support/pipx (rare). Either way the path
        # contains the literal "pipx".
        if "pipx" in cand:
            return "pipx"
        # Homebrew Python on macOS lives under /opt/homebrew (Apple Silicon)
        # or /usr/local/Cellar (Intel). Linuxbrew uses /home/linuxbrew.
        if (
            cand.startswith("/opt/homebrew/")
            or cand.startswith("/usr/local/Cellar/")
            or cand.startswith("/home/linuxbrew/")
        ):
            return "homebrew-python"
        # ``pip install --user`` lands binaries in ~/.local/bin and Python
        # imports in ~/.local/lib/pythonX.Y/site-packages. We can't rely on
        # ~/.local/bin alone because pipx also drops shims there — but the
        # *shebang* will point at the user's site-python, not a pipx venv.
        if cand.startswith(home + "/.local/lib") or cand.startswith(
            home + "/Library/Python/"
        ):
            return "user-pip"
        # Distro Python.
        if cand in ("/usr/bin/python", "/usr/bin/python3") or cand.startswith(
            "/usr/bin/"
        ):
            return "system-python"
    return "unknown"


def _read_shebang(path: str) -> str | None:
    """Read the first ~200 bytes of ``path``, return the interpreter path
    from a ``#!`` shebang, or None if the file can't be read or doesn't
    start with one. Handles broken symlinks, binaries, and permission errors.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(200)
    except (OSError, PermissionError):
        return None
    if not head.startswith(b"#!"):
        return None
    # First line, after the "#!"
    first_line = head.split(b"\n", 1)[0][2:].strip()
    if not first_line:
        return None
    try:
        decoded = first_line.decode("utf-8", errors="replace")
    except Exception:
        return None
    # ``#!/usr/bin/env python3`` → interpreter is the second token
    parts = decoded.split()
    if not parts:
        return None
    if parts[0].endswith("/env") and len(parts) >= 2:
        # ``env python3`` — return ``python3``; classification will fall
        # back to the binary path since this gives us no install hint.
        return parts[1]
    return parts[0]


_VERSION_RE = None


def _query_version(path: str) -> str | None:
    """Run ``<path> --version`` and return a clean version string, or None.

    Older memee versions (pre-2.0.1) didn't have a top-level ``--version``
    option and Click responds with a multi-line "No such option" error. We
    tolerate that — if the output doesn't look like a single version line,
    we return None so the report shows ``v?`` rather than splatting the
    whole error into the table.
    """
    global _VERSION_RE
    if _VERSION_RE is None:
        import re as _re

        # Match a version-like first line: optional "memee" / "memee," prefix,
        # then a token containing at least one digit and dots/letters.
        _VERSION_RE = _re.compile(
            r"^(?:memee[, ]+(?:version\s+)?)?(\d[\w.\-+]*)\s*$",
            _re.IGNORECASE,
        )

    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            timeout=5,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return None

    # If --version isn't recognised, exit code is nonzero and stderr holds
    # a Click error. Don't try to parse — older memee never emitted a
    # version, so just return None.
    if result.returncode != 0:
        return None

    output = (result.stdout or "").strip()
    if not output:
        return None

    # Strip ANSI just in case some future memee colours the version line.
    import re as _re_local

    output = _re_local.sub(r"\x1b\[[0-9;]*m", "", output).strip()

    # Check the first non-empty line. v2.0.1 ``--version`` prints multiple
    # lines ("memee 2.0.1\n  installed: …") — only the first matters.
    first = output.split("\n", 1)[0].strip()
    m = _VERSION_RE.match(first)
    if m:
        return m.group(1)

    # Fallback: if the line looks like ``<prog> <version>`` with a digit,
    # return the last token. Anything multi-line or weird → None.
    if "\n" in output:
        return None
    tokens = first.split()
    if tokens:
        last = tokens[-1]
        if any(ch.isdigit() for ch in last):
            return last
    return None


def detect_memee_installs(*, use_cache: bool = True) -> list[dict]:
    """Scan PATH for every memee binary the shell can find.

    Returns list of ``{path, real_path, mtime, version, install_kind,
    shebang_python}`` sorted by PATH order. Distinct entries are deduplicated
    on ``realpath`` so a Homebrew → pipx symlink doesn't trigger a warning.

    Edge cases handled:
    - Missing PATH directories
    - Broken symlinks (file not readable but listed)
    - Files without read permission
    - Files that aren't actually Python scripts (no shebang)
    - PATH entries that aren't directories
    """
    global _MEMEE_INSTALL_CACHE
    if use_cache and _MEMEE_INSTALL_CACHE is not None:
        return _MEMEE_INSTALL_CACHE

    binary_name = "memee.exe" if os.name == "nt" else "memee"
    seen_paths: set[str] = set()
    seen_realpaths: set[str] = set()
    results: list[dict] = []

    path_env = os.environ.get("PATH", "")
    for entry in path_env.split(os.pathsep):
        if not entry:
            continue
        candidate = os.path.join(entry, binary_name)
        try:
            if not os.path.isfile(candidate):
                continue
        except OSError:
            # Broken/unreadable PATH entry — skip silently.
            continue
        if candidate in seen_paths:
            continue
        seen_paths.add(candidate)

        # Dedup on realpath so a symlink chain → one install isn't warned on.
        try:
            real = os.path.realpath(candidate)
        except OSError:
            real = candidate
        if real in seen_realpaths:
            continue
        seen_realpaths.add(real)

        # mtime is best-effort; broken file → 0.
        try:
            mtime = os.path.getmtime(candidate)
        except OSError:
            mtime = 0.0

        shebang_python = _read_shebang(real) or _read_shebang(candidate)
        version = _query_version(candidate)
        install_kind = _classify_install(real, shebang_python)

        results.append(
            {
                "path": candidate,
                "real_path": real,
                "mtime": mtime,
                "version": version,
                "install_kind": install_kind,
                "shebang_python": shebang_python,
            }
        )

    _MEMEE_INSTALL_CACHE = results
    return results


def _install_kind_label(kind: str) -> str:
    """Human-friendly label for an install_kind."""
    return {
        "pipx": "pipx",
        "homebrew-python": "Homebrew Python",
        "user-pip": "user pip",
        "system-python": "system Python",
        "unknown": "unknown",
    }.get(kind, kind)


def _fix_hint(active: dict, shadowed: dict) -> list[str]:
    """Return shell commands the user can run to drop the shadowing install.

    ``active`` is what the shell currently resolves; ``shadowed`` is the
    upgraded one being hidden. The fix targets ``active``: that's the one
    we want gone (or moved off PATH).
    """
    kind = active["install_kind"]
    shebang = active.get("shebang_python")
    if kind == "homebrew-python":
        py = shebang or "/opt/homebrew/bin/python3"
        return [
            f"{py} -m pip uninstall memee",
            "rehash   # or: open a new shell",
        ]
    if kind == "user-pip":
        py = shebang or "python3"
        return [
            f"{py} -m pip uninstall memee",
            "rehash   # or: open a new shell",
        ]
    if kind == "system-python":
        py = shebang or "/usr/bin/python3"
        return [
            f"sudo {py} -m pip uninstall memee   # if pip-managed",
            "# or remove the binary directly:",
            f"sudo rm {active['path']}",
        ]
    if kind == "pipx":
        # Two pipx installs in different homes. No safe scripted fix.
        return [
            "# Two pipx installs detected — uncommon. Pick one:",
            f"pipx uninstall memee   # run with the right HOME for {active['path']}",
        ]
    # Unknown — be conservative.
    return [
        f"# Unknown install kind for {active['path']}.",
        f"# Inspect the shebang ({shebang or 'none'}) and uninstall via that pip,",
        f"# or as a last resort: rm {active['path']}",
        "# (Removing the file may break that Python's `import memee`.)",
    ]


def _parse_version_tuple(version: str | None) -> tuple[int, ...] | None:
    """Parse a version string like ``2.0.1`` into ``(2, 0, 1)`` for ordered
    comparison. Returns None if the string isn't recognisable as a version,
    so we can treat "unknown version" as "definitely older" downstream.

    We don't pull in ``packaging`` for this — the version strings we get
    from `memee --version` are dotted-numeric with maybe a ``.devN`` /
    ``.rcN`` suffix. Stripping non-digit suffixes from each component is
    enough for the only thing we use this for: "is the active install older
    than the shadowed one".
    """
    if not version:
        return None
    parts: list[int] = []
    for chunk in version.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            return None
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _pip_required_by(python_path: str, package: str = "memee") -> list[str] | None:
    """Return the list of packages that depend on ``package`` in this Python.

    Runs ``<python> -m pip show <package>`` and parses the ``Required-by:``
    line. Empty list = nothing depends on it (safe to uninstall). None =
    couldn't run pip (don't auto-fix; let the user handle it manually).
    """
    try:
        result = subprocess.run(
            [python_path, "-m", "pip", "show", package],
            capture_output=True,
            timeout=10,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("Required-by:"):
            tail = line[len("Required-by:"):].strip()
            if not tail:
                return []
            return [p.strip() for p in tail.split(",") if p.strip()]
    return None


def _can_safely_remove(
    active: dict, shadowed: list[dict]
) -> tuple[bool, str]:
    """Decide whether we're allowed to ``pip uninstall`` the active binary.

    Returns ``(ok, reason)``. ``ok=True`` means doctor will run the
    uninstall as part of auto-fix. Otherwise ``reason`` is the human-readable
    "why not", shown in the report so the user understands why doctor left
    a manual hint instead.
    """
    kind = active.get("install_kind")
    if kind not in ("homebrew-python", "user-pip"):
        return False, (
            f"active install is {_install_kind_label(kind)} — "
            "doctor only auto-removes pip-managed installs (Homebrew "
            "Python or user pip)"
        )

    shebang = active.get("shebang_python")
    if not shebang or not os.path.isfile(shebang):
        return False, (
            "can't locate the Python interpreter for the active install "
            "(no readable shebang)"
        )

    if not shadowed:
        return False, "no shadowed install to fall back to"
    target = shadowed[0]

    active_v = _parse_version_tuple(active.get("version"))
    target_v = _parse_version_tuple(target.get("version"))
    # If we can't read the active version (broken editable / pre-2.0.1
    # install), we treat it as the older one — that case is *exactly* what
    # this fix exists for, so refusing on missing version would defeat it.
    if active_v is not None and target_v is not None and active_v >= target_v:
        return False, (
            f"active v{active.get('version')} is not older than shadowed "
            f"v{target.get('version')} — doctor won't downgrade"
        )
    if target_v is None:
        return False, (
            "shadowed install doesn't report a version — doctor needs a "
            "known-good fallback before removing the active one"
        )

    deps = _pip_required_by(shebang)
    if deps is None:
        return False, (
            f"couldn't run `{shebang} -m pip show memee` to verify safety"
        )
    if deps:
        return False, (
            f"other packages depend on memee in this Python: {', '.join(deps)}"
        )
    return True, ""


def _uninstall_active(active: dict, *, dry_run: bool) -> dict:
    """Run ``<python> -m pip uninstall -y memee`` for the active install.

    Returns ``{"ok": bool, "stdout": str, "stderr": str, "returncode": int,
    "command": list[str], "dry_run": bool}``. The caller decides what to do
    with failures — we don't raise so doctor's report can show pip's actual
    error rather than a generic traceback.
    """
    python_path = active.get("shebang_python") or ""
    cmd = [python_path, "-m", "pip", "uninstall", "-y", "memee"]
    # PEP 668: Homebrew Python (and an increasing number of distro-shipped
    # Pythons) blocks pip outside venvs unless ``--break-system-packages``
    # is passed. We add it for the kinds we already gate to "pip-managed,
    # safe to uninstall" — without the flag the auto-fix dies on
    # ``error: externally-managed-environment``.
    if active.get("install_kind") == "homebrew-python":
        cmd.append("--break-system-packages")
    if dry_run:
        return {
            "ok": True,
            "stdout": "",
            "stderr": "",
            "returncode": 0,
            "command": cmd,
            "dry_run": True,
        }
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=60, text=True
        )
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"{type(e).__name__}: {e}",
            "returncode": -1,
            "command": cmd,
            "dry_run": False,
        }
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "returncode": result.returncode,
        "command": cmd,
        "dry_run": False,
    }


def get_install_health() -> dict:
    """Snapshot the multi-install state for the report layer."""
    installs = detect_memee_installs()
    return {
        "installs": installs,
        "count": len(installs),
        "multi": len(installs) > 1,
    }


def _update_status_for_report() -> dict:
    """Return a small dict the doctor report layer can render without
    importing ``update_check``. Stays compatible with the existing report
    code that does ``results.get("update") or {}``.
    """
    try:
        from memee.update_check import check
        s = check()
    except Exception:
        # Update check has its own swallowing, but a dependency import
        # error during ``import memee.update_check`` would still propagate
        # — guard against that too. Better to skip the section than to
        # break ``memee doctor``.
        return {}
    return {
        "available": s.available,
        "current": s.current,
        "latest": s.latest,
        "checked_at": s.checked_at,
        "source": s.source,
    }


def detect_ai_tools() -> list[dict]:
    """Scan system for installed AI tools and their MCP config status."""
    from memee.hooks_config import MEMEE_MARK

    results = []

    for tool in AI_TOOLS:
        detected = tool["detect_path"].exists()
        configured = False
        config_exists = False
        hooks_configured = False

        if detected and tool.get("config_path"):
            config_path = tool["config_path"]
            if config_path.exists():
                config_exists = True
                try:
                    config = json.loads(config_path.read_text())
                    servers = config.get(tool["config_key"], {})
                    configured = "memee" in servers
                    # Detect Memee-owned hooks by walking the (event → blocks
                    # → hooks) tree looking for our marker. We don't depend on
                    # the exact command string so it survives version bumps.
                    if tool.get("supports_hooks"):
                        for blocks in (config.get("hooks") or {}).values():
                            if not isinstance(blocks, list):
                                continue
                            for block in blocks:
                                if not isinstance(block, dict):
                                    continue
                                for entry in block.get("hooks") or []:
                                    if (
                                        isinstance(entry, dict)
                                        and entry.get(MEMEE_MARK) is True
                                    ):
                                        hooks_configured = True
                                        break
                                if hooks_configured:
                                    break
                            if hooks_configured:
                                break
                except (json.JSONDecodeError, KeyError):
                    pass

        results.append({
            "id": tool["id"],
            "name": tool["name"],
            "detected": detected,
            "config_exists": config_exists,
            "configured": configured,
            "config_path": str(tool.get("config_path", "")),
            "can_auto_fix": detected and not configured,
            "supports_hooks": tool.get("supports_hooks", False),
            "hooks_configured": hooks_configured,
        })

    # CLI tools
    for tool in CLI_TOOLS:
        detected = shutil.which(tool["detect_cmd"]) is not None
        results.append({
            "id": tool["id"],
            "name": tool["name"],
            "detected": detected,
            "configured": detected,  # CLI tools are "configured" if present
            "config_path": "",
            "can_auto_fix": False,
            "note": tool.get("note", ""),
        })

    return results


def configure_tool(tool_id: str) -> bool:
    """Write MCP configuration for a specific AI tool.

    Safety rails:
    - On JSONDecodeError we MUST NOT overwrite: the existing file may hold
      hooks/permissions/env/enabledPlugins the user still needs. We back the
      broken file up to ``settings.json.bak.<timestamp>`` and raise — the
      user fixes the syntax and reruns.
    - Writes are atomic: tmp file + os.replace so Ctrl-C mid-write can't
      leave the original truncated.
    """
    tool_def = next((t for t in AI_TOOLS if t["id"] == tool_id), None)
    if not tool_def:
        return False

    config_path = tool_def["config_path"]
    config_key = tool_def["config_key"]
    mcp_entry = tool_def["mcp_entry"]

    # Read existing config or create new
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except json.JSONDecodeError as e:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = config_path.with_suffix(
                config_path.suffix + f".bak.{ts}"
            )
            try:
                config_path.replace(backup_path)
            except OSError:
                # If we can't even move it, bail out without overwriting.
                raise RuntimeError(
                    f"{config_path} has invalid JSON and could not be backed up: {e}. "
                    "Fix the syntax manually and rerun."
                ) from e
            print(
                f"  {C.BYELLOW}!{C.RESET} {config_path.name} had invalid JSON. "
                f"Backed up to {backup_path.name}."
            )
            raise RuntimeError(
                f"{config_path} had invalid JSON (backed up to {backup_path}). "
                "Fix the syntax and rerun `memee doctor`."
            ) from e

    # Add memee to mcpServers
    if config_key not in config:
        config[config_key] = {}
    config[config_key].update(mcp_entry)

    # Write back atomically: tmp + os.replace. Ctrl-C mid-write can't corrupt
    # the user's existing settings.json.
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(config, indent=2) + "\n")
    os.replace(tmp_path, config_path)
    return True


def configure_all_detected() -> list[dict]:
    """Auto-configure all detected but unconfigured tools."""
    tools = detect_ai_tools()
    configured = []

    for tool in tools:
        if tool.get("can_auto_fix") and not tool["configured"]:
            try:
                success = configure_tool(tool["id"])
            except RuntimeError:
                # configure_tool bails out on invalid JSON — don't crash the
                # whole auto-configure pass; the user saw the warning/backup
                # message already. Skip and move on.
                continue
            if success:
                tool["configured"] = True
                configured.append(tool)

    return configured


def install_hooks_for(tool_id: str, *, dry_run: bool = False) -> dict | None:
    """Install Memee hooks into the named tool's settings.json.

    Returns the result dict from ``hooks_config.install_hooks_for_tool`` or
    None if the tool doesn't support hooks (Cursor, Windsurf, Claude Desktop
    today). The caller renders the dict to the user.
    """
    from memee.hooks_config import install_hooks_for_tool

    tool_def = next((t for t in AI_TOOLS if t["id"] == tool_id), None)
    if not tool_def or not tool_def.get("supports_hooks"):
        return None
    return install_hooks_for_tool(tool_def["config_path"], dry_run=dry_run)


def uninstall_hooks_for(tool_id: str, *, dry_run: bool = False) -> dict | None:
    """Remove Memee hooks from the named tool's settings.json."""
    from memee.hooks_config import uninstall_hooks_for_tool

    tool_def = next((t for t in AI_TOOLS if t["id"] == tool_id), None)
    if not tool_def or not tool_def.get("supports_hooks"):
        return None
    return uninstall_hooks_for_tool(tool_def["config_path"], dry_run=dry_run)


def install_hooks_all(*, dry_run: bool = False) -> list[dict]:
    """Install hooks for every detected tool that supports them.

    Returns a list of result dicts (one per tool that supports hooks) with
    a ``tool`` key added so the caller can label them.
    """
    results = []
    for tool in detect_ai_tools():
        if not tool.get("supports_hooks") or not tool["detected"]:
            continue
        res = install_hooks_for(tool["id"], dry_run=dry_run)
        if res is None:
            continue
        res["tool"] = tool["name"]
        res["tool_id"] = tool["id"]
        results.append(res)
    return results


def uninstall_hooks_all(*, dry_run: bool = False) -> list[dict]:
    """Remove Memee hooks from every detected tool that supports them."""
    results = []
    for tool in detect_ai_tools():
        if not tool.get("supports_hooks") or not tool["detected"]:
            continue
        res = uninstall_hooks_for(tool["id"], dry_run=dry_run)
        if res is None:
            continue
        res["tool"] = tool["name"]
        res["tool_id"] = tool["id"]
        results.append(res)
    return results


def get_rerank_health() -> dict:
    """Snapshot whether the cross-encoder reranker will fire on next search.

    Wrapper around ``reranker.rerank_status`` so the report layer can stay
    free of engine imports — and so a reranker import failure (e.g. an
    older install where the module hasn't shipped yet) degrades gracefully
    instead of breaking ``memee doctor``.
    """
    try:
        from memee.engine.reranker import rerank_status
        return rerank_status()
    except Exception as e:
        return {"error": str(e), "enabled": False}


def get_db_health() -> dict:
    """Check database health."""
    from memee import config
    from memee.storage.models import Memory

    db_path = config.settings.db_path
    result = {
        "exists": db_path.exists(),
        "path": str(db_path),
        "size_mb": 0,
        "memories": 0,
        "embedded": 0,
        "fts_healthy": False,
    }

    if not db_path.exists():
        return result

    result["size_mb"] = round(db_path.stat().st_size / 1024 / 1024, 1)

    try:
        from memee.storage.database import get_session, init_db
        session = get_session(init_db())
        from sqlalchemy import func, text

        result["memories"] = session.query(func.count(Memory.id)).scalar() or 0
        result["embedded"] = session.query(func.count(Memory.id)).filter(
            Memory.embedding.isnot(None)
        ).scalar() or 0

        # Test FTS
        try:
            session.execute(text("SELECT count(*) FROM memories_fts"))
            result["fts_healthy"] = True
        except Exception:
            pass

        session.close()
    except Exception:
        pass

    return result


def get_knowledge_health() -> dict:
    """Check knowledge maturity and health."""
    try:
        from memee.storage.database import get_session, init_db
        from memee.storage.models import Memory, MaturityLevel, MemoryConnection
        from sqlalchemy import func

        session = get_session(init_db())
        total = session.query(func.count(Memory.id)).scalar() or 0
        if total == 0:
            session.close()
            return {"empty": True}

        canon = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.CANON.value).scalar()
        validated = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.VALIDATED.value).scalar()
        stale = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.HYPOTHESIS.value,
            Memory.validation_count == 0,
        ).scalar()
        connections = session.query(func.count(MemoryConnection.source_id)).scalar()
        avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0

        session.close()
        return {
            "total": total,
            "canon": canon,
            "validated": validated,
            "stale_hypotheses": stale,
            "connections": connections,
            "avg_confidence": round(avg_conf, 3),
        }
    except Exception:
        return {"error": True}


def run_doctor(
    auto_fix: bool = True,
    install_hooks: bool = True,
    uninstall_hooks: bool = False,
    dry_run: bool = False,
    skip_install_fix: bool = False,
) -> dict:
    """Run full health check and return results.

    Args:
        auto_fix: when True, fix MCP misconfigurations.
        install_hooks: when True (default), also install Memee hooks into
            tools that support them (currently Claude Code only). Pass False
            with ``--no-hooks`` to wire only MCP.
        uninstall_hooks: when True, strip Memee hooks. Mutually exclusive
            with ``install_hooks`` — if both are set, uninstall wins (it's
            the more conservative action — the user explicitly asked).
        dry_run: when True, no files are written. Doctor reports what it
            *would* change so the user can preview before committing.
        skip_install_fix: when True, never run the destructive multi-install
            uninstall even if ``auto_fix`` is on. The CLI sets this when the
            user declines the interactive prompt or passes
            ``--ignore-multi-install``. The detection itself still runs so
            the report shows the warning.
    """
    results = {
        "tools": detect_ai_tools(),
        "database": get_db_health(),
        "knowledge": get_knowledge_health(),
        "rerank": get_rerank_health(),
        "installs": get_install_health(),
        "update": _update_status_for_report(),
        "issues": [],
        "fixed": [],
        "hooks": [],
        "dry_run": dry_run,
    }

    # Multi-install: warn always, auto-fix when safe. "Safe" = pip-managed
    # active install, no reverse deps, and a newer shadowed binary on PATH
    # to fall back to. Anything else falls through to the manual fix block.
    if results["installs"]["multi"]:
        installs = results["installs"]["installs"]
        active = installs[0]
        shadowed = installs[1:]
        results["issues"].append(
            {
                "type": "multi_install",
                "message": (
                    f"{results['installs']['count']} memee binaries on PATH "
                    f"(mismatch will cause command-not-found errors)"
                ),
            }
        )
        ok, reason = _can_safely_remove(active, shadowed)
        results["installs"]["fix_safe"] = ok
        results["installs"]["fix_reason"] = reason
        if auto_fix and ok and not skip_install_fix:
            outcome = _uninstall_active(active, dry_run=dry_run)
            results["installs"]["fix_outcome"] = outcome
            # Only mark "fixed" on a real, successful uninstall — dry-run
            # records intent but the broken state is still on disk.
            if outcome["ok"] and not outcome.get("dry_run"):
                results["fixed"].append("multi_install")
                # Drop the multi_install issue so the report says "FIXED"
                # cleanly rather than listing a now-resolved problem.
                results["issues"] = [
                    i for i in results["issues"]
                    if i.get("type") != "multi_install"
                ]
                # Invalidate the PATH scan cache: a follow-up `memee doctor`
                # in the same process would otherwise still see two installs.
                global _MEMEE_INSTALL_CACHE
                _MEMEE_INSTALL_CACHE = None

    # Check for issues
    for tool in results["tools"]:
        if tool["detected"] and not tool["configured"] and tool.get("can_auto_fix"):
            results["issues"].append({
                "type": "tool_not_configured",
                "tool": tool["name"],
                "tool_id": tool["id"],
                "message": f"{tool['name']} is installed but Memee is not configured",
            })

    db = results["database"]
    if not db["exists"]:
        results["issues"].append({
            "type": "no_database",
            "message": "Database not found. Run: memee init",
        })
    elif db["memories"] > 0 and db["embedded"] < db["memories"]:
        missing = db["memories"] - db["embedded"]
        results["issues"].append({
            "type": "missing_embeddings",
            "message": f"{missing} memories without embeddings. Run: memee embed",
        })

    kh = results["knowledge"]
    if not kh.get("empty") and not kh.get("error"):
        if kh.get("stale_hypotheses", 0) > 10:
            results["issues"].append({
                "type": "stale_knowledge",
                "message": f"{kh['stale_hypotheses']} unvalidated hypotheses. Consider running: memee dream",
            })

    # Auto-fix tool configs if requested
    if auto_fix and not dry_run:
        for issue in results["issues"]:
            if issue["type"] == "tool_not_configured":
                try:
                    success = configure_tool(issue["tool_id"])
                except RuntimeError as e:
                    # Invalid JSON → configure_tool already backed up + warned.
                    # Attach the issue so the caller can display it.
                    issue["message"] = f"{issue['message']}: {e}"
                    continue
                if success:
                    results["fixed"].append(issue["tool"])

        # Zombie research-experiment sweep was removed with the research
        # engine (v2.0.0). The whole experiment tracker is gone, no zombies.

    # Hook layer: this is what makes Memee fully automatic. We only act on
    # tools that report ``supports_hooks=True`` AND are detected. The
    # uninstall path runs first so a "doctor --uninstall-hooks" doesn't
    # accidentally re-install one second later.
    if uninstall_hooks:
        results["hooks"] = uninstall_hooks_all(dry_run=dry_run)
    elif install_hooks:
        # Re-detect after MCP fixes so "claude_code" appears configured
        # before we lay hooks on top. Same data structure either way.
        results["hooks"] = install_hooks_all(dry_run=dry_run)

    return results


def print_installations_section(install_health: dict) -> None:
    """Print the ``Installations:`` block.

    Single-install (or all-symlinks-to-one) → green check, one line.
    Multi-install → yellow warning + table + fix block tailored to the
    *active* (PATH-first) install's kind.
    """
    installs = install_health.get("installs") or []
    if not installs:
        # No memee on PATH at all — extreme edge case (running from source
        # via ``python -m memee.cli`` only). Stay quiet rather than alarm.
        return

    print(f"\n  {C.BOLD}Installations:{C.RESET}")

    if len(installs) == 1:
        sole = installs[0]
        version = sole.get("version") or "?"
        kind = _install_kind_label(sole["install_kind"])
        print(
            f"    {C.GREEN}✓{C.RESET} memee {version} ({kind})  "
            f"{C.DIM}{sole['path']}{C.RESET}"
        )
        return

    # Multi-install — first PATH entry wins, the rest are shadowed.
    active = installs[0]
    shadowed = installs[1:]
    outcome = install_health.get("fix_outcome")
    fixed_ok = bool(outcome and outcome.get("ok") and not outcome.get("dry_run"))
    would_fix = bool(outcome and outcome.get("dry_run"))

    if fixed_ok:
        banner = f"{C.GREEN}✓{C.RESET}"
        note = (
            f"{C.DIM}(removed the shadowing install — "
            f"open a new shell or run `hash -r` to refresh PATH){C.RESET}"
        )
    elif would_fix:
        banner = f"{C.CYAN}~{C.RESET}"
        note = f"{C.DIM}(dry run — would remove the active install){C.RESET}"
    else:
        banner = f"{C.YELLOW}!{C.RESET}"
        note = f"{C.DIM}(mismatch will cause command-not-found errors){C.RESET}"
    print(f"    {banner} {len(installs)} memee binaries on PATH {note}")

    # Compute column widths so the table lines up — but keep it simple,
    # we only have at most a few binaries.
    rows = []
    for i, inst in enumerate(installs):
        version = inst.get("version") or "?"
        kind = _install_kind_label(inst["install_kind"])
        if i == 0:
            if fixed_ok:
                tag = "[removed]"
            elif would_fix:
                tag = "[would remove]"
            else:
                tag = "[active]"
        else:
            tag = "[promoted]" if (fixed_ok and i == 1) else "[shadowed]"
        rows.append((inst["path"], f"v{version}" if not version.startswith("v") else version, kind, tag))

    pad_path = max(len(r[0]) for r in rows)
    pad_ver = max(len(r[1]) for r in rows)
    pad_kind = max(len(r[2]) for r in rows)
    for path, ver, kind, tag in rows:
        if tag in ("[removed]", "[would remove]"):
            tag_color = C.RED
        elif tag in ("[active]", "[promoted]"):
            tag_color = C.GREEN
        else:
            tag_color = C.DIM
        print(
            f"      {path:<{pad_path}}  {ver:<{pad_ver}}  "
            f"{kind:<{pad_kind}}  {tag_color}{tag}{C.RESET}"
        )

    if fixed_ok:
        # Show what pip actually did. Not a wall of text — pip's "Successfully
        # uninstalled memee-X" line is what users want to see. Stay quiet
        # otherwise.
        cmd = " ".join(outcome.get("command") or [])
        print()
        print(f"    {C.BOLD}Fixed:{C.RESET} {C.DIM}{cmd}{C.RESET}")
        for line in (outcome.get("stdout") or "").splitlines():
            line = line.strip()
            if line.startswith("Successfully uninstalled") or line.startswith("Found existing"):
                print(f"      {C.DIM}{line}{C.RESET}")
        return

    if would_fix:
        cmd = " ".join(outcome.get("command") or [])
        print()
        print(f"    {C.BOLD}Would fix:{C.RESET} {C.DIM}{cmd}{C.RESET}")
        return

    # Failure path: outcome exists but ok=False — pip ran and rejected it.
    if outcome and not outcome.get("ok"):
        cmd = " ".join(outcome.get("command") or [])
        print()
        print(f"    {C.BOLD}{C.RED}Auto-fix failed:{C.RESET} {C.DIM}{cmd}{C.RESET}")
        for line in (outcome.get("stderr") or "").splitlines()[-5:]:
            print(f"      {C.DIM}{line}{C.RESET}")
        # Fall through to the manual hint so the user has a path forward.

    # No outcome → either auto-fix wasn't safe, or user declined. Show why,
    # then the manual fix.
    reason = install_health.get("fix_reason") or ""
    if reason:
        print()
        print(f"    {C.DIM}Auto-fix not run: {reason}{C.RESET}")
    print()
    print(f"    {C.BOLD}Fix:{C.RESET}")
    for line in _fix_hint(active, shadowed[0]):
        print(f"      {line}")
    print()
    print(f"    {C.DIM}Then run `memee doctor` again to verify.{C.RESET}")


def print_doctor_report(results: dict):
    """Print formatted doctor report."""
    print(f"\n  {C.BCYAN}━━━ MEMEE HEALTH CHECK ━━━{C.RESET}\n")

    # Database
    db = results["database"]
    print(f"  {C.BOLD}Database:{C.RESET}")
    if db["exists"]:
        print(f"    {C.GREEN}✓{C.RESET} {db['path']} ({db['size_mb']} MB, {db['memories']} memories)")
        fts = f"{C.GREEN}✓{C.RESET} healthy" if db["fts_healthy"] else f"{C.RED}✗{C.RESET} broken"
        print(f"    {fts} FTS5 index")
        if db["memories"] > 0:
            emb_pct = db["embedded"] / db["memories"] * 100
            emb_icon = f"{C.GREEN}✓" if emb_pct == 100 else f"{C.YELLOW}!"
            print(f"    {emb_icon}{C.RESET} Embeddings: {db['embedded']}/{db['memories']} ({emb_pct:.0f}%)")
    else:
        print(f"    {C.RED}✗{C.RESET} Not found. Run: memee init")

    # Installations (multi-install detection — v2.0.1)
    print_installations_section(results.get("installs") or {})

    # AI Tools
    print(f"\n  {C.BOLD}AI Tools:{C.RESET}")
    mcp_tool_ids = {t["id"] for t in AI_TOOLS}
    any_mcp_detected = False
    for tool in results["tools"]:
        if tool["configured"]:
            icon = f"{C.GREEN}✓{C.RESET}"
            status = "configured"
            if tool.get("note"):
                status = tool["note"]
        elif tool["detected"]:
            icon = f"{C.YELLOW}!{C.RESET}"
            status = f"found but {C.BYELLOW}NOT configured{C.RESET}"
        else:
            icon = f"{C.DIM}-{C.RESET}"
            status = f"{C.DIM}not installed{C.RESET}"

        if tool["detected"] and tool["id"] in mcp_tool_ids:
            any_mcp_detected = True

        # Append hook status for tools that support hooks. We add a small
        # tag so the user can see at a glance whether the loop is wired.
        if tool.get("supports_hooks"):
            if tool.get("hooks_configured"):
                status += f" {C.DIM}+ hooks{C.RESET}"
            elif tool["configured"]:
                status += f" {C.DIM}(no hooks){C.RESET}"
        elif tool["detected"] and tool["id"] in {"cursor", "windsurf", "claude_desktop"}:
            # Be honest about why some tools don't get the full automatic
            # experience: their config file has no harness-level hooks block.
            status += f" {C.DIM}(MCP only — client has no hooks){C.RESET}"

        config_hint = ""
        if tool.get("config_path") and tool["configured"]:
            config_hint = f"  {C.DIM}{tool['config_path']}{C.RESET}"

        print(f"    {icon} {tool['name']:<18s} {status}{config_hint}")

    # Hook installation report — what was just written (or would be in
    # dry-run). Only print this section if doctor actually touched hooks.
    hook_results = results.get("hooks") or []
    if hook_results:
        dry = " (dry run — no changes)" if results.get("dry_run") else ""
        print(f"\n  {C.BOLD}Hooks{dry}:{C.RESET}")
        for hr in hook_results:
            label = hr.get("tool", hr.get("path", "?"))
            if hr.get("skipped_reason"):
                print(
                    f"    {C.YELLOW}!{C.RESET} {label}: "
                    f"{C.DIM}{hr['skipped_reason']}{C.RESET}"
                )
                continue
            diff = hr.get("diff", {})
            added = sum(len(v) for v in diff.get("added", {}).values())
            removed = sum(len(v) for v in diff.get("removed", {}).values())
            changed = sum(len(v) for v in diff.get("changed", {}).values())
            wrote = hr.get("wrote", False)
            backup = hr.get("backup_path")
            if wrote or results.get("dry_run"):
                action = "would write" if results.get("dry_run") else "wrote"
                print(
                    f"    {C.GREEN}✓{C.RESET} {label}: {action} "
                    f"+{added} ~{changed} -{removed} hooks"
                    + (f"  {C.DIM}backup: {backup}{C.RESET}" if backup else "")
                )
            else:
                print(
                    f"    {C.DIM}-{C.RESET} {label}: nothing to do"
                )

    # Show the manual snippet when no MCP client was detected — mirrors the
    # installer behaviour, so an agent running in an unsupported client still
    # learns how to wire Memee in. (CLI-only tools like ollama don't count.)
    if not any_mcp_detected:
        print()
        print(f"    {C.DIM}No MCP client detected. If you use one not auto-configured,{C.RESET}")
        print(f"    {C.DIM}add this to its settings file:{C.RESET}")
        print(f'      {C.BCYAN}{{"mcpServers": {{"memee": {{"command": "memee", "args": ["serve"]}}}}}}{C.RESET}')

    # Cross-encoder reranker status. Default-on when the HF cache is warm;
    # off + actionable hint when the weights aren't on disk.
    rr = results.get("rerank") or {}
    print(f"\n  {C.BOLD}Reranker:{C.RESET}")
    if rr.get("enabled"):
        if rr.get("source") == "env_explicit":
            print(f"    {C.GREEN}✓{C.RESET} rerank: enabled (MEMEE_RERANK_MODEL)")
        else:
            print(f"    {C.GREEN}✓{C.RESET} rerank: enabled (cached)")
    else:
        if rr.get("source") == "kill_switch":
            print(f"    {C.YELLOW}!{C.RESET} rerank: disabled (MEMEE_RERANK kill switch)")
        elif rr.get("error"):
            print(f"    {C.YELLOW}!{C.RESET} rerank: disabled ({rr['error']})")
        else:
            print(f"    {C.YELLOW}!{C.RESET} rerank: disabled (no model cached; pip install memee[rerank] then memee embed --download-rerank)")

    # Update check — passive PyPI poll, 24h cache. Honest about what we
    # know: ✓ when up to date, ! when an upgrade is available with the
    # one-liner the user needs, dim "—" when offline / disabled / can't
    # determine.
    upd = results.get("update") or {}
    print(f"\n  {C.BOLD}Update:{C.RESET}")
    if upd.get("source") == "disabled":
        print(f"    {C.DIM}-{C.RESET} disabled (MEMEE_NO_UPDATE_CHECK)")
    elif upd.get("available") and upd.get("latest"):
        print(
            f"    {C.YELLOW}!{C.RESET} memee {upd.get('current')} → "
            f"{upd['latest']} available"
        )
        print(f"      {C.BOLD}Run:{C.RESET} pipx upgrade memee")
    elif upd.get("latest"):
        print(
            f"    {C.GREEN}✓{C.RESET} up to date "
            f"({upd.get('current')} = latest on PyPI)"
        )
    else:
        print(
            f"    {C.DIM}-{C.RESET} couldn't reach PyPI "
            f"(offline?) — running {upd.get('current') or '?'}"
        )

    # Knowledge Health
    kh = results["knowledge"]
    if not kh.get("empty") and not kh.get("error"):
        print(f"\n  {C.BOLD}Knowledge:{C.RESET}")
        print(f"    {C.GREEN}✓{C.RESET} Canon: {kh.get('canon', 0)} memories")
        print(f"    {C.GREEN}✓{C.RESET} Validated: {kh.get('validated', 0)} memories")
        print(f"    {C.GREEN}✓{C.RESET} Avg confidence: {kh.get('avg_confidence', 0):.0%}")
        print(f"    {C.GREEN}✓{C.RESET} Graph: {kh.get('connections', 0)} connections")
        stale = kh.get("stale_hypotheses", 0)
        if stale > 10:
            print(f"    {C.YELLOW}!{C.RESET} {stale} unvalidated hypotheses (run: memee dream)")
        else:
            print(f"    {C.GREEN}✓{C.RESET} Stale hypotheses: {stale}")

    # Issues + Fixes
    issues = [i for i in results["issues"] if i.get("tool", "") not in results.get("fixed", [])]
    fixed = results.get("fixed", [])

    if fixed:
        print(f"\n  {C.BGREEN}━━━ FIXED ━━━{C.RESET}")
        for name in fixed:
            if name == "multi_install":
                print(
                    f"    {C.GREEN}✓{C.RESET} Removed the shadowing memee "
                    f"install. Open a new shell (or run `hash -r`) to refresh PATH."
                )
            else:
                print(f"    {C.GREEN}✓{C.RESET} {name} configured. Restart to activate.")

    if issues:
        print(f"\n  {C.BYELLOW}━━━ ISSUES ({len(issues)}) ━━━{C.RESET}")
        for issue in issues:
            print(f"    {C.YELLOW}!{C.RESET} {issue['message']}")
    else:
        print(f"\n  {C.BGREEN}━━━ ALL HEALTHY ━━━{C.RESET}")

    print()
