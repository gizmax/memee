"""Interactive CLI installer with rich terminal UI.

Beautiful onboarding experience:
  memee setup        → guided setup for solo dev
  memee setup team   → team server setup
  memee setup join   → join existing team

Uses ANSI colors, box drawing, animations, and progressive disclosure.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

# ANSI color codes
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    # Colors
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"
    WHITE = "\033[97m"
    # Bright
    BCYAN = "\033[96m"
    BGREEN = "\033[92m"
    BYELLOW = "\033[93m"
    BRED = "\033[91m"
    BMAGENTA = "\033[95m"
    # Gradient helpers (kept for compatibility; not used by LOGO any more)
    G1 = "\033[38;5;39m"
    G2 = "\033[38;5;75m"
    G3 = "\033[38;5;111m"
    G4 = "\033[38;5;147m"
    G5 = "\033[38;5;183m"
    BG_DARK = "\033[48;5;234m"
    # Brand accent — cyan-mint #00E5C7, same as `.accent` on memee.eu.
    # Uses truecolor (24-bit) ANSI; degrades to bright cyan on legacy terms.
    BRAND = "\033[38;2;0;229;199m"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(s: str) -> int:
    """Length of `s` ignoring ANSI colour escapes, so box padding lands correctly."""
    return len(_ANSI_RE.sub("", s))


LOGO = f"""
{C.BRAND}  ███╗   ███╗███████╗███╗   ███╗███████╗███████╗
{C.BRAND}  ████╗ ████║██╔════╝████╗ ████║██╔════╝██╔════╝
{C.BRAND}  ██╔████╔██║█████╗  ██╔████╔██║█████╗  █████╗
{C.BRAND}  ██║╚██╔╝██║██╔══╝  ██║╚██╔╝██║██╔══╝  ██╔══╝
{C.BRAND}  ██║ ╚═╝ ██║███████╗██║ ╚═╝ ██║███████╗███████╗
{C.BRAND}  ╚═╝     ╚═╝╚══════╝╚═╝     ╚═╝╚══════╝╚══════╝{C.RESET}
"""

# Farewell logo, same ANSI-Shadow font, same brand colour.
# R E M E M B E R  (70 columns wide)
LOGO_REMEMBER = f"""
{C.BRAND}  ██████╗ ███████╗███╗   ███╗███████╗███╗   ███╗██████╗ ███████╗██████╗
{C.BRAND}  ██╔══██╗██╔════╝████╗ ████║██╔════╝████╗ ████║██╔══██╗██╔════╝██╔══██╗
{C.BRAND}  ██████╔╝█████╗  ██╔████╔██║█████╗  ██╔████╔██║██████╔╝█████╗  ██████╔╝
{C.BRAND}  ██╔══██╗██╔══╝  ██║╚██╔╝██║██╔══╝  ██║╚██╔╝██║██╔══██╗██╔══╝  ██╔══██╗
{C.BRAND}  ██║  ██║███████╗██║ ╚═╝ ██║███████╗██║ ╚═╝ ██║██████╔╝███████╗██║  ██║
{C.BRAND}  ╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝╚══════╝╚═╝     ╚═╝╚═════╝ ╚══════╝╚═╝  ╚═╝{C.RESET}
"""

TAGLINE = f"  {C.DIM}Your agents forget. Memee doesn't.{C.RESET}"


def _clear():
    os.system("cls" if os.name == "nt" else "clear")


def _type(text: str, delay: float = 0.015):
    """Type text with animation effect."""
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        if delay > 0 and char not in ("\n", " "):
            time.sleep(delay)
    print()


def _box(lines: list[str], color: str = C.CYAN, width: int = 60):
    """Draw a box around text. Padding is ANSI-escape aware, so colored
    lines land on the right border instead of falling short by ~8 chars."""
    print(f"  {color}╭{'─' * width}╮{C.RESET}")
    for line in lines:
        pad = max(0, (width - 2) - _visible_len(line))
        print(f"  {color}│{C.RESET} {line}{' ' * pad} {color}│{C.RESET}")
    print(f"  {color}╰{'─' * width}╯{C.RESET}")


def _progress(label: str, steps: list[str], color: str = C.GREEN):
    """Animated progress steps."""
    for i, step in enumerate(steps):
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        for frame in range(8):
            sys.stdout.write(
                f"\r  {color}{spinner[frame % len(spinner)]}{C.RESET} {step}..."
            )
            sys.stdout.flush()
            time.sleep(0.05)
        sys.stdout.write(f"\r  {color}✓{C.RESET} {step}   \n")


def _ask(prompt: str, options: list[str] | None = None, default: str = "") -> str:
    """Interactive prompt with options."""
    if options:
        print()
        for i, opt in enumerate(options, 1):
            marker = f"{C.BCYAN}›{C.RESET}" if i == 1 else " "
            print(f"  {marker} {C.BOLD}{i}{C.RESET}. {opt}")
        print()
        choice = input(f"  {C.DIM}Choose [1-{len(options)}]{C.RESET}: ").strip()
        idx = int(choice) - 1 if choice.isdigit() else 0
        return options[min(idx, len(options) - 1)]
    else:
        if default:
            result = input(f"  {prompt} {C.DIM}({default}){C.RESET}: ").strip()
            return result or default
        return input(f"  {prompt}: ").strip()


def _section(title: str, color: str = C.BCYAN):
    """Section header."""
    print(f"\n  {color}{'━' * 50}{C.RESET}")
    print(f"  {color}{C.BOLD}{title}{C.RESET}")
    print(f"  {color}{'━' * 50}{C.RESET}\n")


def run_setup():
    """Main setup wizard."""
    _clear()
    print(LOGO)
    print(TAGLINE)
    print()

    _box([
        f"{C.BOLD}Welcome to Memee{C.RESET}",
        "",
        "Institutional memory for AI agent teams.",
        "Cross-project • Cross-model • Self-improving",
        "",
        f"{C.DIM}Let's get you set up in 60 seconds.{C.RESET}",
    ], color=C.G3)

    # ── Step 1: Mode ──
    _section("STEP 1: Choose your setup")

    mode = _ask("How will you use Memee?", [
        f"{C.BGREEN}Solo developer{C.RESET} — just me and my AI models (free)",
        f"{C.BYELLOW}Team member{C.RESET} — join an existing team",
        f"{C.BMAGENTA}Team lead{C.RESET} — set up a new team server",
    ])

    if "Solo" in mode:
        _setup_solo()
    elif "join" in mode:
        _setup_join()
    else:
        _setup_team_lead()


def _setup_solo():
    """Solo developer setup."""
    _section("STEP 2: Your profile")

    name = _ask("Your name", default=os.getenv("USER", "developer"))
    org_name = _ask("Organization name", default="personal")

    _section("STEP 3: Your stack")

    print(f"  {C.DIM}Select your primary technologies:{C.RESET}\n")
    stacks = {
        "1": ("Python", ["Python", "FastAPI", "SQLite"]),
        "2": ("JavaScript/TypeScript", ["React", "TypeScript", "Node.js"]),
        "3": ("Swift/iOS", ["Swift", "SwiftUI", "CoreData"]),
        "4": ("Go", ["Go", "Gin", "PostgreSQL"]),
        "5": ("Full-stack", ["Python", "FastAPI", "React", "PostgreSQL"]),
    }

    for key, (label, _) in stacks.items():
        print(f"    {C.BOLD}{key}{C.RESET}. {label}")

    choice = input(f"\n  {C.DIM}Choose [1-5, or type custom]{C.RESET}: ").strip()
    if choice in stacks:
        stack_name, stack = stacks[choice]
    else:
        stack = [s.strip() for s in choice.split(",")]
        stack_name = ", ".join(stack)

    # ── Step 4: AI Models ──
    _section("STEP 4: Your AI models")

    print(f"  {C.DIM}Which AI models do you use? (all share the same memory){C.RESET}\n")
    models = []
    model_options = [
        ("Claude (Anthropic)", "claude-opus-4"),
        ("GPT-4 (OpenAI)", "gpt-4o"),
        ("Gemini (Google)", "gemini-2.0-flash"),
        ("Llama (local)", "llama-3.1"),
        ("Other", ""),
    ]
    for i, (label, _) in enumerate(model_options, 1):
        print(f"    {C.BOLD}{i}{C.RESET}. {label}")

    choices = input(f"\n  {C.DIM}Choose (comma-separated, e.g. 1,2,3){C.RESET}: ").strip()
    for c in choices.split(","):
        c = c.strip()
        if c.isdigit() and 1 <= int(c) <= len(model_options):
            _, model = model_options[int(c) - 1]
            if model:
                models.append(model)

    if not models:
        models = ["claude-opus-4"]

    # ── Install ──
    _section("INSTALLING")

    _progress("Setting up", [
        "Creating database",
        f"Initializing organization '{org_name}'",
        f"Configuring stack: {stack_name}",
        f"Setting up {len(models)} AI model(s)",
        "Generating embeddings index",
        "Running first Dream cycle",
    ])

    # Actually do the setup. OSS is single-user: we create the Organization
    # (tenant container every Project requires) and a default Project. The
    # paid `memee-team` package adds User and Team on top.
    from memee.storage.database import init_db, get_session
    from memee.storage.models import Organization, Project

    engine = init_db()
    session = get_session(engine)

    existing_org = session.query(Organization).filter_by(name=org_name).first()
    if not existing_org:
        org = Organization(name=org_name)
        session.add(org)
        session.flush()

        proj = Project(
            organization_id=org.id,
            name="Default",
            path=str(Path.cwd()),
            stack=stack,
            tags=[s.lower() for s in stack[:2]],
        )
        session.add(proj)
        session.commit()

    # ── Auto-configure AI tools ──
    _section("CONFIGURING AI TOOLS")

    from memee.doctor import detect_ai_tools, configure_tool

    tools = detect_ai_tools()
    configured_tools = []
    for tool in tools:
        if tool["detected"] and not tool["configured"] and tool.get("can_auto_fix"):
            sys.stdout.write(f"  {C.GREEN}✓{C.RESET} {tool['name']:<18s} found → configuring... ")
            sys.stdout.flush()
            success = configure_tool(tool["id"])
            print(f"{C.GREEN}✓ done{C.RESET}" if success else f"{C.RED}✗ failed{C.RESET}")
            if success:
                configured_tools.append(tool["name"])
        elif tool["detected"] and tool["configured"]:
            print(f"  {C.GREEN}✓{C.RESET} {tool['name']:<18s} already configured")
            configured_tools.append(tool["name"])
        elif tool["detected"]:
            note = tool.get("note", "use via CLI")
            print(f"  {C.GREEN}✓{C.RESET} {tool['name']:<18s} {note}")
        else:
            print(f"  {C.DIM}-{C.RESET} {tool['name']:<18s} not installed")

    tools_str = ", ".join(configured_tools) if configured_tools else "none (run memee doctor later)"

    # ── Success ──
    print()
    _box([
        f"{C.BGREEN}✓ Memee is ready!{C.RESET}",
        "",
        "  Database:  ~/.memee/memee.db",
        f"  Org:       {org_name}",
        f"  Stack:     {stack_name}",
        f"  Models:    {', '.join(models)}",
        f"  Tools:     {tools_str}",
        "  Scope:     personal (free tier)",
    ], color=C.GREEN, width=55)

    # ── You're done. Say so clearly. ──
    _section("YOU'RE DONE")

    print(f"  {C.BOLD}Memee is now live and fully automatic.{C.RESET}")
    print()
    print("  From this moment, every time your AI assistant works on a task:")
    print(f"  {C.GREEN}•{C.RESET} it sees only the memories that matter for that task (routed)")
    print(f"  {C.GREEN}•{C.RESET} what it learns is recorded, scored, and shared with other models")
    print(f"    ({C.BCYAN}Claude ↔ GPT ↔ Gemini ↔ Llama{C.RESET}) and other projects on this machine")
    print(f"  {C.GREEN}•{C.RESET} mistakes it catches are remembered org-wide, forever")
    print()
    print(f"  {C.DIM}With Memee Team (from $49/mo), the same memory is shared across")
    print(f"  every developer in your company, not just your laptop.{C.RESET}")
    print()

    _section("YOU CAN JUST TALK TO YOUR AGENT")

    print("  The MCP hooks are wired. You don't need any of the commands below.")
    print("  Just ask your AI assistant, in plain English:")
    print()
    print(f"  {C.BCYAN}\"Search Memee for patterns about API timeouts\"{C.RESET}")
    print(f"  {C.BCYAN}\"Record that we should always use connection pooling\"{C.RESET}")
    print(f"  {C.BCYAN}\"Check if there are anti-patterns for PDF processing\"{C.RESET}")
    print()

    _section("CLI (OPTIONAL)")

    print(f"  {C.DIM}If you prefer the command line, these shortcuts work.")
    print(f"  Paste the command itself, not the leading prompt marker.{C.RESET}")
    print()

    commands = [
        ("Record a pattern", 'memee record pattern "Always use timeout" -t python,api'),
        ("Search memories",  'memee search "timeout API"'),
        ("Check anti-patterns", 'memee check "processing PDF files"'),
        ("Run the dashboard", 'memee dashboard'),
        ("Reproduce the benchmarks", 'memee benchmark'),
    ]

    for label, cmd in commands:
        print(f"  {C.DIM}{label}{C.RESET}")
        print(f"      {C.BCYAN}{cmd}{C.RESET}\n")

    # MCP setup — only talk about it if something wasn't auto-wired.
    if configured_tools:
        print(f"  {C.DIM}Your AI tools are already wired for Memee:"
              f" {', '.join(configured_tools)}.{C.RESET}")
        print(f"  {C.DIM}Nothing to add manually.{C.RESET}")
    else:
        print(f"  {C.BYELLOW}No supported AI tool was detected.{C.RESET} If you use an MCP")
        print("  client (Cursor, Continue, Windsurf, Claude Code), add this to its")
        print("  settings file:")
        print(f'      {C.DIM}{{"mcpServers": {{"memee": {{"command": "memee", "args": ["serve"]}}}}}}{C.RESET}')
    print()

    _type(f"  {C.DIM}The next pattern your agent learns is the last time your team learns it twice.{C.RESET}", delay=0.02)
    print(LOGO_REMEMBER)


def _setup_join():
    """Join an existing team."""
    _section("JOIN A TEAM")

    token = _ask("Team invite token")
    _upgrade_cta(
        "Joining a team requires Memee Team.",
        "Install the licence-gated `memee-team` package, then rerun.",
    )


def _setup_team_lead():
    """Set up a new team (requires memee-team)."""
    _upgrade_cta(
        "Creating a team requires Memee Team.",
        "OSS `memee` is single-user by design. Team and org scope, SSO,",
        "and audit log live in the paid `memee-team` package.",
    )


def _upgrade_cta(*lines: str) -> None:
    _box(
        [
            f"{C.BYELLOW}Memee Team required{C.RESET}",
            "",
            *lines,
            "",
            f"  Get a licence: {C.BCYAN}https://memee.eu/#pricing{C.RESET}",
            f"  Install:       {C.BCYAN}pip install memee-team{C.RESET}",
        ],
        color=C.YELLOW,
        width=58,
    )
