"""SLA accounting: how long processing took, and whether the next round can still use batches.

The Message Batches API costs half as much, but a batch may take up to 24 hours (most finish
within an hour). Each round of resubmissions adds another batch wait, so the planner estimates
how long the next batch will take from the rounds already observed and falls back to the
synchronous API when that estimate no longer fits inside the remaining SLA.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

BATCH_MAX_SECONDS = 24 * 3600
DEFAULT_BATCH_ESTIMATE_SECONDS = 3600  # "most batches finish within an hour"


@dataclass(frozen=True)
class RoundRecord:
    number: int
    mode: Literal["batch", "sync"]
    requests: int
    started_at: float
    ended_at: float
    batch_id: str | None = None
    reason: str = ""

    @property
    def seconds(self) -> float:
        return self.ended_at - self.started_at

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"seconds": round(self.seconds, 1)}


@dataclass(frozen=True)
class RoundPlan:
    mode: Literal["batch", "sync"]
    reason: str


@dataclass(frozen=True)
class SlaPolicy:
    sla_seconds: float
    safety_factor: float = 1.5

    def estimate_batch_seconds(self, history: list[RoundRecord]) -> float:
        observed = [r.seconds for r in history if r.mode == "batch"]
        if not observed:
            return DEFAULT_BATCH_ESTIMATE_SECONDS
        return min(BATCH_MAX_SECONDS, max(observed) * self.safety_factor)

    def plan(
        self, elapsed: float, history: list[RoundRecord], *, force_sync: bool = False
    ) -> RoundPlan:
        if force_sync:
            return RoundPlan("sync", "synchronous mode requested")
        remaining = self.sla_seconds - elapsed
        estimate = self.estimate_batch_seconds(history)
        if remaining <= 0:
            return RoundPlan("sync", "SLA already exceeded: finish as fast as possible")
        if estimate <= remaining:
            return RoundPlan(
                "batch",
                f"estimated batch time {_fmt(estimate)} fits in the remaining {_fmt(remaining)}",
            )
        return RoundPlan(
            "sync",
            f"estimated batch time {_fmt(estimate)} exceeds the remaining {_fmt(remaining)}",
        )


def sla_report(history: list[RoundRecord], sla_seconds: float) -> dict[str, Any]:
    """Total processing time against the SLA, plus the worst case the design must survive."""
    if not history:
        return {"rounds": [], "total_seconds": 0.0, "sla_seconds": sla_seconds, "met": True}
    total = max(r.ended_at for r in history) - min(r.started_at for r in history)
    batch_rounds = sum(r.mode == "batch" for r in history)
    return {
        "rounds": [r.as_dict() for r in history],
        "total_seconds": round(total, 1),
        "sla_seconds": sla_seconds,
        "sla_used_pct": round(100 * total / sla_seconds, 1) if sla_seconds else None,
        "met": total <= sla_seconds,
        "batch_rounds": batch_rounds,
        "sync_rounds": len(history) - batch_rounds,
        "worst_case_batch_only_seconds": batch_rounds * BATCH_MAX_SECONDS,
    }


def _fmt(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    if seconds >= 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds:.0f} s"
