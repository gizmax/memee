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
    },
    {
        "id": "claude_desktop",
        "name": "Claude Desktop",
        "detect_path": Path.home() / "Library" / "Application Support" / "Claude",
        "config_path": Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        "config_key": "mcpServers",
        "mcp_entry": {"memee": {"command": "memee", "args": ["serve"]}},
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "detect_path": Path.home() / "Library" / "Application Support" / "Cursor",
        "config_path": Path.home() / ".cursor" / "mcp.json",
        "config_key": "mcpServers",
        "mcp_entry": {"memee": {"command": "memee", "args": ["serve"]}},
    },
    {
        "id": "windsurf",
        "name": "Windsurf",
        "detect_path": Path.home() / "Library" / "Application Support" / "Windsurf",
        "config_path": Path.home() / ".codeium" / "windsurf" / "mcp_config.json",
        "config_key": "mcpServers",
        "mcp_entry": {"memee": {"command": "memee", "args": ["serve"]}},
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


def detect_ai_tools() -> list[dict]:
    """Scan system for installed AI tools and their MCP config status."""
    results = []

    for tool in AI_TOOLS:
        detected = tool["detect_path"].exists()
        configured = False
        config_exists = False

        if detected and tool.get("config_path"):
            config_path = tool["config_path"]
            if config_path.exists():
                config_exists = True
                try:
                    config = json.loads(config_path.read_text())
                    servers = config.get(tool["config_key"], {})
                    configured = "memee" in servers
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


def run_doctor(auto_fix: bool = True) -> dict:
    """Run full health check and return results."""
    results = {
        "tools": detect_ai_tools(),
        "database": get_db_health(),
        "knowledge": get_knowledge_health(),
        "issues": [],
        "fixed": [],
    }

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
    if auto_fix:
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

        # Sweep zombie research experiments left by Ctrl-C / crash.
        try:
            from memee.engine.research import reset_zombie_experiments
            from memee.storage.database import get_session, init_db

            session = get_session(init_db())
            zombie_count = reset_zombie_experiments(session)
            session.close()
            if zombie_count:
                results["fixed"].append(
                    f"reset {zombie_count} zombie experiment"
                    f"{'s' if zombie_count != 1 else ''}"
                )
        except Exception:
            # Non-critical; don't break doctor over this.
            pass

    return results


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

        config_hint = ""
        if tool.get("config_path") and tool["configured"]:
            config_hint = f"  {C.DIM}{tool['config_path']}{C.RESET}"

        print(f"    {icon} {tool['name']:<18s} {status}{config_hint}")

    # Show the manual snippet when no MCP client was detected — mirrors the
    # installer behaviour, so an agent running in an unsupported client still
    # learns how to wire Memee in. (CLI-only tools like ollama don't count.)
    if not any_mcp_detected:
        print()
        print(f"    {C.DIM}No MCP client detected. If you use one not auto-configured,{C.RESET}")
        print(f"    {C.DIM}add this to its settings file:{C.RESET}")
        print(f'      {C.BCYAN}{{"mcpServers": {{"memee": {{"command": "memee", "args": ["serve"]}}}}}}{C.RESET}')

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
            print(f"    {C.GREEN}✓{C.RESET} {name} configured. Restart to activate.")

    if issues:
        print(f"\n  {C.BYELLOW}━━━ ISSUES ({len(issues)}) ━━━{C.RESET}")
        for issue in issues:
            print(f"    {C.YELLOW}!{C.RESET} {issue['message']}")
    else:
        print(f"\n  {C.BGREEN}━━━ ALL HEALTHY ━━━{C.RESET}")

    print()
