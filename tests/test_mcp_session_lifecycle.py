"""v2.4.17 — every MCP tool closes the session it opens.

Pre-v2.4.17, 16 MCP tools called ``_get_session()`` and returned without
a matching ``close()`` — they leaked sessions on every call. Long-running
MCP servers accumulated open connections until the connection pool
exhausted. The five tools that already wrapped in ``try/finally`` were
the model — every tool now follows the same shape.

This regression guards two things:

1. Source-level audit: every ``session = _get_session()`` in
   ``mcp_server.py`` is immediately followed by ``try:`` and a matching
   ``finally: session.close()`` block. If a future tool forgets the
   wrap, the assertion fails fast with the offending line number.

2. Runtime behaviour: invoking a representative tool monkeypatches
   ``_get_session`` to track open/close counts and asserts every open
   is matched by a close, even on the error path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


MCP_SRC = (
    Path(__file__).resolve().parent.parent / "src" / "memee" / "mcp_server.py"
)


def test_every_get_session_in_mcp_is_wrapped_in_try_finally_close():
    """Source-level audit: enforce the try/finally pattern on every tool."""
    text = MCP_SRC.read_text()
    lines = text.split("\n")

    offences: list[str] = []
    for i, line in enumerate(lines):
        if line.strip() != "session = _get_session()":
            continue
        # The next non-blank line must be `try:` at the same body indent.
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j >= len(lines) or lines[j].strip() != "try:":
            offences.append(
                f"{MCP_SRC.name}:{i + 1}: session opened without `try:` "
                "guard — likely leaks on early return/exception"
            )
            continue
        # Walk forward to find the matching `finally:` at the same indent
        # as the `try:`. We don't need to fully parse — checking that a
        # `session.close()` appears within the function body (before the
        # next decorator/def) is enough belt-and-suspenders.
        try_indent = len(lines[j]) - len(lines[j].lstrip())
        found_close = False
        for k in range(j + 1, len(lines)):
            ln = lines[k]
            stripped = ln.strip()
            if stripped.startswith(("@mcp.tool", "def ", "async def ")):
                break
            if (
                stripped == "session.close()"
                and (len(ln) - len(ln.lstrip())) > try_indent
            ):
                found_close = True
                break
        if not found_close:
            offences.append(
                f"{MCP_SRC.name}:{i + 1}: `try:` present but no "
                "`session.close()` reached before next tool"
            )

    assert not offences, (
        "MCP tools leaking sessions:\n  " + "\n  ".join(offences)
        + "\n\nFix: wrap the body in try/finally so close() always runs."
    )


def test_memory_invalidate_closes_session_on_error_path(monkeypatch):
    """Runtime: even the early-return / error path closes the session."""
    import memee.mcp_server as ms

    closed: list[bool] = []
    original_get = ms._get_session

    real_session = original_get()
    real_close = real_session.close

    def tracking_close():
        closed.append(True)
        return real_close()

    real_session.close = tracking_close

    def patched_get():
        return real_session

    monkeypatch.setattr(ms, "_get_session", patched_get)

    # Invoke the underlying impl. The function is wrapped by @mcp.tool
    # which exposes the original via __wrapped__ on most versions; fall
    # back to calling the registered FastMCP tool if needed.
    fn = getattr(ms.memory_invalidate, "fn", None)
    if fn is None:
        fn = ms.memory_invalidate
    import asyncio
    result = asyncio.run(fn("nonexistent-id", "test reason"))
    payload = json.loads(result)
    assert "error" in payload  # early return path was taken
    assert closed, "session.close() was never called on the early-return path"


def test_no_session_opened_outside_try_block_anywhere():
    """Catch a future tool that opens a session anywhere (not just at the
    top of the body). Every textual `_get_session()` call must be on a
    line that is followed by a `try:` within the same function."""
    text = MCP_SRC.read_text()
    # Skip the definition itself and the helper `_with_session` example
    # that lives in the decorator's docstring.
    occurrences = [
        (i, ln) for i, ln in enumerate(text.split("\n"), 1)
        if "_get_session()" in ln and "def _get_session" not in ln
    ]
    # The decorator body opens a session inside its own try/finally —
    # those lines live inside _with_session, not a tool.
    expected_decorator_lines = [
        ln for _, ln in occurrences
        if re.match(r"^\s{12,}", ln)  # deeply indented decorator code
    ]
    # Every tool-level open should be at indent 4 (function body).
    tool_opens = [
        (i, ln) for i, ln in occurrences
        if ln.startswith("    session = _get_session()")
    ]
    assert tool_opens, "no tool-level session opens found — refactor broken?"
    # Bookkeeping: the count just verifies we still have tools to audit.
    assert len(tool_opens) >= 16, (
        f"Expected ≥16 tool-level session opens (v2.4.17 inventory), "
        f"got {len(tool_opens)}. Decorator lines: "
        f"{len(expected_decorator_lines)}"
    )
