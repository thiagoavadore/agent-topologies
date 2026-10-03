"""End-to-end runs against a scripted model on the real fixtures: every arm, fault and failure path, no network."""

import filecmp
import json
import sys
from pathlib import Path

import pytest

# Topology folders are script folders, and other folders use the same module names (`experiment`, `run`).
# Pytest collects them in one process, so drop cached copies from other folders before importing ours.
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
for name in ("experiment", "run", "pipeline", "injection", "grading"):
    module = sys.modules.get(name)
    if module is not None and not str(getattr(module, "__file__", "")).startswith(str(HERE)):
        del sys.modules[name]

from experiment import arms_for_run, fault_for_run, load_rows, run_row, summarise
from grading import fault_outcome, false_rejection, load_planted, score_plan
from injection import FAULT_TARGETS, Fault, inject
from pipeline import GATE_SYSTEM, STAGE_SYSTEMS, load_cards, run_pipeline, stage_system
from topologies.model import ScriptedBackend

CARDS = load_cards(HERE / "fixtures" / "services")
CARD_NAMES = list(CARDS)
PLANTED = load_planted()
SYSTEM_TO_STAGE = {stage_system(n): n for n in STAGE_SYSTEMS}
# Phrase a faithful stage 1 would quote for each fault target; other facts quote "line".
FILLER = "runtime"  # a benign fact every card has, so a dropped risk alone never leaves a card unaccounted for
EVIDENCE = {(card, category): phrase for (card, category), phrase in FAULT_TARGETS.items()}


def pairs(items: list[dict]) -> set[tuple[str, str]]:
    """(card, category) of each risk item; stage-1 facts carry the category as their text."""
    return {(item["card"], item.get("category", item.get("fact"))) for item in items if item.get("fact") != FILLER}


def faithful_stage(stage: int, prompt: str) -> dict:
    """A perfect model: stage 1 reports the answer key (a fact's text is its category), later stages relabel."""
    if stage == 1:
        facts = [{"card": c, "fact": category, "evidence": EVIDENCE.get((c, category), "line")} for c, category in sorted(PLANTED)]
        return {"facts": facts + [{"card": c, "fact": FILLER, "evidence": "line"} for c in CARD_NAMES]}
    previous = json.loads(prompt)
    if stage == 2:
        risks = [{"card": f["card"], "category": f["fact"], "fact": f["fact"]} for f in previous["facts"] if f["fact"] != FILLER]
        return {"risks": risks, "no_risk_cards": [c for c in CARD_NAMES if c not in {r["card"] for r in risks}]}
    if stage == 3:
        ranked = [{"card": r["card"], "category": r["category"], "priority": i + 1, "reason": "r"} for i, r in enumerate(previous["risks"])]
        return {"ranked": ranked, "no_risk_cards": previous["no_risk_cards"]}
    plan = [{"card": r["card"], "category": r["category"], "priority": r["priority"], "action": "fix"} for r in previous["ranked"]]
    return {"plan": plan, "no_risk_cards": previous["no_risk_cards"]}


def faithful_gate(prompt: str) -> dict:
    """A perfect gate: stage 1 is judged against the answer key, later stages against their own input."""
    stage = int(prompt.split("Stage ")[1][0])
    output = json.loads(prompt.split("=== STAGE OUTPUT ===\n")[1])
    if stage == 1:
        ok = {(f["card"], f["fact"]) for f in output["facts"] if f["fact"] != FILLER} == PLANTED
    else:
        previous = json.loads(prompt.split("=== STAGE INPUT ===\n")[1].split("\n\n=== STAGE OUTPUT")[0])
        items_in = next(v for k, v in previous.items() if k != "no_risk_cards")
        items_out = next(v for k, v in output.items() if k != "no_risk_cards")
        ok = pairs(items_in) == pairs(items_out)
    return {"verdict": "pass" if ok else "fail", "reason": "matches" if ok else "output differs from input"}


def scripted(stage_override=None, gate=faithful_gate):
    """Backend routing by system prompt; `stage_override(stage, prompt)` may return a payload (or None to stay faithful)."""
    calls: list[str] = []

    def respond(system: str, prompt: str) -> str:
        if system == GATE_SYSTEM:
            calls.append("gate")
            return json.dumps(gate(prompt))
        stage = SYSTEM_TO_STAGE[system]
        calls.append(f"stage{stage}")
        payload = stage_override(stage, prompt) if stage_override else None
        return json.dumps(payload if payload is not None else faithful_stage(stage, prompt))

    return ScriptedBackend(respond), calls


def run(arm: str, fault: Fault | None = None, backend=None, **kwargs):
    backend = backend or scripted()[0]
    return run_pipeline(CARDS, backend, arm=arm, model="m", fault=fault, **kwargs)


WRONG_CARD = Fault("wrong-card", "pricing-engine.md", "unpinned_dependency")
DROPPED = Fault("dropped", "pricing-engine.md", "unpinned_dependency")


def test_clean_run_passes_every_checkpoint_and_finds_everything():
    record = run("every-handoff")
    assert record.status == "completed" and record.caught_at_stage is None
    assert [c.after_stage for c in record.checkpoints] == [1, 2, 3, 4]
    assert score_plan(record.plan, PLANTED)["recall"] == 1.0
    assert not false_rejection(record, None)


@pytest.mark.parametrize("fault", [WRONG_CARD, DROPPED])
def test_every_handoff_catches_a_stage_1_fault_at_stage_1_and_stops(fault):
    backend, calls = scripted()
    record = run("every-handoff", fault, backend)
    assert record.status == "stopped_at_checkpoint" and record.plan is None
    assert fault_outcome(record, fault) == "caught-stage-1"
    assert calls == ["stage1", "gate"]  # no stage 2 after the catch
    assert record.checkpoints[0].verdict == "fail" and record.checkpoints[0].reason == "output differs from input"


@pytest.mark.parametrize("arm", ["none", "end-only"])
def test_a_fault_the_late_gate_cannot_see_reaches_production(arm):
    record = run(arm, WRONG_CARD)
    assert record.status == "completed"
    assert fault_outcome(record, WRONG_CARD) == "reached-production"
    assert score_plan(record.plan, PLANTED)["missed"] == ["pricing-engine.md:unpinned_dependency"]


def test_end_only_runs_one_gate_and_none_runs_zero():
    assert [c.after_stage for c in run("end-only").checkpoints] == [4]
    assert run("none").checkpoints == []
    assert run("none").gate_tokens == 0


def test_a_fault_a_stage_recovers_from_is_absorbed():
    def restore(stage: int, prompt: str):
        if stage == 4:  # a later stage that happens to re-add the dropped risk
            payload = faithful_stage(4, prompt)
            payload["plan"].append({"card": "pricing-engine.md", "category": "unpinned_dependency", "priority": 9, "action": "pin"})
            return payload

    record = run("none", DROPPED, scripted(restore)[0])
    assert fault_outcome(record, DROPPED) == "absorbed"


def test_fault_without_a_matching_fact_is_reported_not_injected():
    def vague(stage: int, prompt: str):
        if stage == 1:
            return {"facts": [{"card": c, "fact": category, "evidence": "line"} for c, category in sorted(PLANTED)]}

    record = run("none", Fault("dropped", "pricing-engine.md", "unpinned_dependency"), scripted(vague)[0])
    assert record.injection is not None and not record.injection.applied
    assert fault_outcome(record, DROPPED) == "not-injected"


def test_dropping_a_cards_only_fact_fails_the_contract_check_before_any_gate_call():
    fault = Fault("dropped", "payments-gateway.md", "hardcoded_secret")

    def bare_facts(stage: int, prompt: str):  # one fact per card, no filler
        if stage == 1:
            return {"facts": [{"card": c, "fact": category, "evidence": EVIDENCE.get((c, category), "line")} for c, category in sorted(PLANTED) if c != "bookings-api.md" or category == "missing_timeout"]}

    backend, calls = scripted(bare_facts)
    record = run("every-handoff", fault, backend)
    assert record.checkpoints[0].check == "contract"
    assert "payments-gateway.md" in record.checkpoints[0].reason
    assert fault_outcome(record, fault) == "caught-stage-1"
    assert calls == ["stage1"] and record.gate_tokens == 0


def test_false_rejection_is_a_clean_run_stopped_by_a_gate():
    record = run("every-handoff", backend=scripted(gate=lambda _: {"verdict": "fail", "reason": "nitpick"})[0])
    assert false_rejection(record, None) and record.caught_at_stage == 1


def test_a_gate_that_cannot_run_is_an_error_not_a_catch():
    def broken(system: str, prompt: str) -> str:
        if system == GATE_SYSTEM:
            raise RuntimeError("claude -p failed")
        return json.dumps(faithful_stage(SYSTEM_TO_STAGE[system], prompt))

    record = run("every-handoff", WRONG_CARD, ScriptedBackend(broken))
    assert record.status == "checkpoint_error" and record.caught_at_stage is None
    assert fault_outcome(record, WRONG_CARD) == "no-verdict"


def test_a_stage_that_stays_invalid_after_retry_stops_the_run():
    record = run("none", backend=scripted(lambda stage, prompt: {"nonsense": 1} if stage == 3 else None)[0])
    assert record.status == "stage_failed"
    assert [s.status for s in record.stages] == ["ok", "ok", "invalid"] and record.stages[2].attempts == 2


def test_token_ceiling_skips_later_stages_by_name():
    record = run("none", token_ceiling=1)
    assert record.status == "over_budget" and len(record.stages) == 1
    assert [item.what for item in record.skipped] == ["stage 2 (classify)", "stage 3 (prioritise)", "stage 4 (plan)"]


def test_injection_moves_facts_to_the_next_card_and_drops_cleanly():
    output = faithful_stage(1, "")
    moved, injection = inject(output, WRONG_CARD, CARD_NAMES)
    assert injection.moved_to == CARD_NAMES[(CARD_NAMES.index("pricing-engine.md") + 1) % len(CARD_NAMES)]
    assert ("pricing-engine.md", "unpinned_dependency") not in {(f["card"], f["fact"]) for f in moved["facts"]}
    assert output == faithful_stage(1, "")  # the input is not mutated
    dropped, injection = inject(output, DROPPED, CARD_NAMES)
    assert injection.facts_changed == 1 and len(dropped["facts"]) == len(output["facts"]) - 1


def test_fault_schedule_is_three_faulted_of_five_and_rotates_kind_and_arm_order():
    faults = [fault_for_run(i) for i in range(5)]
    assert [f is not None for f in faults] == [True, False, True, False, True]
    assert [f.kind for f in faults if f] == ["wrong-card", "dropped", "wrong-card"]
    assert len({(f.card, f.category) for f in faults if f}) == 3
    arms = ["none", "end-only", "every-handoff"]
    assert [arms_for_run(arms, i)[0] for i in range(3)] == arms


def test_summary_covers_every_arm_and_refuses_mixed_models(tmp_path):
    path = tmp_path / "runs.jsonl"
    rows = []
    for arm in ("none", "end-only", "every-handoff"):
        fault = fault_for_run(0)
        rows.append(run_row(run(arm, fault), fault, PLANTED, run=1, backend="scripted", model="m"))
        rows.append(run_row(run(arm), None, PLANTED, run=2, backend="scripted", model="m"))
    path.write_text("\n".join(json.dumps(row) for row in rows))
    summary = summarise(path)
    assert "caught-stage-1 1" in summary and "reached-production 1" in summary
    assert "0 of 1" in summary  # no false rejections
    rows[0]["model"] = "other"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="mixes models"):
        load_rows(path)


def test_fixtures_are_unchanged_copies_of_01():
    source = HERE.parent / "01-orchestrator-worker" / "fixtures"
    comparison = filecmp.dircmp(source, HERE / "fixtures")
    assert not comparison.left_only and not comparison.right_only and not comparison.diff_files
    services = filecmp.dircmp(source / "services", HERE / "fixtures" / "services")
    assert not services.left_only and not services.right_only and not services.diff_files
