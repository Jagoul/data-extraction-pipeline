"""Render a run's numbers as a Markdown report."""

from __future__ import annotations

from collections import Counter
from typing import Any

from extraction_pipeline.evaluation import EVALUATED_FIELDS


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    percent = round(value * 100, 1)
    return f"{percent:.0f}%" if percent in (0.0, 100.0) else f"{percent:.1f}%"


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _duration(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.2f} h"
    if seconds >= 60:
        return f"{seconds / 60:.1f} min"
    return f"{seconds:.0f} s"


def build_report(
    run: dict[str, Any],
    results: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    retry_stats: dict[str, dict[str, int]],
    evaluation: dict[str, Any] | None,
) -> str:
    statuses = Counter(r["status"] for r in results)
    routes = Counter(d["route"] for d in decisions)
    usage = run.get("usage", {})
    cache_total = usage.get("cache_read_tokens", 0) + usage.get("cache_write_tokens", 0)
    parts = [
        f"# Extraction run `{run['run_id']}`",
        "",
        f"Model `{run['settings']['model']}` · effort `{run['settings']['effort']}` · "
        f"few-shot {'on' if run['settings']['few_shot'] else 'off'} · "
        f"{len(results)} documents",
        "",
        "## Outcome",
        "",
        _table(
            ["Metric", "Value"],
            [
                ["Accepted (valid on first or later attempt)", statuses.get("accepted", 0)],
                ["Needs review (issues left after retries)", statuses.get("needs_review", 0)],
                ["Failed (no usable extraction)", statuses.get("failed", 0)],
                ["Auto-accepted after routing", routes.get("auto_accept", 0)],
                ["Routed to human review", routes.get("human_review", 0)],
                ["Chunked (oversized) documents", sum(r["mode"] == "chunked" for r in results)],
                ["Cost (USD, batch discount applied)", f"${usage.get('cost_usd', 0):.2f}"],
                [
                    "Prompt-cache reads / (reads + writes)",
                    _pct(usage.get("cache_read_tokens", 0) / cache_total) if cache_total else "-",
                ],
            ],
        ),
    ]

    sla = run.get("sla", {})
    if sla.get("rounds"):
        parts += [
            "",
            "## Processing time and SLA",
            "",
            _table(
                ["Round", "Mode", "Requests", "Duration", "Why this mode"],
                [
                    [r["number"], r["mode"], r["requests"], _duration(r["seconds"]), r["reason"]]
                    for r in sla["rounds"]
                ],
            ),
            "",
            f"Total processing time **{_duration(sla['total_seconds'])}** against an SLA of "
            f"**{_duration(sla['sla_seconds'])}** ({sla['sla_used_pct']}% used) · "
            f"SLA {'met' if sla['met'] else '**missed**'}. Worst case if every batch round took "
            f"the 24 h maximum: {_duration(sla['worst_case_batch_only_seconds'])}.",
        ]

    if retry_stats:
        parts += [
            "",
            "## Validation issues and retries",
            "",
            _table(
                ["Issue kind", "Seen", "Retried", "Resolved by retry", "Persisted", "Not retried"],
                [
                    [
                        kind,
                        s.get("seen", 0),
                        s.get("retried", 0),
                        s.get("resolved", 0),
                        s.get("persisted", 0) + s.get("unresolved_final", 0),
                        s.get("not_retried", 0),
                    ]
                    for kind, s in retry_stats.items()
                ],
            ),
        ]

    if evaluation:
        doc_types = list(evaluation["doc_type_field_accuracy"])
        parts += [
            "",
            "## Accuracy against ground truth",
            "",
            f"Overall field accuracy **{_pct(evaluation['overall_accuracy'])}** across "
            f"{evaluation['documents']} documents · mean cited-works F1 "
            f"{evaluation['mean_citation_f1']}.",
            "",
            _table(
                ["Field", *doc_types, "All"],
                [
                    [
                        f"`{field}`",
                        *[
                            _pct(evaluation["doc_type_field_accuracy"][t].get(field))
                            for t in doc_types
                        ],
                        f"**{_pct(evaluation['field_accuracy'].get(field))}**",
                    ]
                    for field in EVALUATED_FIELDS
                ],
            ),
            "",
            "### Null handling",
            "",
            _table(
                ["Field", "Truly absent", "Returned null", "Fabricated", "Present", "Missed"],
                [
                    [
                        f"`{field}`",
                        c.get("truth_null", 0),
                        c.get("correct_null", 0),
                        c.get("fabricated", 0),
                        c.get("truth_present", 0),
                        c.get("missed", 0),
                    ]
                    for field, c in evaluation["null_handling"].items()
                ],
            ),
            "",
            "### Confidence calibration and routing",
            "",
            _table(
                ["Group", "Fields", "Accuracy"],
                [
                    ["Confidence ≥ threshold", *_cal(evaluation["calibration"]["high"])],
                    ["Confidence < threshold", *_cal(evaluation["calibration"]["low"])],
                    [
                        "Auto-accepted documents",
                        *_cal(evaluation["routing_accuracy"]["auto_accept"]),
                    ],
                    ["Routed to review", *_cal(evaluation["routing_accuracy"]["human_review"])],
                ],
            ),
        ]
        if evaluation["inconsistent_cells"]:
            parts += ["", "**Inconsistent cells** (15+ points below the field's overall accuracy):"]
            parts += [
                f"- `{c['field']}` on {c['doc_type']}: {_pct(c['accuracy'])} vs "
                f"{_pct(c['field_overall'])} overall"
                for c in evaluation["inconsistent_cells"]
            ]
        else:
            parts += ["", "No document type falls 15+ points below a field's overall accuracy."]

    return "\n".join(parts) + "\n"


def _cal(bucket: dict[str, Any]) -> list[Any]:
    return [bucket["fields"], _pct(bucket["accuracy"])]
