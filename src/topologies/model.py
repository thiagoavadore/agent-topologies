"""One thin model client with swappable backends, so every topology folder shares the same call shape."""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    model: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Backend(Protocol):
    def call(self, *, model: str, system: str, prompt: str) -> Reply: ...


class ClaudeCliBackend:
    """Headless Claude Code (`claude -p`): runs on a Claude subscription, no API key needed.

    Tools, settings, MCP servers and session files are switched off so a call is one model turn.
    Claude Code still adds a fixed prompt overhead of a few hundred input tokens per call.
    """

    def call(self, *, model: str, system: str, prompt: str) -> Reply:
        command = [
            "claude", "-p", prompt,
            "--model", model,
            "--system-prompt", system,
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--output-format", "json",
        ]
        # A neutral cwd keeps any project CLAUDE.md out of the prompt.
        with tempfile.TemporaryDirectory() as cwd:
            completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=600)
        if completed.returncode != 0:
            raise RuntimeError(f"claude -p failed ({completed.returncode}): {completed.stderr.strip()[:500]}")
        payload = json.loads(completed.stdout)
        if payload.get("is_error"):
            raise RuntimeError(f"claude -p returned an error: {payload.get('result')}")
        usage = payload["usage"]
        input_tokens = (
            usage["input_tokens"]
            + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
        )
        return Reply(text=payload["result"], input_tokens=input_tokens, output_tokens=usage["output_tokens"], model=model)


class AnthropicSdkBackend:
    """The Anthropic Python SDK: needs ANTHROPIC_API_KEY (or an `ant auth login` profile)."""

    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.Anthropic()

    def call(self, *, model: str, system: str, prompt: str) -> Reply:
        response = self._client.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        input_tokens = (
            usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0)
        )
        return Reply(text=text, input_tokens=input_tokens, output_tokens=usage.output_tokens, model=model)


class ScriptedBackend:
    """Deterministic stand-in for tests: `respond(system, prompt)` returns the reply text."""

    def __init__(self, respond: Callable[[str, str], str]) -> None:
        self._respond = respond

    def call(self, *, model: str, system: str, prompt: str) -> Reply:
        text = self._respond(system, prompt)
        return Reply(text=text, input_tokens=len(system + prompt) // 4, output_tokens=len(text) // 4, model=model)


def backend_from_name(name: str | None = None) -> Backend:
    name = name or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    if name == "claude-cli":
        return ClaudeCliBackend()
    if name == "anthropic-sdk":
        return AnthropicSdkBackend()
    raise ValueError(f"unknown backend {name!r}: use claude-cli or anthropic-sdk")
