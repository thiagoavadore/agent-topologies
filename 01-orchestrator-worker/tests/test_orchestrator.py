"""End-to-end runs against a scripted model: every guard fires on the real fixtures, no network."""

import json
import re
from pathlib import Path

from orchestrator import ROUTER_SYSTEM, SYNTHESIS_SYSTEM, Guards, run_review
from scoring import load_planted, score
from topologies.model import ScriptedBackend

SERVICES = Path(__file__).resolve().parents[1] / "fixtures" / "services"
PLANTED = load_planted()
ALL_CARDS = sorted(path.name for path in SERVICES.glob("*.md"))


def one_worker_per_card(_prompt: str) -> str:
    return json.dumps({"subtasks": [{"id": f"w{i}", "files": [name], "focus": "all"} for i, name in enumerate(ALL_CARDS)]})


def perfect_worker(prompt: str) -> str:
    files = re.findall(r"=== FILE: (\S+) ===", prompt)
    findings = [
        {"file": file, "category": category, "severity": "high", "evidence": "quoted line"}
        for file, category in sorted(PLANTED)
        if file in files
    ]
    return json.dumps({"findings": findings})


def scripted(router=one_worker_per_card, worker=perfect_worker) -> tuple[ScriptedBackend, list[str]]:
    calls: list[str] = []

    def respond(system: str, prompt: str) -> str:
        if system == ROUTER_SYSTEM:
            calls.append("router")
            return router(prompt)
        if system == SYNTHESIS_SYSTEM:
            calls.append("synthesis")
            return "summary"
        calls.append("worker")
        return worker(prompt)

    return ScriptedBackend(respond), calls


def review(backend, **guard_overrides) -> tuple:
    break_router = guard_overrides.pop("break_router", False)
    record = run_review(SERVICES, backend, "supervisor", "worker", Guards(**guard_overrides), break_router=break_router)
    return record, score(record.findings, PLANTED)


def test_fan_out_cap_merges_eight_subtasks_into_three_workers_without_losing_coverage():
    backend, calls = scripted()
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert record.planned_subtasks == 8
    assert record.cap_applied and record.dispatched_workers == 3
    assert calls.count("worker") == 3
    assert result["recall"] == 1.0 and record.uncovered_files == []


def test_without_cap_the_router_plan_runs_as_is():
    backend, calls = scripted()
    record, result = review(backend, fan_out_cap=None, token_ceiling=None)
    assert record.dispatched_workers == 8 and calls.count("worker") == 8
    assert result["recall"] == 1.0


def test_killed_router_falls_back_to_a_deterministic_plan():
    backend, calls = scripted()
    record, result = review(backend, fan_out_cap=3, token_ceiling=None, break_router=True)
    assert "router" not in calls
    assert record.router_status == "failed_fallback"
    assert [len(worker["files"]) for worker in record.workers] == [3, 3, 2]
    assert result["recall"] == 1.0


def test_router_plan_naming_an_unknown_card_is_rejected_and_falls_back():
    backend, _ = scripted(router=lambda _: json.dumps({"subtasks": [{"id": "w1", "files": ["ghost.md"], "focus": "all"}]}))
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert record.router_status == "failed_fallback" and "ghost.md" in record.router_error
    assert result["recall"] == 1.0


def test_payload_breaking_the_contract_is_retried_once_then_not_merged():
    def broken_worker(prompt: str) -> str:
        return json.dumps({"findings": [{"file": "x", "category": "vibes", "severity": "high", "evidence": "e"}]})

    backend, calls = scripted(worker=broken_worker)
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert calls.count("worker") == 6
    assert all(worker["status"] == "invalid" and worker["attempts"] == 2 for worker in record.workers)
    assert record.findings == [] and record.uncovered_files == ALL_CARDS


def test_findings_outside_the_brief_are_dropped():
    def overreaching_worker(prompt: str) -> str:
        own = json.loads(perfect_worker(prompt))["findings"]
        foreign = {"file": "not-mine.md", "category": "no_owner", "severity": "high", "evidence": "e"}
        return json.dumps({"findings": own + [foreign]})

    backend, _ = scripted(worker=overreaching_worker)
    record, result = review(backend, fan_out_cap=3, token_ceiling=None)
    assert all(worker["out_of_brief"] == 1 for worker in record.workers)
    assert result["extra_findings"] == 0 and result["recall"] == 1.0


def test_token_ceiling_stops_later_waves_and_reports_what_was_not_reviewed():
    backend, calls = scripted()
    record, result = review(backend, fan_out_cap=None, token_ceiling=1, max_parallel=4)
    assert record.ceiling_hit
    assert "synthesis" not in calls
    assert record.dispatched_workers == 0 and len(record.uncovered_files) == 8
    assert record.summary.startswith("Token ceiling reached")
