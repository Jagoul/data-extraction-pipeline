"""Runtime configuration, read once from the environment (and `.env`, if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

Effort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_MODEL = "claude-opus-5-5"

# Models that accept the server-side refusal `fallbacks` parameter.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})

# USD per million tokens (standard API rates). Batch requests are billed at 50% of these.
PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
}
BATCH_DISCOUNT = 0.5


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    """Every tunable in one place. Override with EXTRACTOR_* environment variables."""

    model: str = DEFAULT_MODEL
    effort: Effort | None = "medium"  # None omits the parameter (Haiku 4.5 does not accept it)
    max_tokens: int = 8192
    few_shot: bool = True
    strict_tools: bool = True
    max_attempts: int = 3
    retry_unresolvable: bool = False
    max_transport_retries: int = 2
    chunk_token_budget: int = 6000
    review_threshold: float = 0.75
    critical_fields: tuple[str, ...] = ("sample_size", "doi", "study_type")
    critical_threshold: float = 0.85
    sla_hours: float = 4.0
    poll_seconds: float = 20.0
    data_dir: Path = field(default_factory=lambda: Path("data"))
    runs_dir: Path = field(default_factory=lambda: Path("runs"))

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        effort = os.environ.get("EXTRACTOR_EFFORT", "medium")
        if effort == "none":
            effort = None  # type: ignore[assignment]
        elif effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError(
                f"EXTRACTOR_EFFORT must be none|low|medium|high|xhigh|max, got {effort!r}"
            )
        return cls(
            model=os.environ.get("EXTRACTOR_MODEL", DEFAULT_MODEL),
            effort=effort,  # type: ignore[arg-type]
            max_tokens=_int("EXTRACTOR_MAX_TOKENS", 8192),
            max_attempts=_int("EXTRACTOR_MAX_ATTEMPTS", 3),
            chunk_token_budget=_int("EXTRACTOR_CHUNK_TOKENS", 6000),
            review_threshold=_float("EXTRACTOR_REVIEW_THRESHOLD", 0.75),
            critical_threshold=_float("EXTRACTOR_CRITICAL_THRESHOLD", 0.85),
            sla_hours=_float("EXTRACTOR_SLA_HOURS", 4.0),
            poll_seconds=_float("EXTRACTOR_POLL_SECONDS", 20.0),
        )

    def with_(self, **changes: object) -> Settings:
        return replace(self, **changes)  # type: ignore[arg-type]
