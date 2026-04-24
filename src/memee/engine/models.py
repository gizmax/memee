"""Model family detection and cross-model utilities.

Identifies which model family a model belongs to, enabling
cross-model validation bonuses. When different model families
agree on a pattern, confidence increases faster — AI peer review.
"""

from __future__ import annotations

import os

# Order matters: more specific patterns first to avoid substring conflicts
MODEL_FAMILIES = {
    "local": ["ollama", "llamacpp", "mlx", "gguf", "localai"],
    "anthropic": ["claude", "opus", "sonnet", "haiku"],
    "openai": ["gpt", "o1-", "o3-", "o4-", "chatgpt"],
    "google": ["gemini", "palm", "bard"],
    "deepseek": ["deepseek"],
    "mistral": ["mistral", "mixtral", "codestral"],
    "meta": ["llama", "codellama"],
    "cohere": ["command-r", "cohere"],
}


def get_model_family(model_name: str | None) -> str:
    """Detect model family from model name.

    Examples:
        "claude-opus-4" → "anthropic"
        "gpt-4o" → "openai"
        "gemini-2.0-flash" → "google"
        "llama-3.1-70b" → "meta"
        None → "unknown"
    """
    if not model_name:
        return "unknown"

    model_lower = model_name.lower()
    for family, keywords in MODEL_FAMILIES.items():
        if any(kw in model_lower for kw in keywords):
            return family
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
