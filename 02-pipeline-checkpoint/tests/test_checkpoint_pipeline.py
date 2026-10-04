"""02 end to end against a scripted model on the real fixture files: every arm, fault and failure path, no network."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import ckpt_runs
from ckpt_faults import FAULT_KINDS, FAULT_TARGETS, Fault, inject, locator_matches, target_facts
from ckpt_pipeline import (
    ARMS, GATE_SYSTEM, STAGE_SYSTEMS, PipelineRecord, StageResult, load_world, run_checkpoint, run_pipeline, stage_system,
)
from ckpt_scoring import fault_outcome, owner_mismatch, score_plan
from topologies.harbour import FIXTURE, RISKS, RISKS_BY_ID, SERVICES
from topologies.model import ScriptedBackend

WORLD = load_world()
MODEL = "claude-sonnet-5-5"
SYSTEM_TO_STAGE = {stage_system(n): n for n in STAGE_SYSTEMS}


def owner_of(path: str) -> str:
    first = path.split("/")[0]
    return first if first in SERVICES else "platform"


def stage_one_facts(skip: set[str] = frozenset()) -> dict:
    """A faithful stage 1: one plain fact per file, plus one `RISK:<id>` fact per catalogue risk at its own locator."""
    facts = [{"service": owner_of(path), "file": path, "locator": "overview", "fact": "plain"} for path in WORLD if path not in skip]
    facts += [{"service": r.service, "file": r.file, "locator": r.locator, "fact": f"RISK:{r.id}"} for r in RISKS if r.file not in skip]
    return {"facts": facts}


def risks_of(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        if "category" in item:  # already a classified risk (stages 3 and 4)
            out.append(item)
        elif str(item.get("fact", "")).startswith("RISK:"):
            category = item["fact"][5:].split(".")[1]
            out.append({"service": item["service"], "category": category, "file": item["file"], "locator": item["locator"], "fact": item["fact"]})
    return out


class Model:
    """Scripted stages and an oracle gate: fails stage 1 on a misattributed fact or a missing risk fact, passes the rest."""

    def __init__(self, *, skip_files_first_call: set[str] = frozenset(), skip_files_always: set[str] = frozenset(), gate_verdict: str | None = None):
        self.calls: list[tuple[str, str]] = []
        self.skip_first, self.skip_always, self.gate_verdict = skip_files_first_call, skip_files_always, gate_verdict
        self.stage_one_calls = 0

    def backend(self) -> ScriptedBackend:
        return ScriptedBackend(self.respond)

    def respond(self, system: str, prompt: str) -> str:
        if system == GATE_SYSTEM:
            self.calls.append(("gate", prompt))
            return json.dumps(self.gate(prompt))
        stage = SYSTEM_TO_STAGE[system]
        self.calls.append((f"stage{stage}", prompt))
        if stage == 1:
            self.stage_one_calls += 1
            skip = set(self.skip_always) | (set(self.skip_first) if self.stage_one_calls == 1 else set())
            return json.dumps(stage_one_facts(skip))
        data = json.loads(prompt)
        key = {2: "facts", 3: "risks", 4: "ranked"}[stage]
        found = risks_of(data[key])
        with_risk = {item["service"] for item in found}
        quiet = [s for s in SERVICES if s not in with_risk]
        found = [{k: v for k, v in item.items() if k not in ("fact", "priority", "reason")} for item in found] if stage > 2 else found
        if stage == 2:
            return json.dumps({"risks": found, "no_risk_services": quiet})
        if stage == 3:
            return json.dumps({"ranked": [{**item, "priority": n + 1, "reason": "r"} for n, item in enumerate(found)], "no_risk_services": quiet})
        return json.dumps({"plan": [{**item, "priority": n + 1, "action": "a"} for n, item in enumerate(found)], "no_risk_services": quiet})

    def gate(self, prompt: str) -> dict:
        if self.gate_verdict:
            return {"verdict": self.gate_verdict, "reason": "scripted"}
        if "Stage 1 (extract)" in prompt:
            facts = json.loads(prompt.split("=== STAGE OUTPUT ===")[1])["facts"]
            if any(f["service"] != owner_of(f["file"]) and f["service"] != "platform" for f in facts):
                return {"verdict": "fail", "reason": "a fact is attributed to a service that does not own its file"}
            if sum(f["fact"].startswith("RISK:") for f in facts) < len(RISKS):
                return {"verdict": "fail", "reason": "a risk fact is missing"}
        return {"verdict": "pass", "reason": "faithful"}


def run(arm: str, fault: Fault | None, model: Model | None = None) -> tuple[PipelineRecord, Model]:
    model = model or Model()
    return run_pipeline(WORLD, model.backend(), arm=arm, model=MODEL, fault=fault), model


def fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in FIXTURE.rglob("*") if p.is_file()):
        digest.update(path.read_bytes())
    return digest.hexdigest()


# --- clean runs

@pytest.mark.parametrize("arm", list(ARMS))
def test_clean_run_completes_with_full_grounded_recall(arm):
    record, model = run(arm, None)
    assert record.status == "completed" and record.caught_at_stage is None
    assert len(record.checkpoints) == len(ARMS[arm])
    assert score_plan(record.plan)["grounded"] == len(RISKS)
    assert [kind for kind, _ in model.calls].count("gate") == len(ARMS[arm])
    assert fault_outcome(record, None) is None


def test_gate_tokens_only_with_checkpoints():
    none, _ = run("none", None)
    every, _ = run("every-handoff", None)
    assert none.gate_tokens == 0 and every.gate_tokens > 0
    assert every.total_tokens == every.stage_tokens + every.gate_tokens


def test_run_does_not_write_to_the_fixture():
    before = fingerprint()
    run("every-handoff", Fault("dropped", FAULT_TARGETS[0]))
    assert fingerprint() == before


# --- faults, per arm

@pytest.mark.parametrize("risk_id", FAULT_TARGETS)
@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_every_fault_hits_its_target_exactly(risk_id, kind):
    risk = RISKS_BY_ID[risk_id]
    output, injection = inject(stage_one_facts(), Fault(kind, risk_id))
    assert injection.matched_by == "locator" and injection.facts_changed == 1
    if kind == "dropped":
        assert not any(f["fact"] == f"RISK:{risk_id}" for f in output["facts"])
    else:
        moved = [f for f in output["facts"] if f["fact"] == f"RISK:{risk_id}"]
        assert [f["service"] for f in moved] == [injection.moved_to] and injection.moved_to != risk.service


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_none_arm_lets_fault_reach_production(kind):
    fault = Fault(kind, "bookings-api.missing_timeout")
    record, _ = run("none", fault)
    assert fault_outcome(record, fault) == "reached-production"
    assert record.injection and record.injection.facts_changed == 1
    assert "bookings-api.missing_timeout" in score_plan(record.plan)["missed"]


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_every_handoff_catches_at_stage_one_and_names_the_owner(kind):
    fault = Fault(kind, "payments-gateway.hardcoded_secret")
    record, model = run("every-handoff", fault)
    assert record.status == "stopped_at_checkpoint" and record.plan is None
    assert fault_outcome(record, fault) == "caught-stage-1"
    assert owner_mismatch(record, fault) is False
    assert [kind for kind, _ in model.calls] == ["stage1", "gate"]
    assert record.checkpoints[0].reason


def test_end_only_sees_a_consistent_plan_and_misses_the_fault():
    fault = Fault("wrong-service", "admin-console.public_exposure")
    record, _ = run("end-only", fault)
    assert record.status == "completed" and len(record.checkpoints) == 1
    assert fault_outcome(record, fault) == "reached-production"


def test_end_only_catch_is_blamed_on_stage_four():
    fault = Fault("dropped", "admin-console.public_exposure")
    model = Model(gate_verdict="fail")
    record, _ = run("end-only", fault, model)
    assert fault_outcome(record, fault) == "caught-stage-4"
    assert owner_mismatch(record, fault) is True
    assert record.stage_tokens > 0 and len(record.stages) == 4


def test_clean_run_stopped_by_a_checkpoint_is_a_false_rejection():
    record, _ = run("every-handoff", None, Model(gate_verdict="fail"))
    assert ckpt_runs.false_rejection(record, None) and record.caught_at_stage == 1


def test_contract_check_fails_without_a_gate_call_when_a_service_is_missing():
    output = stage_one_facts()
    output["facts"] = [f for f in output["facts"] if f["service"] != "notifications"]
    calls = []
    backend = ScriptedBackend(lambda system, prompt: calls.append(1) or "{}")
    result = run_checkpoint(backend, MODEL, 1, "input", output)
    assert (result.check, result.verdict, result.tokens) == ("contract", "fail", 0)
    assert "notifications" in result.reason and not calls


def test_not_injected_is_impossible():
    with pytest.raises(AssertionError):
        inject({"facts": []}, Fault("dropped", FAULT_TARGETS[0]))
    record = PipelineRecord("none", "completed", None, None, [StageResult(1, "extract", "ok")], [], [], [], 0, 0, 0.0)
    with pytest.raises(AssertionError):
        fault_outcome(record, Fault("dropped", FAULT_TARGETS[0]))


def test_absorbed_when_the_plan_still_has_the_grounded_finding():
    fault = Fault("dropped", "fleet-telemetry.single_point_of_failure")
    risk = RISKS_BY_ID[fault.risk_id]
    item = {"service": risk.service, "category": risk.category, "file": risk.file, "locator": risk.locator}
    injected = inject(stage_one_facts(), fault)[1]
    record = PipelineRecord("none", "completed", None, injected, [StageResult(1, "extract", "ok")], [], [item], [], 0, 0, 0.0)
    assert fault_outcome(record, fault) == "absorbed"


# --- stage-1 coverage and failure paths

def test_stage_one_retries_once_when_a_file_is_uncovered():
    model = Model(skip_files_first_call={"bookings-api/bookings_api.py"})
    record, _ = run("none", Fault("dropped", "bookings-api.missing_timeout"), model)
    assert model.stage_one_calls == 2 and record.status == "completed"
    assert "left these files" in next(p for kind, p in model.calls if kind == "stage1" and "left these" in p)


def test_uncovered_file_after_retry_is_no_verdict_not_an_assertion():
    fault = Fault("dropped", "bookings-api.missing_timeout")
    model = Model(skip_files_always={"bookings-api/bookings_api.py"})
    record, _ = run("every-handoff", fault, model)
    assert record.status == "stage_failed" and record.injection is None
    assert fault_outcome(record, fault) == "no-verdict"


def test_stage_two_prompt_names_the_services_and_excludes_platform():
    system = stage_system(2)
    assert all(service in system for service in SERVICES)
    assert '"platform" is not a service' in system and "{" not in system.split("Reply with JSON")[0]


# --- scoring

@pytest.mark.parametrize(
    ("cited", "expected", "result"),
    [
        ("charge(): requests.post(url)", "charge(): requests.post(timeout=)", True),
        ("other(): requests.post(url)", "charge(): requests.post(timeout=)", False),
        ("overrides", "overrides.http.default_rate_limit", True),
        ("overrides.http.default_rate_limit", "overrides.http.default_rate_limit", True),
        ("data.store", "overrides.http.default_rate_limit", False),
        ("instances", "instances", True),
        ("owner", "instances", False),
        ("lodash", "every requirement", True),
        ("instances, zones, notes", "instances", True),
        ("data, backup", "backup", True),
        ("data, backup", "instances", False),
        ("", "instances", False),
        ("instances", "charge(): requests.post(timeout=)", False),
    ],
)
def test_locator_matching(cited, expected, result):
    assert locator_matches(cited, expected) is result


def test_recall_needs_id_file_and_locator():
    risk = RISKS_BY_ID["bookings-api.missing_timeout"]
    good = {"service": risk.service, "category": risk.category, "file": risk.file, "locator": risk.locator}
    assert score_plan([good])["grounded"] == 1
    for broken in ({"service": "notifications"}, {"file": "bookings-api/service.yaml"}, {"locator": "instances"}, {"category": "no_owner"}):
        assert score_plan([{**good, **broken}])["grounded"] == 0


# --- experiment

def test_fault_schedule_is_half_faulted_and_rotates_kind_and_target():
    faults = [ckpt_runs.fault_for_run(i) for i in range(10)]
    assert [f is not None for f in faults] == [True, False] * 5
    real = [f for f in faults if f]
    assert [f.kind for f in real] == ["wrong-service", "dropped", "wrong-service", "dropped", "wrong-service"]
    assert {f.risk_id for f in real} == set(FAULT_TARGETS)


def test_arm_order_rotates_per_run():
    arms = list(ARMS)
    firsts = [ckpt_runs.arms_for_run(arms, i)[0] for i in range(6)]
    assert firsts == arms * 2 and all(sorted(ckpt_runs.arms_for_run(arms, i)) == sorted(arms) for i in range(6))


def test_experiment_writes_rows_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(ckpt_runs, "backend_from_name", lambda name: Model().backend())
    out = tmp_path / "runs.jsonl"
    ckpt_runs.run_arms(4, list(ARMS), out, "claude-cli", MODEL, None)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 12
    assert {row["run"]: row["fault"] is None for row in rows} == {1: False, 2: True, 3: False, 4: True}
    same_fault = {(row["run"], json.dumps(row["fault"])) for row in rows}
    assert len(same_fault) == 4
    assert all(row["fault_outcome"] != "not-injected" for row in rows)
    for row in rows:
        assert {"stages", "checkpoints", "plan", "served_models", "total_tokens", "gate_tokens", "wall_seconds"} <= set(row)
    summary = ckpt_runs.summarise(out)
    assert "| every-handoff | claude-sonnet-5-5, claude-cli | 4 (2 / 2)" in summary
    assert "caught-stage-1" in summary and "runs that reached stage 4" in summary


def test_summary_refuses_mixed_models_or_backends(tmp_path, monkeypatch):
    monkeypatch.setattr(ckpt_runs, "backend_from_name", lambda name: Model().backend())
    out = tmp_path / "runs.jsonl"
    ckpt_runs.run_arms(1, ["none"], out, "claude-cli", MODEL, None)
    ckpt_runs.run_arms(1, ["none"], out, "anthropic-sdk", MODEL, None)
    with pytest.raises(ValueError, match="mixes models or backends"):
        ckpt_runs.summarise(out)


def test_token_ceiling_skips_later_stages_by_name():
    record = run_pipeline(WORLD, Model().backend(), arm="every-handoff", model=MODEL, token_ceiling=1)
    assert record.status == "over_budget" and [s.stage for s in record.stages] == [1]
    assert "stage 2 (classify)" in ckpt_runs.describe_skipped(record.skipped)


# --- pre-registration guard

def load_cli(name: str):
    spec = importlib.util.spec_from_file_location(f"ckpt02_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guard_reads_draft_marker(tmp_path):
    draft, approved = tmp_path / "d.md", tmp_path / "a.md"
    draft.write_text("> **DRAFT, awaiting approval.**")
    approved.write_text("Approved 2026-10-05.")
    with pytest.raises(SystemExit, match="DRAFT"):
        ckpt_runs.assert_preregistered(draft)
    ckpt_runs.assert_preregistered(approved)


def test_experiment_cli_refuses_to_benchmark_while_predictions_are_draft(monkeypatch, tmp_path):
    monkeypatch.setattr(ckpt_runs, "PREDICTIONS", tmp_path / "PREDICTIONS.md")
    (tmp_path / "PREDICTIONS.md").write_text("DRAFT")
    cli = load_cli("experiment")
    monkeypatch.setattr(cli, "assert_preregistered", lambda: ckpt_runs.assert_preregistered(tmp_path / "PREDICTIONS.md"))
    monkeypatch.setattr(cli, "run_arms", lambda *a, **k: pytest.fail("benchmark started"))
    monkeypatch.setattr(sys, "argv", ["experiment.py", "--out", str(tmp_path / "runs.jsonl")])
    with pytest.raises(SystemExit):
        cli.main()
    monkeypatch.setattr(sys, "argv", ["experiment.py", "--summarise-only", "--out", str(tmp_path / "runs.jsonl")])
    cli.main()
    assert (tmp_path / "summary.md").exists()


def test_run_cli_has_no_guard_and_loads():
    assert hasattr(load_cli("run"), "main")
