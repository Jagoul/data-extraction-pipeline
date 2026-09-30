"""Everything that touches the Claude API: request building, response parsing, and clients.

One request builder serves both the synchronous API and the Message Batches API, so a batch
request is byte-for-byte the request a synchronous call would send (minus `fallbacks`, which the
Batches API rejects). Responses from either path are parsed into the same `CallOutcome`.

Structured output uses a user-defined tool with `strict: true` and `tool_choice: auto`. Current
models reject forced tool choice (`any` / `tool`), so the instruction to call the tool lives in
the prompt, and a response without a tool call is handled as its own outcome.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from extraction_pipeline.config import BATCH_DISCOUNT, FALLBACK_MODELS, PRICES, Settings
from extraction_pipeline.prompts import CITATIONS_TOOL_NAME, system_prompt
from extraction_pipeline.schema import API_SCHEMA, TOOL_NAME, extraction_tool

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def citations_tool(*, strict: bool = True) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": CITATIONS_TOOL_NAME,
        "description": "Record every cited work in one part of a long document's reference "
        "list. Call once per part.",
        "input_schema": {
            "type": "object",
            "properties": {"cited_works": copy.deepcopy(API_SCHEMA["properties"]["cited_works"])},
            "required": ["cited_works"],
            "additionalProperties": False,
        },
    }
    if strict:
        tool["strict"] = True
    return tool


def build_params(
    settings: Settings, user_message: str, *, citations_only: bool = False
) -> dict[str, Any]:
    """Messages API parameters for one extraction request (sync or batch)."""
    tool = (
        citations_tool(strict=settings.strict_tools)
        if citations_only
        else extraction_tool(strict=settings.strict_tools)
    )
    params: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "system": [
            {
                "type": "text",
                "text": system_prompt(few_shot=settings.few_shot),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "tools": [tool],
        "tool_choice": {"type": "auto"},
        "messages": [{"role": "user", "content": user_message}],
    }
    if settings.effort is not None:
        params["output_config"] = {"effort": settings.effort}
    return params


# --- Outcomes ----------------------------------------------------------------------------------


class CallStatus(StrEnum):
    TOOL_CALL = "tool_call"
    NO_TOOL_CALL = "no_tool_call"
    TRUNCATED = "truncated"  # stop_reason max_tokens: output (or tool input) cut off
    REFUSAL = "refusal"
    API_ERROR = "api_error"  # retryable: server error, overload, expired, canceled
    INVALID_REQUEST = "invalid_request"  # not retryable as-is


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cost_usd += other.cost_usd

    @classmethod
    def from_message(cls, message: Any, model: str, *, batch: bool) -> Usage:
        u = getattr(message, "usage", None)
        if u is None:
            return cls()
        usage = cls(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )
        price = PRICES.get(model)
        if price:
            usage.cost_usd = (
                (
                    usage.input_tokens * price["input"]
                    + usage.output_tokens * price["output"]
                    + usage.cache_write_tokens * price["cache_write"]
                    + usage.cache_read_tokens * price["cache_read"]
                )
                / 1_000_000
                * (BATCH_DISCOUNT if batch else 1.0)
            )
        return usage


@dataclass
class CallOutcome:
    status: CallStatus
    tool_input: dict[str, Any] | None = None
    detail: str = ""
    usage: Usage = field(default_factory=Usage)

    @property
    def retryable_transport(self) -> bool:
        return self.status is CallStatus.API_ERROR


def parse_message(message: Any, *, model: str, batch: bool) -> CallOutcome:
    """Classify a Message (sync or batch) and pull out the tool input, if any."""
    usage = Usage.from_message(message, model, batch=batch)
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        return CallOutcome(CallStatus.REFUSAL, detail=f"refusal ({category})", usage=usage)
    if message.stop_reason == "max_tokens":
        return CallOutcome(CallStatus.TRUNCATED, detail="hit max_tokens", usage=usage)
    for block in message.content:
        if block.type == "tool_use" and block.name in (TOOL_NAME, CITATIONS_TOOL_NAME):
            return CallOutcome(CallStatus.TOOL_CALL, tool_input=dict(block.input), usage=usage)
    return CallOutcome(
        CallStatus.NO_TOOL_CALL, detail="the model answered without the tool", usage=usage
    )


def calibrated_token_counter(
    client: anthropic.Anthropic, model: str, sample: str
) -> Callable[[str], int]:
    """A fast local counter calibrated once against the real tokenizer via `count_tokens`.

    Counting every line through the API would cost one call per line, so the sample text is
    counted once and the resulting characters-per-token ratio is applied locally.
    """
    tokens = client.messages.count_tokens(
        model=model, messages=[{"role": "user", "content": sample}]
    ).input_tokens
    chars_per_token = max(1.0, len(sample) / max(1, tokens))
    return lambda text: max(1, math.ceil(len(text) / chars_per_token))


# --- Clients -----------------------------------------------------------------------------------


class MessagesClient(Protocol):
    def create(self, params: dict[str, Any]) -> CallOutcome: ...


class SyncClient:
    """Synchronous Messages API with server-side refusal fallbacks and typed error handling."""

    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self.settings = settings
        self.client = client or anthropic.Anthropic()

    def create(self, params: dict[str, Any]) -> CallOutcome:
        try:
            if params["model"] in FALLBACK_MODELS:
                message = self.client.beta.messages.create(
                    **params, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                message = self.client.messages.create(**params)
        except anthropic.BadRequestError as exc:
            return CallOutcome(CallStatus.INVALID_REQUEST, detail=str(exc.message))
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
            return CallOutcome(CallStatus.API_ERROR, detail=type(exc).__name__)
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                return CallOutcome(CallStatus.API_ERROR, detail=f"HTTP {exc.status_code}")
            raise
        return parse_message(message, model=params["model"], batch=False)


@dataclass(frozen=True)
class BatchStatus:
    batch_id: str
    ended: bool
    created_at: datetime
    ended_at: datetime | None
    counts: dict[str, int]


class BatchClient(Protocol):
    def submit(self, requests: dict[str, dict[str, Any]]) -> str: ...
    def status(self, batch_id: str) -> BatchStatus: ...
    def results(self, batch_id: str) -> Iterator[tuple[str, CallOutcome]]: ...


class AnthropicBatchClient:
    """Message Batches API: submit keyed by custom_id, poll, stream results in any order."""

    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self.settings = settings
        self.client = client or anthropic.Anthropic()

    def submit(self, requests: dict[str, dict[str, Any]]) -> str:
        batch = self.client.messages.batches.create(
            requests=[
                Request(custom_id=custom_id, params=MessageCreateParamsNonStreaming(**params))  # type: ignore[typeddict-item]
                for custom_id, params in requests.items()
            ]
        )
        return batch.id

    def status(self, batch_id: str) -> BatchStatus:
        batch = self.client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        return BatchStatus(
            batch_id=batch.id,
            ended=batch.processing_status == "ended",
            created_at=batch.created_at,
            ended_at=batch.ended_at,
            counts={
                "processing": counts.processing,
                "succeeded": counts.succeeded,
                "errored": counts.errored,
                "canceled": counts.canceled,
                "expired": counts.expired,
            },
        )

    def results(self, batch_id: str) -> Iterator[tuple[str, CallOutcome]]:
        for item in self.client.messages.batches.results(batch_id):
            result = item.result
            if result.type == "succeeded":
                yield (
                    item.custom_id,
                    parse_message(result.message, model=self.settings.model, batch=True),
                )
            elif result.type == "errored":
                error = result.error.error
                status = (
                    CallStatus.INVALID_REQUEST
                    if error.type == "invalid_request_error"
                    else CallStatus.API_ERROR
                )
                yield item.custom_id, CallOutcome(status, detail=f"{error.type}: {error.message}")
            else:  # "expired" or "canceled": the request never ran; safe to resubmit
                yield item.custom_id, CallOutcome(CallStatus.API_ERROR, detail=result.type)
