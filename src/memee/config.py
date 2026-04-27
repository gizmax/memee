"""Memee configuration. All settings overridable via MEMEE_ env vars."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_path: Path = Path.home() / ".memee" / "memee.db"
    org_name: str = "default"

    # Confidence scoring
    validation_weight: float = 0.08
    invalidation_weight: float = 0.12
    cross_project_bonus: float = 1.5
    cross_model_bonus: float = 1.3  # Diversity bonus, not "peer review"
    diminishing_factor: float = 0.95

    # Maturity thresholds
    tested_min_applications: int = 1
    validated_min_confidence: float = 0.7
    validated_min_projects: int = 3
    canon_min_confidence: float = 0.85
    canon_min_projects: int = 5
    canon_min_validations: int = 10
    deprecated_max_confidence: float = 0.2
    deprecated_min_applications: int = 3

    # Lifecycle
    hypothesis_ttl_days: int = 90
    aging_interval_hours: int = 24

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 7878

    # CMAM (Claude Managed Agents Memory) adapter — optional, off by default
    cmam_enabled: bool = False
    cmam_backend: str = "fs"                  # "fs" (local dir) or "api" (Anthropic)
    cmam_store_id: str = "memee-canon"
    cmam_local_root: Path | None = None       # defaults to ~/.memee/cmam/<store_id>
    cmam_api_base: str = "https://api.anthropic.com"
    cmam_redact: bool = True

    # ``.memee`` pack signing key. Optional; if set, ``memee pack export``
    # signs the pack with this ed25519 PEM private key. The CLI flag
    # ``--key`` takes precedence over the env var.
    pack_key: Path | None = None

    model_config = {"env_prefix": "MEMEE_"}


def get_settings() -> Settings:
    """Get fresh settings (re-reads env vars)."""
    return Settings()


settings = Settings()

