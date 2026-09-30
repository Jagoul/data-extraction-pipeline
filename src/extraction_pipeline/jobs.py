"""One document's extraction as a state machine, shared by the sync and batch runners.

A job exposes the requests it wants sent next (`pending`) and learns from each outcome
(`observe`). The runner only moves requests and outcomes around; every decision lives here:

    extract ──► tool call ──► validate ──► valid ─────────────────────► ACCEPTED
       │                         │
       │                         ├─ retryable issues, attempts left ──► correction request
       │                         └─ unresolvable issues / out of tries ► NEEDS_REVIEW
       ├─ truncated (max_tokens) ──► split into chunks ──► merge ──► validate
       ├─ no tool call ────────────► correction request ("call the tool")
       ├─ API error / expired ─────► resubmit unchanged (bounded)
       └─ refusal / invalid request ► FAILED
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from extraction_pipeline.config import Settings
from extraction_pipeline.documents import Document, TokenCounter, approx_tokens, split_into_chunks
from extraction_pipeline.llm import CallOutcome, CallStatus, Usage, build_params
from extraction_pipeline.prompts import (
    citations_chunk_message,
    correction_message,
    extraction_message,
    first_chunk_message,
)
from extraction_pipeline.validation import Issue, IssueKind, validate_extraction

CONTEXT_OVERFLOW_HINTS = ("too long", "context", "exceed")


class JobStatus(StrEnum):
    ACTIVE = "active"
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


@dataclass
class Attempt:
    """One validation round: what came back and which issues it had."""

    number: int
    request: str  # "extract" | "correct" | "chunked"
    call_status: str
    issues: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ExtractionJob:
    doc: Document
    settings: Settings
    job_id: str = ""
    count_tokens: TokenCounter = approx_tokens
    status: JobStatus = JobStatus.ACTIVE
    mode: str = "single"
    attempts: list[Attempt] = field(default_factory=list)
    extraction: dict[str, Any] | None = None
    issues: tuple[Issue, ...] = ()
    failure: str | None = None
    transport_retries: int = 0
    usage: Usage = field(default_factory=Usage)
    events: list[str] = field(default_factory=list)
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    chunks: list[str] = field(default_factory=list)
    chunk_inputs: dict[int, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.job_id = self.job_id or self.doc.doc_id
        if not self.pending and self.status is JobStatus.ACTIVE:
            message = extraction_message(self.doc.doc_id, self.doc.text)
            self.pending["a1"] = build_params(self.settings, message)

    # --- Public surface --------------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self.status is not JobStatus.ACTIVE

    def observe(self, key: str, outcome: CallOutcome) -> None:
        """Feed back the outcome of the request sent under `key`."""
        params = self.pending.pop(key)
        self.usage.add(outcome.usage)
        self._log(f"{key}: {outcome.status}" + (f" ({outcome.detail})" if outcome.detail else ""))

        match outcome.status:
            case CallStatus.API_ERROR:
                self._retry_transport(key, params, outcome)
            case CallStatus.INVALID_REQUEST:
                if self.mode == "single" and _looks_like_overflow(outcome.detail):
                    self._switch_to_chunks("request too large for one call")
                else:
                    self._fail(f"invalid request: {outcome.detail}")
            case CallStatus.REFUSAL:
                self._fail(outcome.detail or "refusal")
            case CallStatus.TRUNCATED:
                if self.mode == "single":
                    self._switch_to_chunks("response truncated at max_tokens")
                else:
                    self._fail(f"chunk {key} truncated even after chunking")
            case CallStatus.NO_TOOL_CALL if self.mode == "chunked" and _chunk_index(key) > 1:
                self._retry_transport(key, params, outcome)  # a citations chunk: just resend it
            case CallStatus.NO_TOOL_CALL:
                self._handle_no_tool_call(key)
            case CallStatus.TOOL_CALL:
                assert outcome.tool_input is not None
                self._handle_tool_call(key, outcome.tool_input)

    def abandon(self, reason: str) -> None:
        """Stop the job from outside (e.g. the runner's round limit)."""
        self._fail(reason)

    def result(self) -> dict[str, Any]:
        """Serializable summary of the finished (or failed) job."""
        return {
            "job_id": self.job_id,
            "doc_id": self.doc.doc_id,
            "doc_type": self.doc.doc_type,
            "status": str(self.status),
            "mode": self.mode,
            "chunks": len(self.chunks) or None,
            "extraction": self.extraction,
            "issues": [asdict(i) | {"kind": str(i.kind)} for i in self.issues],
            "attempts": [asdict(a) for a in self.attempts],
            "failure": self.failure,
            "transport_retries": self.transport_retries,
            "usage": asdict(self.usage),
            "events": self.events,
        }

    # --- Transitions -------------------------------------------------------------------------

    def _handle_tool_call(self, key: str, tool_input: dict[str, Any]) -> None:
        if self.mode == "chunked":
            self.chunk_inputs[_chunk_index(key)] = tool_input
            if any(k.startswith(key.split("c")[0]) for k in self.pending):
                return  # wait for the other chunks of this round
            tool_input = self._merge_chunks()
        self.extraction = tool_input
        outcome = validate_extraction(tool_input, self.doc.text)
        self.issues = outcome.issues
        request = (
            "chunked" if self.mode == "chunked" else ("extract" if not self.attempts else "correct")
        )
        self.attempts.append(
            Attempt(
                number=len(self.attempts) + 1,
                request=request,
                call_status=str(CallStatus.TOOL_CALL),
                issues=[
                    {"field": i.field, "kind": str(i.kind), "message": i.message}
                    for i in outcome.issues
                ],
            )
        )
        incomplete = next((i for i in outcome.issues if i.kind is IssueKind.INCOMPLETE), None)
        if incomplete and self.mode == "single":
            self._switch_to_chunks(incomplete.message)
            if self.done or self.pending:
                return  # chunk requests are queued, or the document can't be split
        if outcome.valid:
            self._finish(JobStatus.ACCEPTED, "valid extraction")
        elif len(self.attempts) >= self.settings.max_attempts:
            self._finish(JobStatus.NEEDS_REVIEW, "out of attempts")
        elif not outcome.retryable and not self.settings.retry_unresolvable:
            self._finish(JobStatus.NEEDS_REVIEW, "only unresolvable issues (information absent)")
        else:
            self._request_correction([i.render() for i in outcome.issues])

    def _handle_no_tool_call(self, key: str) -> None:
        self.attempts.append(
            Attempt(
                number=len(self.attempts) + 1,
                request="correct" if self.attempts else "extract",
                call_status=str(CallStatus.NO_TOOL_CALL),
                issues=[
                    {
                        "field": "(response)",
                        "kind": str(IssueKind.FORMAT),
                        "message": "no tool call",
                    }
                ],
            )
        )
        if len(self.attempts) >= self.settings.max_attempts:
            self._fail("the model never called the extraction tool")
        else:
            self._request_correction(
                ["- (response) [format]: you answered in prose; call the tool instead"]
            )

    def _request_correction(self, errors: list[str]) -> None:
        n = len(self.attempts) + 1
        if self.mode == "chunked":
            previous = self.chunk_inputs.get(1)
            text, key = self.chunks[0], f"a{n}c1"
            self.chunk_inputs.pop(1, None)
        else:
            previous, text, key = self.extraction, self.doc.text, f"a{n}"
        message = correction_message(self.doc.doc_id, text, previous, errors)
        self.pending[key] = build_params(self.settings, message)
        self._log(f"retry {key}: {len(errors)} issue(s) sent back")

    def _switch_to_chunks(self, reason: str) -> None:
        chunks = split_into_chunks(
            self.doc.text, self.settings.chunk_token_budget, self.count_tokens
        )
        if len(chunks) < 2:
            self._fail(f"{reason}, and the document cannot be split further")
            return
        self.mode, self.chunks, self.chunk_inputs = "chunked", chunks, {}
        n, total = len(self.attempts) + 1, len(chunks)
        self.pending[f"a{n}c1"] = build_params(
            self.settings, first_chunk_message(self.doc.doc_id, chunks[0], total)
        )
        for part, text in enumerate(chunks[1:], start=2):
            self.pending[f"a{n}c{part}"] = build_params(
                self.settings,
                citations_chunk_message(self.doc.doc_id, text, part, total),
                citations_only=True,
            )
        self._log(f"{reason}: resubmitting as {total} chunks")

    def _merge_chunks(self) -> dict[str, Any]:
        merged = dict(self.chunk_inputs[1])
        merged["cited_works"] = [
            work
            for index in sorted(self.chunk_inputs)
            for work in self.chunk_inputs[index].get("cited_works", [])
        ]
        return merged

    def _retry_transport(self, key: str, params: dict[str, Any], outcome: CallOutcome) -> None:
        self.transport_retries += 1
        if self.transport_retries > self.settings.max_transport_retries:
            self._fail(f"API errors after {self.transport_retries} tries: {outcome.detail}")
        else:
            self.pending[key] = params  # resubmit unchanged

    def _finish(self, status: JobStatus, why: str) -> None:
        self.status = status
        self.pending.clear()
        self._log(f"{status}: {why}")

    def _fail(self, reason: str) -> None:
        self.failure = reason
        self._finish(JobStatus.FAILED, reason)

    def _log(self, message: str) -> None:
        self.events.append(message)


def _chunk_index(key: str) -> int:
    return int(key.rsplit("c", 1)[1])


def _looks_like_overflow(detail: str) -> bool:
    return any(hint in detail.lower() for hint in CONTEXT_OVERFLOW_HINTS)
