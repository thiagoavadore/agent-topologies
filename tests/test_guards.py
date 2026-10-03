"""The shared guards and `served_model`, with no model calls."""

import json
import subprocess

import pytest

from topologies.guards import (
    InvalidAfterRetry,
    SkippedWork,
    Validated,
    call_and_validate,
    describe_skipped,
    extract_json,
    may_start_group,
)
from topologies.model import ClaudeCliBackend, ScriptedBackend

SCHEMA = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}


def scripted(*replies: str) -> tuple[ScriptedBackend, list[str]]:
    prompts, queue = [], list(replies)

    def respond(system: str, prompt: str) -> str:
        prompts.append(prompt)
        return queue.pop(0)

    return ScriptedBackend(respond), prompts


def call(backend):
    return call_and_validate(backend, model="m", system="s", prompt="p", schema=SCHEMA)


def test_extract_json_stops_at_first_complete_object():
    assert extract_json('Here: {"n": 1} and also {"n": 2}') == {"n": 1}


def test_extract_json_skips_a_stray_brace_before_the_object():
    assert extract_json('use {braces} like so: {"n": 3}') == {"n": 3}


def test_extract_json_reads_a_fenced_object():
    assert extract_json('```json\n{"n": 4}\n```') == {"n": 4}


def test_extract_json_without_an_object_raises_value_error():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_valid_reply_needs_one_attempt():
    backend, _ = scripted('{"n": 1}')
    result = call(backend)
    assert isinstance(result, Validated) and result.payload == {"n": 1} and result.attempts == 1


def test_bad_reply_is_retried_once_with_the_error_in_the_prompt():
    backend, prompts = scripted("oops", '{"n": 2}')
    result = call(backend)
    assert isinstance(result, Validated) and result.attempts == 2
    assert "broke the contract" in prompts[1]
    assert result.tokens > 0


def test_two_bad_replies_return_invalid_after_retry_not_an_exception():
    backend, prompts = scripted('{"n": "x"}', "still not json")
    result = call(backend)
    assert isinstance(result, InvalidAfterRetry) and result.attempts == 2
    assert len(prompts) == 2


def test_backend_errors_propagate():
    class Broken:
        def call(self, **kwargs):
            raise RuntimeError("timeout")

    with pytest.raises(RuntimeError):
        call(Broken())


def test_budget_blocks_at_and_over_the_ceiling():
    assert may_start_group(99, 100)
    assert not may_start_group(100, 100)
    assert may_start_group(10**9, None)


def test_describe_skipped_names_what_and_why():
    text = describe_skipped([SkippedWork("card-7.md", "token ceiling reached")])
    assert "card-7.md" in text and "token ceiling reached" in text
    assert describe_skipped([]) == "Nothing was skipped."


def fake_cli(monkeypatch, payload: dict) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)


def test_cli_served_model_is_the_one_with_most_output_tokens(monkeypatch):
    fake_cli(monkeypatch, {
        "result": "hi", "is_error": False,
        "usage": {"input_tokens": 1, "output_tokens": 5},
        "modelUsage": {"small": {"outputTokens": 2}, "big": {"outputTokens": 40}},
    })
    reply = ClaudeCliBackend().call(model="alias", system="s", prompt="p")
    assert (reply.model, reply.served_model, reply.served_models) == ("alias", "big", ("big", "small"))


def test_cli_without_model_usage_leaves_served_model_empty(monkeypatch):
    fake_cli(monkeypatch, {"result": "hi", "is_error": False, "usage": {"input_tokens": 1, "output_tokens": 5}})
    assert ClaudeCliBackend().call(model="alias", system="s", prompt="p").served_model == ""


def test_sdk_served_model_comes_from_the_response(monkeypatch):
    from types import SimpleNamespace

    from topologies.model import AnthropicSdkBackend

    response = SimpleNamespace(
        model="claude-sonnet-5-5-20260901",
        content=[SimpleNamespace(type="text", text="hi")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=2, cache_creation_input_tokens=None, cache_read_input_tokens=None),
    )
    backend = AnthropicSdkBackend.__new__(AnthropicSdkBackend)
    backend._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    reply = backend.call(model="alias", system="s", prompt="p")
    assert (reply.model, reply.served_model) == ("alias", "claude-sonnet-5-5-20260901")
