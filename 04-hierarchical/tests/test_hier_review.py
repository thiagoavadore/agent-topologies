import json
import subprocess
from dataclasses import replace as dc_replace
from types import SimpleNamespace
from unittest.mock import patch

import jsonschema
import pytest
from hier_helpers import Script, asking_workers, changed_files, forward, payload, protective_top, stable_commit  # noqa: F401

import experiment
import hier
from hier import SubLeadResult, run_arm, run_pair
from hier_config import ARMS, CUTS
from hier_harm import coupling_ladder
from hier_prompts import LEAD_REASON_MAX, NOTES_ASKED, NOTES_MAX, SUB_LEAD_SCHEMA, WORKER_REASON_MAX, top_system, worker_schema, worker_system
from topologies.fixcheck import PRICING_TIMEOUT_LINE
from topologies.harbour import REGRESSION_NEEDS
from topologies.model import ScriptedBackend

PRICING, PAYMENTS = REGRESSION_NEEDS
PYTHON = CUTS["by-runtime"]["python"]


class ServedBackend(ScriptedBackend):
    """Scripted replies that claim to have been served by `served`."""

    def __init__(self, respond, served: str) -> None:
        super().__init__(respond)
        self.served = served

    def call(self, *, model, system, prompt):
        return dc_replace(super().call(model=model, system=system, prompt=prompt), served_models=(self.served,))


def pair(script, arms=ARMS, backend=None):
    backend = backend or ScriptedBackend(script)
    return run_pair(run=1, backend=backend, backend_name="scripted", worker_model="w", lead_model="l", arms=arms)


def by_arm(rows):
    return {row["arm"]: row for row in rows}


def python_script(top=protective_top, **sub_lead_requests) -> Script:
    requests = sub_lead_requests.get("python") or forward(("http.default_timeout", "15s", "charges take up to 12 s"))
    return Script(workers=asking_workers(), sub_leads={"python": requests}, top=top)


def write(path, rows):
    path.write_text("".join(json.dumps(item) + "\n" for item in rows))


def test_failed_platform_lead_is_not_credited_with_catching_the_threat():
    row = by_arm(pair(python_script(top="not json at all"), arms=["by-runtime"]))["by-runtime"]
    assert row["top"]["called"] and row["top"]["error"] and row["score"]["regressed"] == []
    assert row["ladder"][PRICING] == "top-failed"
    assert [item["tier"] for item in row["call_errors"]] == ["top"]
    answered = by_arm(pair(python_script(), arms=["by-runtime"]))["by-runtime"]
    assert answered["ladder"][PRICING] in {"caught-at-top", "reached-hub"}


def test_ladder_workers_and_failed_top_outcomes():
    threat = {"harms": [{"need": PRICING, "inside": False}]}
    case = dict(worker_requests=[threat], requests_up=[threat], tiered=True)
    assert coupling_ladder(**case, regressed=[], top_failed=True)[PRICING] == "top-failed"
    assert coupling_ladder(**case, regressed=[PRICING], regressed_by_workers=[PRICING])[PRICING] == "introduced-by-workers"
    assert coupling_ladder(**case, regressed=[PRICING], regressed_by_workers=[])[PRICING] == "reached-hub"


def test_need_already_broken_by_the_workers_is_not_blamed_on_the_platform_lead():
    def break_pricing(repo):
        path = repo / "pricing-engine/src/index.js"
        path.write_text(path.read_text().replace(PRICING_TIMEOUT_LINE, "const timeoutMs = 100;"))

    workers = {"pricing-engine": payload(changed_files("pricing-engine", break_pricing))}
    rows = by_arm(pair(Script(workers=workers, top=protective_top), arms=["flat", "by-team"]))
    for row in rows.values():
        assert row["regressed_by_workers"] == [PRICING] and PRICING in row["score"]["regressed"]
        assert row["ladder"][PRICING] == "introduced-by-workers"


def test_lead_reasons_may_be_longer_than_worker_reasons_and_both_limits_are_stated():
    long_reason = "x" * 300
    request = {"key": "http.default_timeout", "value": "5s", "reason": long_reason}
    jsonschema.validate({"requests": [request]}, SUB_LEAD_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"requests": [{**request, "reason": "x" * (LEAD_REASON_MAX + 1)}]}, SUB_LEAD_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"files": [], "hub_changes": [request]}, worker_schema(["bookings-api/service.yaml"]))
    assert f"at most {WORKER_REASON_MAX} characters" in worker_system()
    assert f"at most {LEAD_REASON_MAX} characters" in top_system(tiered=True, needs=False)
    script = python_script()
    pair(script, arms=["by-runtime"])
    assert all(f"at most {LEAD_REASON_MAX} characters" in system for _, system, _ in script.calls("sub-lead"))


def test_a_300_character_sub_lead_reason_no_longer_forces_the_fallback():
    script = python_script(python=forward(("http.default_timeout", "15s", "r" * 300)))
    row = by_arm(pair(script, arms=["by-runtime"]))["by-runtime"]
    assert row["sub_lead_fallbacks"] == 0 and row["requests_up"][0]["reason"] == "r" * 300


def test_harm_ignores_the_reason_text_even_when_it_asks_for_an_override():
    reason = "bookings-api only: give it its own override, do not change the default"
    row = by_arm(pair(python_script(python=forward(("http.default_timeout", "15s", reason))), arms=["by-runtime"]))["by-runtime"]
    [request] = row["requests_up"]
    assert request["reason"] == reason and [h["need"] for h in request["harms"]] == [PRICING] and request["changes_hub"] is True
    assert "override_for" not in json.dumps(row["requests_up"]) and "override_for" not in worker_system()


def test_changes_hub_flag_and_both_escalation_counts():
    repeat = forward(("http.default_timeout", "300ms", "keep the default"), ("http.default_timeout", "15s", "raise it"))
    row = by_arm(pair(python_script(python=repeat), arms=["by-runtime", "flat"]))
    assert [r["changes_hub"] for r in row["by-runtime"]["requests_up"]] == [False, True]
    assert row["by-runtime"]["escalations"] == 2 and row["by-runtime"]["hub_changing_escalations"] == 1
    assert row["flat"]["escalations"] == 3 and row["flat"]["hub_changing_escalations"] == 3


def test_code_commit_is_marked_dirty_when_tracked_code_differs(monkeypatch):
    monkeypatch.undo()  # drop the autouse pin so the real function runs
    def fake(clean):
        def run(command, **kwargs):
            out = "deadbeef\n" if command[1] == "rev-parse" else ("" if clean else " M 04-hierarchical/hier.py\n")
            return SimpleNamespace(returncode=0, stdout=out)

        return run

    with patch("hier.subprocess.run", fake(True)):
        assert hier.code_commit() == "deadbeef"
    with patch("hier.subprocess.run", fake(False)):
        assert hier.code_commit() == "deadbeef-dirty"
    status = subprocess.run(["git", "status", "--porcelain", "--", ":(exclude)04-hierarchical/results"], capture_output=True, text=True)
    assert status.returncode == 0  # the pathspec used by code_commit is valid git


def test_summary_refuses_dirty_or_mixed_commits(tmp_path):
    rows = pair(python_script())
    write(tmp_path / "clean.jsonl", rows)
    experiment.summarise(tmp_path / "clean.jsonl")
    write(tmp_path / "dirty.jsonl", [{**row, "code_commit": "abc123-dirty"} for row in rows])
    with pytest.raises(ValueError, match="uncommitted"):
        experiment.summarise(tmp_path / "dirty.jsonl")
    write(tmp_path / "mixed.jsonl", [rows[0], {**rows[1], "code_commit": "other"}])
    with pytest.raises(ValueError, match="mixes code commits"):
        experiment.summarise(tmp_path / "mixed.jsonl")


def test_reused_sub_leads_are_not_counted_twice_in_call_seconds():
    leads = [SubLeadResult(group="python", services=PYTHON, wall_seconds=5.0)]
    common = dict(backend=ScriptedBackend(Script()), worker_model="w", lead_model="l", hub_text="x", base_hub={}, workers=[], sub_leads=leads, regressed_by_workers=[])
    from hier_hub import parse_hub, scratch_copy
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as scratch:
        hub_text = (scratch_copy(Path(scratch) / "b") / "platform.yaml").read_text()
    common.update(hub_text=hub_text, base_hub=parse_hub(hub_text))
    assert run_arm("by-runtime", **common)["management_call_seconds"] == 5.0
    assert run_arm("by-runtime-registry", **common)["management_call_seconds"] == 0.0


def test_served_model_mismatch_and_exception_calls_flag_the_row(tmp_path):
    script = python_script()
    script.workers["notifications"] = RuntimeError("backend down")
    rows = pair(script, arms=["by-runtime"], backend=ServedBackend(script, served="claude-haiku-4-5"))
    [row] = rows
    assert {item["tier"] for item in row["model_mismatches"]} == {"worker", "sub-lead", "top"}
    assert any(item["tier"] == "worker" and item["who"] == "notifications" and "backend down" in item["error"] for item in row["call_errors"])
    quiet = by_arm(pair(python_script(), arms=["by-runtime"]))["by-runtime"]
    assert quiet["model_mismatches"] == [] and quiet["call_errors"] == []
    write(tmp_path / "runs.jsonl", [row])
    table = experiment.summarise(tmp_path / "runs.jsonl")
    assert "| by-runtime | 0 of 1 | 0 of 3 | 0 | 0 | 1 of 1 | 1 of 1 |" in table


def test_blank_platform_lead_value_is_unreadable_not_fatal(tmp_path):
    blank = json.dumps({"hub": [{"key": "http.default_timeout", "value": "   ", "reason": "oops"}], "overrides": []})
    rows = pair(python_script(top=blank), arms=["by-runtime"])
    [row] = rows
    assert row["unreadable_decisions"] == ["http.default_timeout"] and row["final_hub"]["http.default_timeout"] == "300ms"
    write(tmp_path / "runs.jsonl", rows)
    assert "| by-runtime | 0 of 1 | 0 of 3 | 0 | 1 |" in experiment.summarise(tmp_path / "runs.jsonl")


def test_unreadable_requests_up_are_counted(tmp_path):
    script = python_script(python=forward(("http.default_timeout", "fifteen seconds", "words")))
    rows = pair(script, arms=["by-runtime"])
    assert rows[0]["unreadable_requests_up"] == 1
    write(tmp_path / "runs.jsonl", rows)
    assert "| by-runtime | 0 of 1 | 0 of 3 | 1 |" in experiment.summarise(tmp_path / "runs.jsonl")


def test_worker_notes_cap_is_stated_and_a_long_note_validates():
    assert f"at most {NOTES_ASKED} characters" in worker_system() and NOTES_ASKED < NOTES_MAX
    schema = worker_schema(["pricing-engine/package.json"])
    jsonschema.validate({"files": [], "hub_changes": [], "notes": "n" * 900}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"files": [], "hub_changes": [], "notes": "n" * (NOTES_MAX + 1)}, schema)
