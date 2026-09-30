"""Request building and response parsing against the real SDK types (no network)."""

from types import SimpleNamespace

import pytest
from anthropic.types import Message
from anthropic.types.messages import MessageBatchIndividualResponse

from extraction_pipeline.llm import (
    AnthropicBatchClient,
    CallStatus,
    Usage,
    build_params,
    calibrated_token_counter,
    parse_message,
)
from extraction_pipeline.prompts import CITATIONS_TOOL_NAME
from extraction_pipeline.schema import TOOL_NAME


def message(content, stop_reason="tool_use", **usage):
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 200} | usage,
        }
    )


TOOL_USE = {"type": "tool_use", "id": "t1", "name": TOOL_NAME, "input": {"title": "x"}}


def test_params_force_the_tool_through_the_prompt_not_tool_choice(settings):
    params = build_params(settings, "hello")

    assert params["tool_choice"] == {"type": "auto"}  # forced choice 400s on current models
    assert params["tools"][0]["strict"] is True
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"] == {"effort": "medium"}
    assert "thinking" not in params  # adaptive by default on this model


def test_citation_chunks_get_the_citations_tool(settings):
    tool = build_params(settings, "refs", citations_only=True)["tools"][0]

    assert tool["name"] == CITATIONS_TOOL_NAME
    assert tool["input_schema"]["required"] == ["cited_works"]


@pytest.mark.parametrize(
    ("content", "stop", "expected"),
    [
        ([TOOL_USE], "tool_use", CallStatus.TOOL_CALL),
        ([{"type": "text", "text": "Here you go"}], "end_turn", CallStatus.NO_TOOL_CALL),
        ([TOOL_USE], "max_tokens", CallStatus.TRUNCATED),
        ([], "refusal", CallStatus.REFUSAL),
    ],
)
def test_parse_message_classifies_every_stop_reason(content, stop, expected):
    result = parse_message(message(content, stop), model="claude-opus-5-5", batch=False)

    assert result.status is expected
    if expected is CallStatus.TOOL_CALL:
        assert result.tool_input == {"title": "x"}


def test_usage_cost_applies_the_batch_discount():
    msg = message([TOOL_USE], cache_read_input_tokens=10_000)

    sync = Usage.from_message(msg, "claude-opus-5-5", batch=False)
    batch = Usage.from_message(msg, "claude-opus-5-5", batch=True)

    assert sync.cost_usd == pytest.approx((1000 * 4 + 200 * 20 + 10_000 * 0.2) / 1e6)
    assert batch.cost_usd == pytest.approx(sync.cost_usd / 2)


def _batch_item(custom_id, result):
    return MessageBatchIndividualResponse.model_validate({"custom_id": custom_id, "result": result})


def test_batch_results_are_mapped_by_type(settings):
    items = [
        _batch_item("a", {"type": "succeeded", "message": message([TOOL_USE]).model_dump()}),
        _batch_item(
            "b",
            {
                "type": "errored",
                "error": {
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": "prompt is too long"},
                },
            },
        ),
        _batch_item(
            "c",
            {
                "type": "errored",
                "error": {
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": "busy"},
                },
            },
        ),
        _batch_item("d", {"type": "expired"}),
    ]
    fake = SimpleNamespace(
        messages=SimpleNamespace(batches=SimpleNamespace(results=lambda _id: iter(items)))
    )

    results = dict(AnthropicBatchClient(settings, fake).results("b1"))  # type: ignore[arg-type]

    assert results["a"].status is CallStatus.TOOL_CALL
    assert results["a"].usage.cost_usd > 0
    assert results["b"].status is CallStatus.INVALID_REQUEST
    assert "too long" in results["b"].detail
    assert results["c"].status is CallStatus.API_ERROR
    assert results["d"].status is CallStatus.API_ERROR


def test_calibrated_counter_uses_one_count_tokens_call():
    calls = []

    def count_tokens(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(input_tokens=100)

    fake = SimpleNamespace(messages=SimpleNamespace(count_tokens=count_tokens))
    counter = calibrated_token_counter(fake, "claude-opus-5-5", "x" * 400)  # type: ignore[arg-type]

    assert counter("y" * 40) == 10  # 4 characters per token, measured
    assert counter("z" * 80) == 20
    assert len(calls) == 1


def test_effort_is_omitted_when_the_model_does_not_accept_it(settings):
    params = build_params(settings.with_(model="claude-haiku-4-5", effort=None), "hello")

    assert "output_config" not in params
    assert params["model"] == "claude-haiku-4-5"


def test_effort_none_is_read_from_the_environment(monkeypatch):
    from extraction_pipeline.config import Settings

    monkeypatch.setenv("EXTRACTOR_EFFORT", "none")

    assert Settings.from_env().effort is None
