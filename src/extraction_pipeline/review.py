"""Human-review routing: decide, per document, whether an extraction can flow downstream.

A document goes to a human when any of these holds:

- the job failed (refusal, repeated API errors, no tool call);
- validation issues remain after the retry budget, including information absent from the source;
- the model's own confidence in any scored field is below the threshold. Critical fields (those
  a meta-analysis depends on) use a stricter threshold.

The reviewer gets the exact fields to check, not just "please review this document".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from extraction_pipeline.config import Settings

Route = Literal["auto_accept", "human_review"]


@dataclass(frozen=True)
class ReviewDecision:
    doc_id: str
    route: Route
    reasons: list[str] = field(default_factory=list)
    fields_to_check: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def threshold_for(field_name: str, settings: Settings) -> float:
    if field_name in settings.critical_fields:
        return settings.critical_threshold
    return settings.review_threshold


def route(result: dict[str, Any], settings: Settings) -> ReviewDecision:
    """Route one job result (as produced by `ExtractionJob.result()`)."""
    doc_id = result["doc_id"]
    reasons: list[str] = []
    fields: set[str] = set()

    if result["status"] == "failed":
        return ReviewDecision(doc_id, "human_review", [f"extraction failed: {result['failure']}"])

    for issue in result.get("issues", []):
        reasons.append(f"unresolved {issue['kind']} issue on {issue['field']}: {issue['message']}")
        fields.add(issue["field"].split(".")[0])

    extraction = result.get("extraction") or {}
    confidence = extraction.get("field_confidence") or {}
    for name, score in sorted(confidence.items()):
        limit = threshold_for(name, settings)
        if isinstance(score, int | float) and score < limit:
            reasons.append(f"low confidence on {name}: {score:.2f} < {limit:.2f}")
            fields.add(name)

    decision: Route = "human_review" if reasons else "auto_accept"
    return ReviewDecision(doc_id, decision, reasons, sorted(fields))
