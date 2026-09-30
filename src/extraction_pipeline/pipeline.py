"""End-to-end runs: extraction, routing, downstream outputs, evaluation, and the report."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from extraction_pipeline.config import Settings
from extraction_pipeline.documents import Document, TokenCounter, approx_tokens
from extraction_pipeline.evaluation import EVALUATED_FIELDS, evaluate, retry_statistics
from extraction_pipeline.jobs import ExtractionJob, JobStatus
from extraction_pipeline.llm import Usage
from extraction_pipeline.report import build_report
from extraction_pipeline.review import route
from extraction_pipeline.runner import PipelineRunner, RunOutcome
from extraction_pipeline.schema import StudyRecord
from extraction_pipeline.store import RunStore, settings_snapshot


@dataclass
class RunArtifacts:
    run: dict[str, Any]
    results: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    evaluation: dict[str, Any] | None
    retry_stats: dict[str, dict[str, int]]
    report: str


def total_usage(results: Sequence[dict[str, Any]]) -> dict[str, float]:
    usage = Usage()
    for result in results:
        usage.add(Usage(**result["usage"]))
    return {**usage.__dict__, "cost_usd": round(usage.cost_usd, 4)}


def summarise(
    run_id: str,
    settings: Settings,
    outcome: RunOutcome,
    truth: dict[str, dict[str, Any]],
    started_at: str,
) -> RunArtifacts:
    results = outcome.results()
    decisions = [route(result, settings).as_dict() for result in results]
    evaluation = (
        evaluate(results, truth, {d["doc_id"]: d for d in decisions}, settings.review_threshold)
        if truth
        else None
    )
    retry_stats = retry_statistics(results)
    run = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "settings": settings_snapshot(settings),
        "documents": len(results),
        "sla": outcome.sla,
        "usage": total_usage(results),
    }
    report = build_report(run, results, decisions, retry_stats, evaluation)
    return RunArtifacts(run, results, decisions, evaluation, retry_stats, report)


def downstream_records(artifacts: RunArtifacts) -> list[dict[str, Any]]:
    """Auto-accepted, fully validated records: the only rows downstream systems ingest."""
    routes = {d["doc_id"]: d["route"] for d in artifacts.decisions}
    rows = []
    for result in artifacts.results:
        if result["status"] != JobStatus.ACCEPTED or routes[result["doc_id"]] != "auto_accept":
            continue
        record = StudyRecord.model_validate(result["extraction"])
        rows.append(
            {
                "doc_id": result["doc_id"],
                "doc_type": result["doc_type"],
                "record": record.model_dump(mode="json"),
                "extracted_with": artifacts.run["settings"]["model"],
                "run_id": artifacts.run["run_id"],
            }
        )
    return rows


def review_queue(artifacts: RunArtifacts) -> list[dict[str, Any]]:
    extractions = {r["doc_id"]: r for r in artifacts.results}
    return [
        decision | {"extraction": extractions[decision["doc_id"]]["extraction"]}
        for decision in artifacts.decisions
        if decision["route"] == "human_review"
    ]


def persist(store: RunStore, artifacts: RunArtifacts) -> None:
    store.write_json("run.json", artifacts.run)
    store.write_jsonl("results.jsonl", artifacts.results)
    store.write_jsonl("records.jsonl", downstream_records(artifacts))
    store.write_jsonl("review_queue.jsonl", review_queue(artifacts))
    if artifacts.evaluation:
        store.write_json(
            "evaluation.json", artifacts.evaluation | {"retries": artifacts.retry_stats}
        )
    store.write_text("report.md", artifacts.report)


def run_extraction(
    documents: Sequence[Document],
    settings: Settings,
    runner: PipelineRunner,
    truth: dict[str, dict[str, Any]],
    run_id: str,
    *,
    force_sync: bool = False,
    count_tokens: TokenCounter = approx_tokens,
) -> RunArtifacts:
    started = datetime.now(UTC).isoformat(timespec="seconds")
    jobs = [ExtractionJob(doc, settings, count_tokens=count_tokens) for doc in documents]
    outcome = runner.run(jobs, force_sync=force_sync)
    return summarise(run_id, settings, outcome, truth, started)


# --- Few-shot A/B ------------------------------------------------------------------------------

VARIANTS = {"fs": True, "zs": False}  # few-shot, zero-shot


def run_few_shot_ab(
    documents: Sequence[Document],
    settings: Settings,
    runner: PipelineRunner,
    truth: dict[str, dict[str, Any]],
    run_id: str,
    *,
    force_sync: bool = False,
    count_tokens: TokenCounter = approx_tokens,
) -> dict[str, RunArtifacts]:
    """Run both variants on the same documents in the same batch, then score each."""
    started = datetime.now(UTC).isoformat(timespec="seconds")
    jobs = [
        ExtractionJob(
            doc,
            settings.with_(few_shot=few_shot),
            job_id=f"{prefix}-{doc.doc_id}",
            count_tokens=count_tokens,
        )
        for prefix, few_shot in VARIANTS.items()
        for doc in documents
    ]
    outcome = runner.run(jobs, force_sync=force_sync)
    artifacts = {}
    for prefix, few_shot in VARIANTS.items():
        subset = RunOutcome(
            jobs=[j for j in outcome.jobs if j.job_id.startswith(f"{prefix}-")],
            rounds=outcome.rounds,
            sla=outcome.sla,
        )
        artifacts[prefix] = summarise(
            f"{run_id}-{prefix}", settings.with_(few_shot=few_shot), subset, truth, started
        )
    return artifacts


def ab_comparison(artifacts: dict[str, RunArtifacts]) -> str:
    few, zero = artifacts["fs"].evaluation, artifacts["zs"].evaluation
    if not few or not zero:
        return "No ground truth: accuracy comparison unavailable.\n"

    def pct(v: float | None) -> str:
        return "-" if v is None else f"{v * 100:.0f}%"

    def delta(a: float | None, b: float | None) -> str:
        return "-" if a is None or b is None else f"{(a - b) * 100:+.0f}"

    lines = [
        "# Few-shot vs zero-shot",
        "",
        f"Overall field accuracy: few-shot **{pct(few['overall_accuracy'])}**, zero-shot "
        f"**{pct(zero['overall_accuracy'])}** "
        f"({delta(few['overall_accuracy'], zero['overall_accuracy'])} points).",
        "",
        "## By document type (all fields)",
        "",
        "| Document type | Few-shot | Zero-shot | Δ points |",
        "|---|---|---|---|",
    ]
    for doc_type in few["doc_type_field_accuracy"]:
        a = _mean(few["doc_type_field_accuracy"][doc_type])
        b = _mean(zero["doc_type_field_accuracy"].get(doc_type, {}))
        lines.append(f"| {doc_type} | {pct(a)} | {pct(b)} | {delta(a, b)} |")
    lines += [
        "",
        "## By field",
        "",
        "| Field | Few-shot | Zero-shot | Δ points |",
        "|---|---|---|---|",
    ]
    for field in EVALUATED_FIELDS:
        a, b = few["field_accuracy"].get(field), zero["field_accuracy"].get(field)
        lines.append(f"| `{field}` | {pct(a)} | {pct(b)} | {delta(a, b)} |")
    fs_cost = artifacts["fs"].run["usage"]["cost_usd"]
    zs_cost = artifacts["zs"].run["usage"]["cost_usd"]
    lines += ["", f"Cost: few-shot ${fs_cost:.2f}, zero-shot ${zs_cost:.2f}."]
    return "\n".join(lines) + "\n"


def _mean(values: dict[str, float | None]) -> float | None:
    present = [v for v in values.values() if v is not None]
    return sum(present) / len(present) if present else None
