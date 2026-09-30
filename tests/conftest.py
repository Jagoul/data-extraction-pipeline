"""Shared fixtures: settings, documents, an oracle extractor, and fake API clients."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from extraction_pipeline.config import Settings
from extraction_pipeline.corpus import GeneratedDocument, generate
from extraction_pipeline.documents import Document
from extraction_pipeline.llm import BatchStatus, CallOutcome, CallStatus, Usage
from extraction_pipeline.schema import GROUNDED_FIELDS, SCORED_FIELDS
from extraction_pipeline.validation import DOI_OR_URL

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def corpus() -> list[GeneratedDocument]:
    return generate(seed=7)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(poll_seconds=0, data_dir=tmp_path / "data", runs_dir=tmp_path / "runs")


def as_document(generated: GeneratedDocument) -> Document:
    return Document(generated.truth.doc_id, generated.truth.doc_type, generated.text)


def first_of(corpus: list[GeneratedDocument], doc_type: str, **truth: Any) -> GeneratedDocument:
    for doc in corpus:
        if doc.truth.doc_type == doc_type and all(
            getattr(doc.truth, k) == v for k, v in truth.items()
        ):
            return doc
    raise LookupError(f"no {doc_type} document with {truth}")


def _quote_for(value: Any, text: str, *, field: str = "") -> str | None:
    """The first line of the document that supports the value, as an evidence quote.

    A year only counts where it is written as a date, not inside a DOI or URL.
    """
    forms = [str(value)]
    if isinstance(value, int):
        forms.append(f"{value:,}")
    for line in text.splitlines():
        searched = DOI_OR_URL.sub(" ", line) if field == "publication_year" else line
        if any(form in searched for form in forms):
            return line.strip()
    return None


def oracle_extraction(doc: GeneratedDocument) -> dict[str, Any]:
    """A perfect, fully grounded extraction built from the ground truth."""
    t = doc.truth
    extraction: dict[str, Any] = {
        "title": t.title,
        "authors": t.authors or ["unknown"],
        "publication_year": t.publication_year,
        "doi": t.doi,
        "study_type": t.study_type,
        "study_type_detail": t.study_type_detail,
        "sample_size": t.sample_size,
        "funding_source": t.funding_source,
        "primary_outcome": t.primary_outcome,
        "key_findings": ["The intervention improved outcomes."],
        "cited_works": copy.deepcopy(t.cited_works),
        "field_confidence": dict.fromkeys(SCORED_FIELDS, 0.95),
        "evidence": {},
    }
    if not t.authors:
        extraction["field_confidence"]["authors"] = 0.0
    for name in GROUNDED_FIELDS:
        value = extraction[name]
        extraction["evidence"][name] = (
            None if value is None else _quote_for(value, doc.text, field=name)
        )
    return extraction


def tool_call(tool_input: dict[str, Any]) -> CallOutcome:
    return CallOutcome(CallStatus.TOOL_CALL, tool_input=tool_input, usage=Usage(10, 5))


def outcome(status: CallStatus, detail: str = "") -> CallOutcome:
    return CallOutcome(status, detail=detail, usage=Usage(10, 5))


Responder = Callable[[str, dict[str, Any]], CallOutcome]


@dataclass
class FakeSyncClient:
    """Answers each request with `responder(custom_id-less key, params)`."""

    responder: Callable[[dict[str, Any]], CallOutcome]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, params: dict[str, Any]) -> CallOutcome:
        self.calls.append(params)
        return self.responder(params)


@dataclass
class FakeBatchClient:
    """A Message Batches API stand-in. Results come back in reverse order, like the real API
    returns them in any order, so callers must route by custom_id."""

    responder: Responder
    seconds_per_batch: float = 600
    polls_before_end: int = 1
    batches: list[dict[str, dict[str, Any]]] = field(default_factory=list)
    drop: set[str] = field(default_factory=set)
    _polls: dict[str, int] = field(default_factory=dict)

    def submit(self, requests: dict[str, dict[str, Any]]) -> str:
        self.batches.append(dict(requests))
        batch_id = f"msgbatch_{len(self.batches)}"
        self._polls[batch_id] = 0
        return batch_id

    def status(self, batch_id: str) -> BatchStatus:
        self._polls[batch_id] += 1
        index = int(batch_id.rsplit("_", 1)[1])
        created = datetime(2026, 9, 30, 9, 0, tzinfo=UTC) + timedelta(hours=index)
        ended = self._polls[batch_id] > self.polls_before_end
        return BatchStatus(
            batch_id=batch_id,
            ended=ended,
            created_at=created,
            ended_at=created + timedelta(seconds=self.seconds_per_batch) if ended else None,
            counts={"processing": 0 if ended else 1},
        )

    def results(self, batch_id: str) -> Iterator[tuple[str, CallOutcome]]:
        requests = self.batches[int(batch_id.rsplit("_", 1)[1]) - 1]
        for cid, params in reversed(list(requests.items())):
            if cid not in self.drop:
                yield cid, self.responder(cid, params)
