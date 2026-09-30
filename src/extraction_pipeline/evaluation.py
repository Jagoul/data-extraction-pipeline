"""Measure extraction quality against ground truth, and the pipeline's own behaviour.

- **Accuracy** per field, and per document type x field, with a consistency check that flags
  any cell well below that field's overall accuracy.
- **Null handling**: fabrications (a value where the truth is null) and misses (null where the
  truth has a value), per nullable field.
- **Calibration**: are high-confidence fields actually more accurate than low-confidence ones?
- **Routing**: accuracy of what was auto-accepted vs what was sent to a human.
- **Retries**: for each issue kind, how often a retry resolved it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from difflib import SequenceMatcher
from typing import Any

from extraction_pipeline.validation import PLACEHOLDERS

EVALUATED_FIELDS = (
    "title",
    "authors",
    "publication_year",
    "doi",
    "study_type",
    "sample_size",
    "funding_source",
    "primary_outcome",
    "cited_works",
)
NULLABLE_FIELDS = ("publication_year", "doi", "sample_size", "funding_source", "primary_outcome")
CONSISTENCY_MARGIN = 0.15
MIN_CELL_SIZE = 3


def _norm(value: Any) -> str:
    text = str(value).lower()
    text = re.sub(r"^(evidence summary|new research|new review|subject)\s*:\s*", "", text)
    text = re.sub(r"^the\s+", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _similar(a: Any, b: Any, threshold: float) -> bool:
    x, y = _norm(a), _norm(b)
    return bool(x and y) and (x in y or y in x or SequenceMatcher(None, x, y).ratio() >= threshold)


def _both_null_or(pred: Any, truth: Any, compare: Callable[[Any, Any], bool]) -> bool:
    if truth is None or pred is None:
        return truth is None and pred is None
    return compare(pred, truth)


def cited_works_f1(pred: list[dict[str, Any]], truth: list[dict[str, Any]]) -> float:
    def key(work: dict[str, Any]) -> tuple[str, Any]:
        return (_norm(work.get("first_author", "")).split(" ")[-1], work.get("year"))

    p, t = Counter(map(key, pred or [])), Counter(map(key, truth or []))
    if not p and not t:
        return 1.0
    overlap = sum((p & t).values())
    if not overlap:
        return 0.0
    precision, recall = overlap / sum(p.values()), overlap / sum(t.values())
    return 2 * precision * recall / (precision + recall)


def _authors_match(pred: Any, truth: list[str]) -> bool:
    pred = pred or []
    if not truth:  # anonymous document: the right answer is an explicit placeholder
        return all(str(a).strip().lower() in PLACEHOLDERS for a in pred)
    return sorted(map(_norm, pred)) == sorted(map(_norm, truth))


def _study_type_match(extraction: dict[str, Any], truth: dict[str, Any]) -> bool:
    if extraction.get("study_type") != truth["study_type"]:
        return False
    if truth["study_type"] != "other":
        return True
    return _similar(extraction.get("study_type_detail") or "", truth["study_type_detail"], 0.8)


def field_correct(field: str, extraction: dict[str, Any], truth: dict[str, Any]) -> bool:
    pred = extraction.get(field)
    match field:
        case "title":
            return _similar(pred or "", truth["title"], 0.9)
        case "authors":
            return _authors_match(pred, truth["authors"])
        case "publication_year" | "sample_size":
            return bool(pred == truth[field])
        case "doi":
            return _both_null_or(pred, truth["doi"], lambda a, b: str(a).lower() == str(b).lower())
        case "study_type":
            return _study_type_match(extraction, truth)
        case "funding_source" | "primary_outcome":
            return _both_null_or(pred, truth[field], lambda a, b: _similar(a, b, 0.8))
        case "cited_works":
            return cited_works_f1(pred or [], truth["cited_works"]) >= 0.95
    raise ValueError(f"unknown field {field}")


# --- Aggregation -------------------------------------------------------------------------------


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def evaluate(
    results: Iterable[dict[str, Any]],
    truth_by_id: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]] | None = None,
    review_threshold: float = 0.75,
) -> dict[str, Any]:
    decisions = decisions or {}
    by_field: dict[str, list[bool]] = defaultdict(list)
    by_type_field: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    nulls: dict[str, Counter[str]] = {f: Counter() for f in NULLABLE_FIELDS}
    confidence_buckets: dict[str, list[bool]] = {"high": [], "low": []}
    routed: dict[str, list[bool]] = {"auto_accept": [], "human_review": []}
    citation_recall: list[float] = []

    for result in results:
        truth = truth_by_id.get(result["doc_id"])
        if truth is None:
            continue
        extraction = result.get("extraction") or {}
        confidence = extraction.get("field_confidence") or {}
        route = decisions.get(result["doc_id"], {}).get("route")
        for field in EVALUATED_FIELDS:
            ok = bool(extraction) and field_correct(field, extraction, truth)
            by_field[field].append(ok)
            by_type_field[result["doc_type"]][field].append(ok)
            if route:
                routed[route].append(ok)
            score = confidence.get(field)
            if isinstance(score, int | float):
                confidence_buckets["high" if score >= review_threshold else "low"].append(ok)
        for field in NULLABLE_FIELDS:
            pred, real = extraction.get(field), truth[field]
            if real is None:
                nulls[field]["truth_null"] += 1
                nulls[field]["fabricated" if pred is not None else "correct_null"] += 1
            else:
                nulls[field]["truth_present"] += 1
                nulls[field]["missed" if pred is None else "present"] += 1
        if extraction:
            citation_recall.append(
                cited_works_f1(extraction.get("cited_works") or [], truth["cited_works"])
            )

    field_accuracy = {f: _rate(sum(v), len(v)) for f, v in by_field.items()}
    matrix = {
        doc_type: {f: _rate(sum(v), len(v)) for f, v in fields.items()}
        for doc_type, fields in sorted(by_type_field.items())
    }
    inconsistent = [
        {"doc_type": doc_type, "field": f, "accuracy": acc, "field_overall": field_accuracy[f]}
        for doc_type, fields in by_type_field.items()
        for f, values in fields.items()
        if len(values) >= MIN_CELL_SIZE
        and (acc := _rate(sum(values), len(values))) is not None
        and (overall := field_accuracy[f]) is not None
        and acc < overall - CONSISTENCY_MARGIN
    ]
    all_checks = [ok for values in by_field.values() for ok in values]
    return {
        "documents": len(by_field.get("title", [])),
        "overall_accuracy": _rate(sum(all_checks), len(all_checks)),
        "field_accuracy": field_accuracy,
        "doc_type_field_accuracy": matrix,
        "inconsistent_cells": inconsistent,
        "null_handling": {
            f: dict(c) | {"fabrication_rate": _rate(c["fabricated"], c["truth_null"])}
            for f, c in nulls.items()
        },
        "calibration": {
            bucket: {"fields": len(v), "accuracy": _rate(sum(v), len(v))}
            for bucket, v in confidence_buckets.items()
        },
        "routing_accuracy": {
            route: {"fields": len(v), "accuracy": _rate(sum(v), len(v))}
            for route, v in routed.items()
        },
        "mean_citation_f1": round(sum(citation_recall) / len(citation_recall), 3)
        if citation_recall
        else None,
    }


def retry_statistics(results: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """For each issue kind: seen, retried, resolved by the retry, or left for a human.

    An issue on attempt N counts as resolved when the same field has no issue of that kind on
    attempt N+1. An issue on the final attempt was either never retried (unresolvable kind, or
    retries exhausted) or it is the one that stayed.
    """
    stats: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        attempts = result.get("attempts", [])
        for index, attempt in enumerate(attempts):
            following = attempts[index + 1] if index + 1 < len(attempts) else None
            after = {(i["field"], i["kind"]) for i in following["issues"]} if following else None
            for issue in attempt["issues"]:
                kind = issue["kind"]
                stats[kind]["seen"] += 1
                if after is None:
                    stats[kind][
                        "not_retried" if kind == "missing_required" else "unresolved_final"
                    ] += 1
                else:
                    stats[kind]["retried"] += 1
                    resolved = (issue["field"], kind) not in after
                    stats[kind]["resolved" if resolved else "persisted"] += 1
    return {kind: dict(counter) for kind, counter in sorted(stats.items())}
