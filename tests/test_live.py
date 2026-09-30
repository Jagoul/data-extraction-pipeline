"""Live checks against the real Claude API. Run with `uv run pytest -m live` (costs a few cents)."""

import os

import pytest

from extraction_pipeline.config import Settings
from extraction_pipeline.jobs import ExtractionJob, JobStatus
from extraction_pipeline.review import route
from extraction_pipeline.runner import PipelineRunner
from tests.conftest import as_document, first_of

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY"),
]


@pytest.fixture(scope="module")
def extract():
    from extraction_pipeline.llm import SyncClient

    settings = Settings.from_env()
    runner = PipelineRunner(settings, sync_client=SyncClient(settings))

    def run(generated):
        job = ExtractionJob(as_document(generated), settings)
        runner.run([job], force_sync=True)
        return job, settings

    return run


def test_absent_fields_come_back_null_not_fabricated(extract, corpus):
    doc = first_of(corpus, "narrative_abstract", doi=None, funding_source=None)

    job, _ = extract(doc)

    assert job.extraction["doi"] is None
    assert job.extraction["funding_source"] is None
    assert job.extraction["evidence"]["doi"] is None


def test_url_doi_is_normalised_to_a_bare_doi(extract, corpus):
    doc = next(d for d in corpus if d.truth.doi and f"https://doi.org/{d.truth.doi}" in d.text)

    job, _ = extract(doc)

    assert job.status is JobStatus.ACCEPTED
    assert job.extraction["doi"] == doc.truth.doi


def test_anonymous_note_is_routed_to_a_human_without_inventing_authors(extract, corpus):
    doc = first_of(corpus, "technical_note")

    job, settings = extract(doc)

    assert job.status is JobStatus.NEEDS_REVIEW
    assert len(job.attempts) == 1  # information absent: not retried
    assert route(job.result(), settings).route == "human_review"
