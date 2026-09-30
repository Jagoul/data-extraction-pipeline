"""The `extractor` command line."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from extraction_pipeline.config import Settings
from extraction_pipeline.corpus import write_corpus
from extraction_pipeline.documents import Document, load_corpus, load_ground_truth
from extraction_pipeline.pipeline import (
    RunArtifacts,
    ab_comparison,
    persist,
    run_extraction,
    run_few_shot_ab,
)
from extraction_pipeline.review import route
from extraction_pipeline.runner import PipelineRunner
from extraction_pipeline.schema import FULL_SCHEMA
from extraction_pipeline.store import RunStore, new_run_id
from extraction_pipeline.validation import validate_extraction

app = typer.Typer(
    help="Schema-first document extraction with Claude.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _settings(few_shot: bool = True, sla_hours: float | None = None) -> Settings:
    settings = Settings.from_env().with_(few_shot=few_shot)
    return settings.with_(sla_hours=sla_hours) if sla_hours is not None else settings


def _runner(settings: Settings, *, batch: bool) -> tuple[PipelineRunner, Any]:
    import anthropic

    from extraction_pipeline.llm import AnthropicBatchClient, SyncClient

    client = anthropic.Anthropic()
    runner = PipelineRunner(
        settings,
        sync_client=SyncClient(settings, client),
        batch_client=AnthropicBatchClient(settings, client) if batch else None,
        progress=lambda message: console.print(f"[dim]{message}[/dim]"),
    )
    return runner, client


def _counter(client: Any, settings: Settings, documents: list[Document]) -> Any:
    from extraction_pipeline.llm import calibrated_token_counter

    longest = max(documents, key=lambda d: len(d.text)).text
    return calibrated_token_counter(client, settings.model, longest[:20_000])


def _summary_table(artifacts: RunArtifacts) -> Table:
    table = Table(title=f"Run {artifacts.run['run_id']}", show_header=True)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    statuses = [r["status"] for r in artifacts.results]
    routes = [d["route"] for d in artifacts.decisions]
    table.add_row("Documents", str(len(statuses)))
    for status in ("accepted", "needs_review", "failed"):
        table.add_row(f"  {status}", str(statuses.count(status)))
    table.add_row("Auto-accepted", str(routes.count("auto_accept")))
    table.add_row("Human review", str(routes.count("human_review")))
    if artifacts.evaluation:
        table.add_row("Field accuracy", f"{artifacts.evaluation['overall_accuracy']:.1%}")
    sla = artifacts.run["sla"]
    if sla.get("rounds"):
        table.add_row("Processing time", f"{sla['total_seconds'] / 60:.1f} min")
        table.add_row("SLA", "met" if sla["met"] else "MISSED")
    table.add_row("Cost", f"${artifacts.run['usage']['cost_usd']:.2f}")
    return table


@app.command()
def corpus(
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("data"),
    seed: Annotated[int, typer.Option(help="Random seed (same seed, same corpus)")] = 7,
) -> None:
    """Generate the synthetic document corpus and its ground truth."""
    documents = write_corpus(out, seed)
    console.print(f"Wrote {len(documents)} documents and ground truth to {out}/")


@app.command()
def extract(
    path: Annotated[Path, typer.Argument(help="A text or Markdown document")],
    few_shot: Annotated[bool, typer.Option(help="Include few-shot examples")] = True,
    trace: Annotated[bool, typer.Option(help="Print every request and outcome")] = True,
) -> None:
    """Extract one document synchronously, with the validation-retry loop."""
    from extraction_pipeline.jobs import ExtractionJob

    settings = _settings(few_shot)
    truth = load_ground_truth(settings.data_dir).get(path.stem, {})
    document = Document.from_path(path, truth.get("doc_type", "unknown"))
    runner, _ = _runner(settings, batch=False)
    outcome = runner.run([ExtractionJob(document, settings)], force_sync=True)
    result = outcome.results()[0]
    if trace:
        for event in result["events"]:
            console.print(f"  [cyan]·[/cyan] {escape(event)}")
    console.print_json(json.dumps(result["extraction"], ensure_ascii=False))
    decision = route(result, settings)
    colour = "green" if decision.route == "auto_accept" else "yellow"
    console.print(f"[bold {colour}]{result['status']} → {decision.route}[/bold {colour}]")
    for reason in decision.reasons:
        console.print(f"  - {escape(reason)}")


@app.command()
def run(
    limit: Annotated[int | None, typer.Option(help="Only the first N documents")] = None,
    docs: Annotated[str | None, typer.Option(help="Only these ids, comma-separated")] = None,
    sync: Annotated[bool, typer.Option(help="Use the synchronous API instead of batches")] = False,
    few_shot: Annotated[bool, typer.Option(help="Include few-shot examples")] = True,
    sla_hours: Annotated[float | None, typer.Option(help="Processing-time SLA")] = None,
    run_id: Annotated[str | None, typer.Option(help="Name of the run folder")] = None,
) -> None:
    """Extract the corpus with the Message Batches API, then route, evaluate, and report."""
    settings = _settings(few_shot, sla_hours)
    documents = load_corpus(settings.data_dir, limit)
    if docs:
        wanted = {d.strip() for d in docs.split(",")}
        documents = [d for d in documents if d.doc_id in wanted]
    if not documents:
        console.print("[red]No documents found. Run `extractor corpus` first.[/red]")
        raise typer.Exit(1)
    runner, client = _runner(settings, batch=not sync)
    run_id = run_id or new_run_id()
    artifacts = run_extraction(
        documents,
        settings,
        runner,
        load_ground_truth(settings.data_dir),
        run_id,
        force_sync=sync,
        count_tokens=_counter(client, settings, documents),
    )
    store = RunStore(settings.runs_dir, run_id)
    persist(store, artifacts)
    console.print(_summary_table(artifacts))
    console.print(f"Report: {store.path / 'report.md'}")


@app.command("ab-test")
def ab_test(
    limit: Annotated[int | None, typer.Option(help="Only the first N documents")] = None,
    sync: Annotated[bool, typer.Option(help="Use the synchronous API instead of batches")] = False,
) -> None:
    """Compare few-shot and zero-shot extraction on the same documents, in one batch."""
    settings = _settings()
    documents = load_corpus(settings.data_dir, limit)
    runner, client = _runner(settings, batch=not sync)
    run_id = new_run_id("ab")
    artifacts = run_few_shot_ab(
        documents,
        settings,
        runner,
        load_ground_truth(settings.data_dir),
        run_id,
        force_sync=sync,
        count_tokens=_counter(client, settings, documents),
    )
    comparison = ab_comparison(artifacts)
    for prefix, variant in artifacts.items():
        persist(RunStore(settings.runs_dir / run_id, prefix), variant)
    target = RunStore(settings.runs_dir, run_id).write_text("comparison.md", comparison)
    console.print(comparison)
    console.print(f"Comparison: {target}")


@app.command()
def schema(
    out: Annotated[Path | None, typer.Option(help="Write to a file instead of stdout")] = None,
) -> None:
    """Print the published JSON Schema for downstream consumers."""
    text = json.dumps(FULL_SCHEMA, indent=2) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        console.print(f"Wrote {out}")
    else:
        sys.stdout.write(text)


@app.command()
def validate(
    extraction: Annotated[Path, typer.Argument(help="JSON file with an extraction")],
    document: Annotated[Path, typer.Argument(help="The source document")],
) -> None:
    """Validate an extraction offline, exactly as the pipeline does (no API calls)."""
    outcome = validate_extraction(json.loads(extraction.read_text()), document.read_text())
    if outcome.valid:
        console.print("[green]valid[/green]")
        return
    for issue in outcome.issues:
        retry = "retryable" if issue.kind.retryable else "needs a human"
        detail = escape(f"[{issue.kind}, {retry}] {issue.message}")
        console.print(f"[yellow]{escape(issue.field)}[/yellow] {detail}")
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
