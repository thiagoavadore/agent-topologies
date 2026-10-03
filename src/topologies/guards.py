"""Safety checks shared by topologies 02 and 03: schema-checked calls, a token budget, named skips."""

import json
from dataclasses import dataclass

import jsonschema

from topologies.model import Backend


@dataclass(frozen=True)
class Validated:
    payload: dict
    attempts: int
    tokens: int
    served_models: tuple[str, ...]


@dataclass(frozen=True)
class InvalidAfterRetry:
    error: str
    attempts: int
    tokens: int
    served_models: tuple[str, ...]


@dataclass(frozen=True)
class SkippedWork:
    what: str
    reason: str


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a reply, tolerating prose or a code fence around it."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    # Last resort for replies the decoder rejects: widest brace span.
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def call_and_validate(
    backend: Backend, *, model: str, system: str, prompt: str, schema: dict
) -> Validated | InvalidAfterRetry:
    """Call the model, check the reply against `schema`, retry once, never raise on bad output.

    Backend errors (timeouts, failed `claude -p`) still raise: they are not bad model output.
    """
    tokens, served, error = 0, [], ""
    for attempt in (1, 2):
        reply = backend.call(model=model, system=system, prompt=prompt)
        tokens += reply.total_tokens
        served.extend(name for name in reply.served_models if name not in served)
        try:
            payload = extract_json(reply.text)
            jsonschema.validate(payload, schema)
        except (ValueError, jsonschema.ValidationError) as problem:
            error = str(problem)[:300]
            prompt = (
                f"{prompt}\n\nYour previous reply broke the contract: {error}\n"
                "Reply again with valid JSON only."
            )
            continue
        return Validated(payload=payload, attempts=attempt, tokens=tokens, served_models=tuple(served))
    return InvalidAfterRetry(error=error, attempts=2, tokens=tokens, served_models=tuple(served))


def may_start_group(spent: int, ceiling: int | None) -> bool:
    """Say whether the next group of calls may start; the ceiling is soft, so a started group can overshoot."""
    return ceiling is None or spent < ceiling


def describe_skipped(skipped: list[SkippedWork]) -> str:
    """List skipped work for a summary, one line per item, or say that nothing was skipped."""
    if not skipped:
        return "Nothing was skipped."
    return "\n".join(f"- Not done: {item.what} ({item.reason})" for item in skipped)
