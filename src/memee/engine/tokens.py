"""Token savings calculator: how many tokens does Memee save?

Without Memee, agents rediscover knowledge through conversation:
  - Ask model about best practice → 2-5K tokens
  - Debug when it doesn't work → 3-10K tokens
  - Ask about edge cases → 2-3K tokens
  - Total per discovery: 7-18K tokens

With Memee, agents get answers from memory:
  - MCP tool call → ~200 tokens
  - Memory result → ~300 tokens
  - Total per lookup: ~500 tokens

Savings: 7-18K → 500 = 93-97% reduction per knowledge retrieval.
At scale: 50 agents × 10 lookups/day × 250 days = 125K lookups/year.
"""

from __future__ import annotations

from dataclasses import dataclass

# Token cost estimates (per knowledge retrieval event)
# Based on typical Claude/GPT conversation patterns

# WITHOUT Memee: agent must discover knowledge through conversation
DISCOVERY_TOKENS = {
    "simple_question": {
        "input": 2000,    # Agent asks about a pattern
        "output": 1000,   # Model explains
        "total": 3000,
        "description": "Simple Q&A about a best practice",
    },
    "debugging": {
        "input": 5000,    # Agent shares code + error
        "output": 3000,   # Model diagnoses + suggests fix
        "total": 8000,
        "description": "Debugging a problem (would have been avoided)",
    },
    "research": {
        "input": 3000,    # Agent asks for alternatives
        "output": 2000,   # Model compares approaches
        "total": 5000,
        "description": "Researching the right approach",
    },
    "iteration": {
        "input": 4000,    # Agent shows failed attempt
        "output": 2000,   # Model suggests different approach
        "total": 6000,
        "description": "One failed iteration cycle",
    },
}

# WITH Memee: agent gets answer from memory via MCP
LOOKUP_TOKENS = {
    "tool_call": {
        "input": 150,     # MCP tool call overhead
        "output": 50,     # Tool call response wrapper
    },
    "memory_result": {
        "input": 0,       # Result injected into context
        "output": 300,    # Average memory content
    },
    "total": 500,         # Total per lookup
}

# Model pricing (per 1M tokens, April 2026)
MODEL_PRICING = {
    "claude-opus-4": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.0},
    "default": {"input": 3.0, "output": 15.0},
}


@dataclass
class TokenSavings:
    """Token savings calculation result."""
    # Per event
    tokens_without: int     # Tokens spent discovering knowledge
    tokens_with: int        # Tokens spent looking up from Memee
    tokens_saved: int       # Difference

    # Aggregate
    total_lookups: int
    total_tokens_saved: int
    total_cost_saved_usd: float

    # Breakdown
    input_tokens_saved: int
    output_tokens_saved: int
    cost_input_saved: float
    cost_output_saved: float

    # Rates
    reduction_pct: float    # % token reduction
    cost_per_lookup_without: float
    cost_per_lookup_with: float


def estimate_per_event_savings(
    event_type: str = "simple_question",
    model: str = "claude-sonnet-4",
) -> dict:
    """Estimate token savings for a single knowledge retrieval event."""
    discovery = DISCOVERY_TOKENS.get(event_type, DISCOVERY_TOKENS["simple_question"])
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])

    without_input = discovery["input"]
    without_output = discovery["output"]
    without_total = discovery["total"]

    with_total = LOOKUP_TOKENS["total"]
    with_input = LOOKUP_TOKENS["tool_call"]["input"]
    with_output = LOOKUP_TOKENS["tool_call"]["output"] + LOOKUP_TOKENS["memory_result"]["output"]

    saved_input = without_input - with_input
    saved_output = without_output - with_output

    cost_without = (without_input * pricing["input"] + without_output * pricing["output"]) / 1_000_000
    cost_with = (with_input * pricing["input"] + with_output * pricing["output"]) / 1_000_000
    cost_saved = cost_without - cost_with

    return {
        "event_type": event_type,
        "model": model,
        "without": {"input": without_input, "output": without_output, "total": without_total},
        "with_memee": {"input": with_input, "output": with_output, "total": with_total},
        "saved": {"input": saved_input, "output": saved_output, "total": without_total - with_total},
        "reduction_pct": round((1 - with_total / without_total) * 100, 1),
        "cost_without": round(cost_without, 6),
        "cost_with": round(cost_with, 6),
        "cost_saved": round(cost_saved, 6),
    }


def estimate_org_savings(
    agents: int = 10,
    lookups_per_agent_per_day: int = 8,
    working_days_per_year: int = 250,
    avg_iterations_saved_per_incident: int = 3,
    incidents_per_year: int = 50,
    model: str = "claude-sonnet-4",
) -> TokenSavings:
    """Estimate annual token savings for an organization."""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])

    # Regular lookups (search, suggest, check)
    total_lookups = agents * lookups_per_agent_per_day * working_days_per_year

    # Tokens without Memee: each lookup = full discovery conversation
    avg_discovery = DISCOVERY_TOKENS["simple_question"]["total"]
    total_without = total_lookups * avg_discovery

    # Tokens with Memee: each lookup = MCP tool call
    total_with = total_lookups * LOOKUP_TOKENS["total"]

    # Bonus: avoided debugging sessions (incidents prevented)
    debug_tokens_saved = (
        incidents_per_year
        * avg_iterations_saved_per_incident
        * DISCOVERY_TOKENS["debugging"]["total"]
    )

    # Bonus: avoided research (decisions already recorded)
    research_tokens_saved = (
        incidents_per_year * DISCOVERY_TOKENS["research"]["total"]
    )

    total_saved = (total_without - total_with) + debug_tokens_saved + research_tokens_saved

    # Split input/output (rough 60/40 ratio)
    input_saved = int(total_saved * 0.6)
    output_saved = int(total_saved * 0.4)

    cost_input = input_saved * pricing["input"] / 1_000_000
    cost_output = output_saved * pricing["output"] / 1_000_000

    cost_per_without = avg_discovery * (pricing["input"] * 0.6 + pricing["output"] * 0.4) / 1_000_000
    cost_per_with = LOOKUP_TOKENS["total"] * (pricing["input"] * 0.6 + pricing["output"] * 0.4) / 1_000_000

    return TokenSavings(
        tokens_without=avg_discovery,
        tokens_with=LOOKUP_TOKENS["total"],
        tokens_saved=avg_discovery - LOOKUP_TOKENS["total"],
        total_lookups=total_lookups,
        total_tokens_saved=total_saved,
        total_cost_saved_usd=round(cost_input + cost_output, 2),
        input_tokens_saved=input_saved,
        output_tokens_saved=output_saved,
        cost_input_saved=round(cost_input, 2),
        cost_output_saved=round(cost_output, 2),
        reduction_pct=round((1 - total_with / total_without) * 100, 1),
        cost_per_lookup_without=round(cost_per_without, 6),
        cost_per_lookup_with=round(cost_per_with, 6),
    )


def format_savings_report(savings: TokenSavings, agents: int = 10, model: str = "claude-sonnet-4") -> str:
    """Format token savings as readable report."""
    lines = []
    lines.append("")
    lines.append("═" * 70)
    lines.append("  TOKEN SAVINGS REPORT")
    lines.append("═" * 70)
    lines.append(f"  Config: {agents} agents, {model}")
    lines.append("")
    lines.append("  PER KNOWLEDGE RETRIEVAL:")
    lines.append(f"    Without Memee:  {savings.tokens_without:,} tokens (full conversation)")
    lines.append(f"    With Memee:     {savings.tokens_with:,} tokens (MCP lookup)")
    lines.append(f"    Saved:          {savings.tokens_saved:,} tokens ({savings.reduction_pct:.0f}% reduction)")
    lines.append(f"    Cost: ${savings.cost_per_lookup_without:.4f} → ${savings.cost_per_lookup_with:.4f}")
    lines.append("")
    lines.append("  ANNUAL (ORGANIZATION):")
    lines.append(f"    Total lookups:    {savings.total_lookups:,}/year")
    lines.append(f"    Tokens saved:     {savings.total_tokens_saved:,}")
    lines.append(f"    = {savings.total_tokens_saved/1_000_000:.1f}M tokens")
    lines.append(f"    Cost saved:       ${savings.total_cost_saved_usd:,.2f}/year")
    lines.append("")
    lines.append("  BREAKDOWN:")
    lines.append(f"    Input tokens:   {savings.input_tokens_saved:,} saved → ${savings.cost_input_saved:,.2f}")
    lines.append(f"    Output tokens:  {savings.output_tokens_saved:,} saved → ${savings.cost_output_saved:,.2f}")
    lines.append("")

    # Scale examples
    for n, label in [(1, "Solo dev"), (5, "Small team"), (10, "Team"),
                     (50, "Department"), (200, "Enterprise")]:
        scaled = estimate_org_savings(agents=n, model=model)
        lines.append(f"    {label:15s} ({n:3d} agents): "
                     f"{scaled.total_tokens_saved/1_000_000:6.1f}M tokens, "
                     f"${scaled.total_cost_saved_usd:8,.2f}/yr saved")

    lines.append("")
    lines.append("═" * 70)
    return "\n".join(lines)
