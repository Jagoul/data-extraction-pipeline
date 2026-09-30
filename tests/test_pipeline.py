"""End to end with fake API clients: outputs, report, A/B, and the CLI's offline commands."""

import json
from dataclasses import asdict

import pytest
from typer.testing import CliRunner

from extraction_pipeline.cli import app
from extraction_pipeline.pipeline import (
    ab_comparison,
    downstream_records,
    persist,
    review_queue,
    run_extraction,
    run_few_shot_ab,
)
from extraction_pipeline.prompts import FEW_SHOT_EXAMPLES
from extraction_pipeline.runner import PipelineRunner, split_custom_id
from extraction_pipeline.schema import StudyRecord
from extraction_pipeline.store import RunStore
from tests.conftest import FakeBatchClient, as_document, oracle_extraction, tool_call


@pytest.fixture
def sample(corpus):
    """Ten ordinary documents plus both anonymous-note flavours of 'information absent'."""
    ordinary = [d for d in corpus if d.truth.doc_type not in ("long_report", "technical_note")][:10]
    anonymous = [d for d in corpus if d.truth.doc_type == "technical_note"][:1]
    return ordinary + anonymous


@pytest.fixture
def artifacts(settings, sample):
    by_id = {d.truth.doc_id: d for d in sample}

    def respond(cid, _params):
        job_id, _ = split_custom_id(cid)
        doc = by_id[job_id.split("-", 1)[1] if job_id.startswith(("fs-", "zs-")) else job_id]
        return tool_call(oracle_extraction(doc))

    runner = PipelineRunner(settings, batch_client=FakeBatchClient(respond), sleep=lambda _: None)
    truth = {d.truth.doc_id: asdict(d.truth) for d in sample}
    return run_extraction([as_document(d) for d in sample], settings, runner, truth, "run-test")


def test_only_auto_accepted_valid_records_flow_downstream(artifacts, sample):
    rows = downstream_records(artifacts)

    assert len(rows) == len(sample) - 1  # the anonymous note is held back
    for row in rows:
        StudyRecord.model_validate(row["record"])  # the downstream contract holds


def test_the_review_queue_says_what_to_check(artifacts):
    queue = review_queue(artifacts)

    assert len(queue) == 1
    assert "authors" in queue[0]["fields_to_check"]
    assert queue[0]["extraction"]["authors"] == ["unknown"]


def test_evaluation_and_report_are_produced(artifacts):
    assert artifacts.evaluation["overall_accuracy"] == 1.0
    for heading in (
        "## Outcome",
        "## Processing time and SLA",
        "## Accuracy against ground truth",
        "### Null handling",
        "### Confidence calibration and routing",
    ):
        assert heading in artifacts.report


def test_persist_writes_every_output_file(artifacts, tmp_path):
    store = RunStore(tmp_path, "run-test")

    persist(store, artifacts)

    names = {p.name for p in store.path.iterdir()}
    assert names == {
        "run.json",
        "results.jsonl",
        "records.jsonl",
        "review_queue.jsonl",
        "evaluation.json",
        "report.md",
    }
    assert json.loads((store.path / "run.json").read_text())["run_id"] == "run-test"


def test_ab_run_puts_both_variants_in_one_batch(settings, sample):
    by_id = {d.truth.doc_id: d for d in sample}
    seen_prompts = {}

    def respond(cid, params):
        job_id, _ = split_custom_id(cid)
        variant, doc_id = job_id.split("-", 1)
        seen_prompts[variant] = params["system"][0]["text"]
        return tool_call(oracle_extraction(by_id[doc_id]))

    batch = FakeBatchClient(respond)
    runner = PipelineRunner(settings, batch_client=batch, sleep=lambda _: None)
    truth = {d.truth.doc_id: asdict(d.truth) for d in sample}

    result = run_few_shot_ab([as_document(d) for d in sample], settings, runner, truth, "ab")

    assert len(batch.batches) == 1
    assert len(batch.batches[0]) == 2 * len(sample)
    assert "<examples>" in seen_prompts["fs"]
    assert "<examples>" not in seen_prompts["zs"]
    assert "Few-shot vs zero-shot" in ab_comparison(result)


# --- CLI (offline commands) --------------------------------------------------------------------

cli = CliRunner()


def test_cli_schema_prints_the_published_schema():
    result = cli.invoke(app, ["schema"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["title"] == "StudyRecord"


def test_cli_validate_accepts_and_rejects(tmp_path):
    example = next(e for e in FEW_SHOT_EXAMPLES if e.name == "table_numbered_bibliography")
    document = tmp_path / "doc.md"
    document.write_text(example.document)
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(example.extraction))
    bad.write_text(json.dumps(example.extraction | {"doi": "https://doi.org/x"}))

    assert cli.invoke(app, ["validate", str(good), str(document)]).exit_code == 0
    rejected = cli.invoke(app, ["validate", str(bad), str(document)])
    assert rejected.exit_code == 1
    assert "retryable" in rejected.stdout


def test_cli_corpus_writes_documents_and_truth(tmp_path):
    result = cli.invoke(app, ["corpus", "--out", str(tmp_path)])

    assert result.exit_code == 0
    assert len(list((tmp_path / "documents").glob("*.md"))) == 100
    assert (tmp_path / "ground_truth.jsonl").exists()
