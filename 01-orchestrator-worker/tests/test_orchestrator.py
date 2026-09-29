"""End-to-end runs against a scripted model: every guard fires on the real fixtures, no network."""

import json
import re
from pathlib import Path

import pytest

from orchestrator import ROUTER_SYSTEM, SYNTHESIS_SYSTEM, Guards, WorkerResult, merge_findings, run_review
from scoring import load_planted, score
from topologies.model import ScriptedBackend

SERVICES = Path(__file__).resolve().parents[1] / "fixtures" / "services"
PLANTED = load_planted()
ALL_CARDS = sorted(path.name for path in SERVICES.glob("*.md"))


def plan(*card_groups: list[str]) -> str:
    return json.dumps({"subtasks": [{"id": f"w{i}", "files": group, "focus": "all"} for i, group in enumerate(card_groups)]})


def one_worker_per_card(_prompt: str) -> str:
    return plan(*[[name] for name in ALL_CARDS])


def perfect_worker(prompt: str) -> str:
    files = re.findall(r"=== FILE: (\S+) ===", prompt)
    findings = [
        {"file": file, "category": category, "severity": "high", "evidence": "quoted line"}
        for file, category in sorted(PLANTED)
        if file in files
    ]
    return json.dumps({"findings": findings})


def scripted(router=one_worker_per_card, worker=perfect_worker, synthesis=lambda _: "summary"):
    """Return a backend that routes each call by its system prompt, plus a log of (role, prompt)."""
    calls: list[tuple[str, str]] = []

    def respond(system: str, prompt: str) -> str:
        if system == ROUTER_SYSTEM:
            role, reply = "router", router
        elif system == SYNTHESIS_SYSTEM:
            role, reply = "synthesis", synthesis
        else:
            role, reply = "worker", worker
        calls.append((role, prompt))
        return reply(prompt)

    return ScriptedBackend(respond), calls


def roles(calls: list[tuple[str, str]]) -> list[str]:
    return [role for role, _ in calls]


def review(backend, break_router: bool = False, **guards):
    record = run_review(SERVICES, backend, "supervisor", "worker", Guards(**guards), break_router=break_router)
    return record, score(record.findings, PLANTED)


def raise_error(_prompt: str) -> str:
    raise RuntimeError("backend down")


# Fan-out cap


def test_fan_out_cap_merges_eight_subtasks_into_three_workers_without_losing_coverage():
    backend, calls = scripted()
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert record.planned_subtasks == 8
    assert record.cap_applied and record.dispatched_workers == 3
    assert roles(calls).count("worker") == 3
    assert result["recall"] == 1.0 and record.uncovered_files == []


def test_without_cap_the_router_plan_runs_as_is():
    backend, calls = scripted()
    record, result = review(backend, fan_out_cap=None, token_ceiling=None)
    assert record.dispatched_workers == 8 and roles(calls).count("worker") == 8
    assert result["recall"] == 1.0


@pytest.mark.parametrize("guards", [{"fan_out_cap": 0}, {"fan_out_cap": -1}, {"max_parallel": 0}])
def test_nonsense_guard_values_are_rejected(guards):
    with pytest.raises(ValueError):
        Guards(**guards)


# Router and fallback plan


def test_killed_router_falls_back_to_a_deterministic_plan():
    backend, calls = scripted()
    record, result = review(backend, break_router=True, fan_out_cap=3, token_ceiling=None)
    assert "router" not in roles(calls)
    assert record.router_status == "failed_fallback"
    assert [len(worker["files"]) for worker in record.workers] == [3, 3, 2]
    assert result["recall"] == 1.0


def test_router_call_that_raises_falls_back():
    backend, _ = scripted(router=raise_error)
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert record.router_status == "failed_fallback" and "router call failed" in record.router_error
    assert result["recall"] == 1.0


def test_router_reply_without_json_falls_back():
    backend, _ = scripted(router=lambda _: "Sure, here is my plan: four workers.")
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert record.router_status == "failed_fallback" and "router plan rejected" in record.router_error
    assert result["recall"] == 1.0


def test_router_plan_naming_an_unknown_card_is_rejected_and_falls_back():
    backend, _ = scripted(router=lambda _: plan(["ghost.md"]))
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert record.router_status == "failed_fallback" and "ghost.md" in record.router_error
    assert result["recall"] == 1.0


def test_router_plan_assigning_a_card_twice_is_rejected_and_falls_back():
    backend, _ = scripted(router=lambda _: plan(ALL_CARDS, [ALL_CARDS[0]]))
    record, _ = review(backend, fan_out_cap=3, token_ceiling=None)
    assert record.router_status == "failed_fallback" and "duplicated" in record.router_error


def test_card_the_router_forgot_gets_a_coverage_worker():
    backend, _ = scripted(router=lambda _: plan(*[[name] for name in ALL_CARDS[:-1]]))
    record, result = review(backend, fan_out_cap=None, token_ceiling=None)
    coverage = [worker for worker in record.workers if worker["subtask_id"] == "coverage"]
    assert [worker["files"] for worker in coverage] == [[ALL_CARDS[-1]]]
    assert result["recall"] == 1.0


# Strict format


def test_payload_breaking_the_contract_is_retried_once_then_not_merged():
    def broken_worker(_prompt: str) -> str:
        return json.dumps({"findings": [{"file": "x", "category": "vibes", "severity": "high", "evidence": "e"}]})

    backend, calls = scripted(worker=broken_worker)
    record, _ = review(backend, fan_out_cap=3, token_ceiling=None)
    assert roles(calls).count("worker") == 3 * 2  # 3 workers, 2 attempts each
    assert all(worker["status"] == "invalid" and worker["attempts"] == 2 for worker in record.workers)
    assert record.findings == [] and record.uncovered_files == ALL_CARDS


def test_reply_without_json_is_invalid_after_two_attempts():
    backend, _ = scripted(worker=lambda _: "no findings here")
    record, _ = review(backend, fan_out_cap=3, token_ceiling=None)
    assert all(worker["status"] == "invalid" and worker["attempts"] == 2 for worker in record.workers)


def test_retry_with_the_contract_error_can_recover():
    def fixed_on_retry(prompt: str) -> str:
        return perfect_worker(prompt) if "broke the contract" in prompt else "not json"

    backend, calls = scripted(worker=fixed_on_retry)
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert all(worker["status"] == "ok" and worker["attempts"] == 2 for worker in record.workers)
    assert result["recall"] == 1.0
    retry_prompts = [prompt for role, prompt in calls if role == "worker" and "broke the contract" in prompt]
    assert len(retry_prompts) == 3 and all("Expecting value" in prompt for prompt in retry_prompts)


def test_worker_that_raises_marks_its_cards_not_reviewed_and_the_review_continues():
    backend, calls = scripted(worker=raise_error)
    record, _ = review(backend, fan_out_cap=3, token_ceiling=None)
    assert all(worker["status"] == "error" and worker["attempts"] == 1 for worker in record.workers)
    assert record.uncovered_files == ALL_CARDS
    assert "synthesis" in roles(calls)


def test_findings_outside_the_brief_are_dropped():
    def overreaching_worker(prompt: str) -> str:
        own = json.loads(perfect_worker(prompt))["findings"]
        foreign = {"file": "not-mine.md", "category": "no_owner", "severity": "high", "evidence": "e"}
        return json.dumps({"findings": own + [foreign]})

    backend, _ = scripted(worker=overreaching_worker)
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert all(worker["out_of_brief"] == 1 for worker in record.workers)
    assert result["extra_findings"] == 0 and result["recall"] == 1.0


# Token budget


def test_token_ceiling_already_exceeded_skips_every_worker():
    backend, calls = scripted()
    record, _ = review(backend, fan_out_cap=None, token_ceiling=1, max_parallel=4)
    assert record.ceiling_hit
    assert "synthesis" not in roles(calls)
    assert record.dispatched_workers == 0 and len(record.uncovered_files) == 8
    assert record.summary.startswith("Token ceiling reached")


def test_token_ceiling_hit_after_the_first_wave_skips_the_second_and_names_its_cards():
    backend, _ = scripted()
    full, _ = review(backend, fan_out_cap=None, token_ceiling=None, max_parallel=4)
    first_wave_tokens = sum(worker["tokens"] for worker in full.workers[:4])
    ceiling = full.tokens_by_role["router"] + first_wave_tokens

    backend, calls = scripted()
    record, _ = review(backend, fan_out_cap=None, token_ceiling=ceiling, max_parallel=4)
    skipped = [name for worker in record.workers if worker["status"] == "skipped_ceiling" for name in worker["files"]]
    assert record.ceiling_hit and record.dispatched_workers == 4
    assert len(skipped) == 4 and sorted(skipped) == record.uncovered_files
    assert "synthesis" not in roles(calls)
    assert all(name in record.summary for name in skipped)


# Merge and summary


def test_merge_keeps_the_highest_severity_per_card_and_category():
    low = {"file": "a.md", "category": "no_owner", "severity": "low", "evidence": "e"}
    high = {**low, "severity": "high"}
    results = [
        WorkerResult(subtask_id="w1", files=["a.md"], focus="", status="ok", findings=[low]),
        WorkerResult(subtask_id="w2", files=["a.md"], focus="", status="ok", findings=[high]),
    ]
    assert merge_findings(results) == [high]


def test_summary_prompt_names_the_cards_that_were_not_reviewed():
    backend, calls = scripted(worker=raise_error)
    review(backend, fan_out_cap=3, token_ceiling=None)
    synthesis_prompt = next(prompt for role, prompt in calls if role == "synthesis")
    assert json.loads(synthesis_prompt)["not_reviewed"] == ALL_CARDS


def test_failed_summary_call_keeps_the_findings():
    backend, _ = scripted(synthesis=raise_error)
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert record.summary.startswith("Synthesis failed")
    assert result["recall"] == 1.0
