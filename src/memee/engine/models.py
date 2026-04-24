"""Model family detection and cross-model utilities.

Identifies which model family a model belongs to, enabling
cross-model validation bonuses. When different model families
agree on a pattern, confidence increases faster — AI peer review.
"""

from __future__ import annotations

import os
import re

# Kept for back-compat reference. Detection below is token-based, not
# substring-based, so a spurious match like ``sonnet-transformers`` on
# "sonnet" no longer happens.
MODEL_FAMILIES = {
    "local": ["ollama", "llamacpp", "mlx", "gguf", "localai"],
    "anthropic": ["claude", "opus", "sonnet", "haiku"],
    "openai": ["gpt", "chatgpt"],  # o-series handled by regex below
    "google": ["gemini", "palm", "bard"],
    "deepseek": ["deepseek"],
    "mistral": ["mistral", "mixtral", "codestral"],
    "meta": ["llama", "codellama"],
    "cohere": ["command-r", "cohere"],
}

_O_SERIES_RE = re.compile(r"^o\d+")


def get_model_family(model_name: str | None) -> str:
    """Detect model family from model name.

    Uses structured token matching (split on ``/``, ``-``, ``_``) rather
    than substring matching. This prevents false positives like
    ``sonnet-transformers`` classifying as Anthropic, or future model
    names that happen to contain a substring of an unrelated family.

    Examples:
        "claude-opus-4" → "anthropic"
        "gpt-4o" → "openai"
        "gpt-5" → "openai"
        "o5-mini" → "openai"
        "gemini-2.0-flash" → "google"
        "llama-4-405b" → "meta"
        "sonnet-transformers" → "unknown"  (not a Claude variant)
        None → "unknown"
    """
    if not model_name:
        return "unknown"

    tokens = [t for t in re.split(r"[/\-_]", model_name.lower().strip()) if t]
    if not tokens:
        return "unknown"
    token_set = set(tokens)

    # Local inference runtimes — check first so a Llama running through
    # Ollama still reads as "local" (runtime dominates family).
    if token_set & {"ollama", "llamacpp", "mlx", "gguf", "localai"}:
        return "local"

    # Anthropic — "claude" is the unambiguous signal. "opus"/"sonnet"/
    # "haiku" alone are too generic (e.g. "sonnet-transformers" is an
    # unrelated HF-style name) — they only imply Anthropic when paired
    # with "claude".
    if "claude" in token_set:
        return "anthropic"
    if token_set & {"opus", "sonnet", "haiku"} and "claude" in token_set:
        return "anthropic"

    # OpenAI — "gpt" / "chatgpt" plus the o-series ("o1-preview", "o5-mini").
    if token_set & {"gpt", "chatgpt"}:
        return "openai"
    if any(_O_SERIES_RE.match(t) for t in tokens):
        return "openai"

    if token_set & {"gemini", "palm", "bard"}:
        return "google"
    if "deepseek" in token_set:
        return "deepseek"
    if token_set & {"mistral", "mixtral", "codestral"}:
        return "mistral"
    if token_set & {"llama", "codellama"}:
        return "meta"
    if "qwen" in token_set:
        return "alibaba"
    if "grok" in token_set:
        return "xai"
    # Cohere command-r uses a hyphen; token-split already gives us {"command", "r"}.
    if ("command" in token_set and "r" in token_set) or "cohere" in token_set:
        return "cohere"
    return "unknown"


def detect_current_model() -> str | None:
    """Auto-detect the current model from environment variables.

    Checks common env vars set by AI frameworks.
    """
    # Check in priority order
    for env_var in [
        "MEMEE_MODEL",           # Explicit Memee config
        "ANTHROPIC_MODEL",       # Claude
        "OPENAI_MODEL",          # OpenAI
        "MODEL_NAME",            # Generic
        "LLM_MODEL",             # LangChain convention
    ]:
        value = os.getenv(env_var)
        if value:
            return value
    return None


def is_different_family(model_a: str | None, model_b: str | None) -> bool:
    """Check if two models belong to different families.

    Used to determine cross-model bonus eligibility.
    """
    if not model_a or not model_b:
        return False
    family_a = get_model_family(model_a)
    family_b = get_model_family(model_b)
    if family_a == "unknown" or family_b == "unknown":
        return False
    return family_a != family_b


def get_unique_model_families(model_names: list[str]) -> set[str]:
    """Get unique model families from a list of model names."""
    families = set()
    for name in model_names:
        family = get_model_family(name)
        if family != "unknown":
            families.add(family)
    return families
