"""The code behind the published token numbers: `claude -p` usage parsing and the results table."""

import json
import subprocess
from pathlib import Path

import pytest

from experiment import summarise
from topologies.model import ClaudeCliBackend

RESULTS = Path(__file__).resolve().parents[1] / "results"


def fake_claude(monkeypatch, stdout: str, returncode: int = 0) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="boom")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)


def claude_output(**usage) -> str:
    return json.dumps({"result": "hi", "is_error": False, "usage": usage})


def test_input_tokens_include_cache_writes_and_reads(monkeypatch):
    fake_claude(monkeypatch, claude_output(input_tokens=10, cache_creation_input_tokens=200, cache_read_input_tokens=3000, output_tokens=7))
    reply = ClaudeCliBackend().call(model="m", system="s", prompt="p")
    assert (reply.input_tokens, reply.output_tokens, reply.total_tokens) == (3210, 7, 3217)


def test_null_cache_fields_count_as_zero(monkeypatch):
    fake_claude(monkeypatch, claude_output(input_tokens=10, cache_creation_input_tokens=None, output_tokens=7))
    assert ClaudeCliBackend().call(model="m", system="s", prompt="p").input_tokens == 10


def test_zero_usage_is_warned_about(monkeypatch, capsys):
    fake_claude(monkeypatch, claude_output(input_tokens=0, output_tokens=0))
    ClaudeCliBackend().call(model="m", system="s", prompt="p")
    assert "[warning]" in capsys.readouterr().err


@pytest.mark.parametrize(
    "stdout, returncode, message",
    [
        ("", 1, "failed"),
        ("not json", 0, "unreadable"),
        (json.dumps({"is_error": True, "result": "rate limited", "usage": {}}), 0, "rate limited"),
    ],
)
def test_cli_failures_raise_readable_errors(monkeypatch, stdout, returncode, message):
    fake_claude(monkeypatch, stdout, returncode)
    with pytest.raises(RuntimeError, match=message):
        ClaudeCliBackend().call(model="m", system="s", prompt="p")


def test_published_summary_matches_the_published_runs():
    assert summarise(RESULTS / "runs.jsonl") == (RESULTS / "summary.md").read_text().rstrip("\n")
