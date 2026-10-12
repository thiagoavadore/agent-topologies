import json

import pytest
from hier_helpers import Script, asking_workers, forward, protective_top, stable_commit  # noqa: F401
from hier_snapshot import digests

import experiment
import hier_prompts
import run as run_module
from hier import rotated, run_pair
from hier_config import ARMS, CUTS
from hier_harm import LADDER
from hier_prompts import RULE_8, lead_rules_block, needs_block, worker_system
from hier_replay import load_replay
from topologies.model import ScriptedBackend

PRICING = "pricing-engine.demand_model_timeout"
RULE_8_TEXT = "pricing-engine/src/index.js"

# Hashes of the prompts the frozen benchmark (0db1298) rendered, for fixed inputs: see hier_snapshot.render.
FROZEN_DIGESTS = {
    "worker_system": "108aad7571f150d5",
    "worker_prompt": "3c12ef6b36f31bc7",
    "sub_lead_prompt": "b3103af537844c87",
    "sub_lead_system_by-team_rentals-and-money": "c24d34afeb6383b4",
    "sub_lead_system_by-team_fleet": "12f2d0e1aebd7538",
    "sub_lead_system_by-team_customer-and-back-office": "4a1a57750af0b4c7",
    "sub_lead_system_by-runtime_python": "6021a206387883a0",
    "sub_lead_system_by-runtime_jvm-and-go": "ddac163919a3282b",
    "sub_lead_system_by-runtime_node-ruby-rust": "bfc26b56ec60f654",
    "top_prompt_flat": "2f9616311873a5bc",
    "top_prompt_by-team_needs_False": "9b212fd96118797d",
    "top_prompt_by-team_needs_True": "7ca8cf6bd80e2cc3",
    "top_prompt_by-runtime_needs_False": "37cd54480a45c3ba",
    "top_prompt_by-runtime_needs_True": "613713f8a23359d6",
    "top_system_tiered_False_needs_False": "88874620d13360f4",
    "top_system_tiered_False_needs_True": "16e93c1a673bc46d",
    "top_system_tiered_True_needs_False": "e566459f45b81540",
    "top_system_tiered_True_needs_True": "181a873bdcd6dd10",
}


def rule8_top(prompt: str) -> str:
    """Behaves like the protective top when its prompt carries rule 8 (the pricing-engine line) or the needs, else applies the highest timeout."""
    return protective_top(prompt + ("\nService needs (" if RULE_8_TEXT in prompt else ""))


def script(top=rule8_top) -> Script:
    return Script(workers=asking_workers(), sub_leads={"python": forward(("http.default_timeout", "15s", "charges take up to 12 s")), "customer-and-back-office": forward(("http.default_timeout", "5s", "the SMTP relay is slow"))}, top=top)


def pair(scr, run=1, **kwargs):
    return run_pair(run=run, backend=ScriptedBackend(scr), backend_name="scripted", worker_model="w", lead_model="l", **kwargs)


def by_arm(rows):
    return {row["arm"]: row for row in rows}


def write(path, rows):
    path.write_text("".join(json.dumps(item) + "\n" for item in rows))


def test_blind_lead_contract_drops_rule_8_only():
    full, blind = lead_rules_block("full").split("\n"), lead_rules_block("blind").split("\n")
    [dropped] = [line for line in full if line not in blind]
    assert dropped.startswith(RULE_8) and RULE_8_TEXT in dropped
    assert [line for line in full if line != dropped] == blind
    assert RULE_8_TEXT not in "\n".join(blind)
    with pytest.raises(ValueError, match="unknown lead contract"):
        lead_rules_block("other")


def test_blind_contract_refuses_a_contract_without_exactly_one_rule_8(monkeypatch):
    for text in ("1. **Files.** x\n", f"{RULE_8} a\n{RULE_8} b\n"):
        monkeypatch.setattr(hier_prompts, "worker_contract", lambda text=text: text)
        with pytest.raises(ValueError, match="exactly one line"):
            lead_rules_block("blind")


def test_full_prompts_unchanged():
    assert digests() == FROZEN_DIGESTS
    assert len([name for name in FROZEN_DIGESTS if name.startswith("sub_lead_system_")]) == 6
    assert digests(contract="full") == FROZEN_DIGESTS
    changed = {name for name, value in digests(contract="blind").items() if value != FROZEN_DIGESTS[name]}
    assert changed == {name for name in FROZEN_DIGESTS if "sub_lead_system" in name or name.startswith("top_prompt")}


def test_worker_prompt_same_under_both_contracts():
    seen = {}
    for contract in ("full", "blind"):
        scr = script()
        pair(scr, lead_contract=contract, arms=["flat"])
        seen[contract] = sorted((key, system, prompt) for key, system, prompt in scr.calls("worker"))
    assert seen["full"] == seen["blind"] and len(seen["full"]) == 8
    assert RULE_8_TEXT in worker_system()


def test_blind_tiered_leads_never_name_pricing_outside_subtree():
    scr = script()
    pair(scr, lead_contract="blind", arms=["by-team", "by-runtime"])
    prompts = [prompt for _, _, prompt in scr.calls("top")]
    assert len(prompts) == 2
    for prompt in prompts:
        assert RULE_8_TEXT not in prompt and "Service needs (" not in prompt
        named = [line for line in prompt.splitlines() if "pricing-engine" in line]
        assert named and all(line.startswith("- ") and ": " in line and "asks" not in line for line in named), named  # only the Groups lines
    assert all(RULE_8_TEXT not in system for _, system, _ in scr.calls("sub-lead"))


def test_blind_keeps_needs_in_flat_and_registry():
    seen = {}
    for arm in ARMS:
        scr = script()
        pair(scr, lead_contract="blind", arms=[arm])
        [(_, _, prompt)] = scr.calls("top")
        seen[arm] = needs_block() in prompt
    assert seen == {"flat": True, "by-team": False, "by-runtime": False, "by-runtime-registry": True}


def published(tmp_path, runs=(1, 2, 3)):
    rows = [row for run in runs for row in pair(script(), run=run)]
    write(tmp_path / "published.jsonl", rows)
    return tmp_path / "published.jsonl", rows


def test_replay_workers(tmp_path):
    path, rows = published(tmp_path)
    replay = load_replay(path)
    assert replay.code_commit == "abc123" and replay.worker_model == "w" and sorted(replay.workers_by_run) == [1, 2, 3]
    scr = script()
    blind = pair(scr, run=2, lead_contract="blind", workers=replay.workers_for(2), workers_replayed_from=replay.source())
    assert scr.calls("worker") == []
    assert all(row["workers"] == next(r for r in rows if r["run"] == 2)["workers"] for row in blind)
    assert all(row["workers_replayed_from"] == {"path": str(path), "code_commit": "abc123"} for row in blind)
    assert all(row["regressed_by_workers"] == [] and row["wall_seconds_workers"] < 1 for row in blind)
    with pytest.raises(ValueError, match="no run index 9"):
        replay.workers_for(9)
    first = json.loads(path.read_text().splitlines()[0])
    write(tmp_path / "commits.jsonl", [first, {**first, "code_commit": "other"}])
    with pytest.raises(ValueError, match="mixes code commits"):
        load_replay(tmp_path / "commits.jsonl")
    write(tmp_path / "models.jsonl", [first, {**first, "worker_model": "other"}])
    with pytest.raises(ValueError, match="worker models"):
        load_replay(tmp_path / "models.jsonl")


def test_replay_run_pairs_refuses_a_missing_index_before_any_call(tmp_path, monkeypatch):
    path, _ = published(tmp_path, runs=(1, 2))
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: pytest.fail("a backend was built"))
    with pytest.raises(SystemExit, match="no run index"):
        experiment.run_pairs(3, list(ARMS), tmp_path / "out.jsonl", "scripted", "w", "l", replay=load_replay(path))


def test_blind_rows_tagged_and_separated(tmp_path, monkeypatch):
    path, _ = published(tmp_path, runs=(1,))
    replay = load_replay(path)
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: ScriptedBackend(script()))
    out = tmp_path / "blind.jsonl"
    experiment.run_pairs(1, list(ARMS), out, "scripted", "w", "l", lead_contract="blind", replay=replay)
    rows = experiment.load_rows(out)
    assert {row["lead_contract"] for row in rows} == {"blind"} and all(row["workers_replayed_from"]["path"] == str(path) for row in rows)
    table = experiment.summarise(out)
    assert "blind to CONTRACT rule 8" in table and "replayed from" in table and all(outcome in table for outcome in LADDER)
    assert experiment.pair_tokens(rows) == sum(r["tokens_by_tier"]["top"] for r in rows) + sum(r["tokens_by_tier"]["sub_leads"] for r in rows if not r["sub_leads_shared_with"])

    full_rows = experiment.load_rows(path)
    write(tmp_path / "mixed.jsonl", [full_rows[0], rows[1]])
    with pytest.raises(ValueError, match="mixes lead contracts"):
        experiment.summarise(tmp_path / "mixed.jsonl")
    assert "mixes" not in experiment.summarise(path)[:200]  # a file of full-contract rows (no replay field needed) still summarises

    monkeypatch.setattr("sys.argv", ["experiment.py", "--lead-contract", "blind", "--out", str(experiment.RESULTS / "runs.jsonl")])
    monkeypatch.setattr(experiment, "refuse_unregistered", lambda contract="full": pytest.fail("ran before the output check"))
    with pytest.raises(SystemExit, match="full-contract benchmark"):
        experiment.main()
    seen = {}
    monkeypatch.setattr(experiment, "refuse_unregistered", lambda contract="full": seen.setdefault("contract", contract))
    monkeypatch.setattr(experiment, "run_pairs", lambda *args, **kwargs: seen.setdefault("out", args[2]))
    monkeypatch.setattr(experiment, "summarise", lambda path: "table")
    monkeypatch.setattr(experiment, "BLIND_RESULTS", tmp_path / "blind-dir")
    monkeypatch.setattr("sys.argv", ["experiment.py", "--lead-contract", "blind", "--replay-workers", str(path), "--worker-model", "w"])
    experiment.main()
    assert seen == {"contract": "blind", "out": tmp_path / "blind-dir" / "runs.jsonl"}
    assert (tmp_path / "blind-dir" / "summary.md").exists()


def test_blind_pre_registration_has_its_own_draft_and_heading(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "BLIND_DRAFT_FILE", tmp_path / "blind-draft.md")
    monkeypatch.setattr(experiment, "PREDICTIONS", tmp_path / "PREDICTIONS.md")
    (tmp_path / "PREDICTIONS.md").write_text("## 04-hierarchical: the full benchmark")
    with pytest.raises(SystemExit, match="missing"):
        experiment.refuse_unregistered("blind")
    (tmp_path / "blind-draft.md").write_text("**DRAFT.**")
    with pytest.raises(SystemExit, match="DRAFT"):
        experiment.refuse_unregistered("blind")
    (tmp_path / "blind-draft.md").write_text("Frozen.")
    with pytest.raises(SystemExit, match="no section"):
        experiment.refuse_unregistered("blind")
    (tmp_path / "PREDICTIONS.md").write_text("## 04-hierarchical: the full benchmark\n\n## 04-hierarchical, blind leads: why")
    experiment.refuse_unregistered("blind")


def test_run_py_takes_the_blind_and_replay_flags(tmp_path, monkeypatch, capsys):
    path, _ = published(tmp_path, runs=(1, 2, 3))
    monkeypatch.setattr(run_module, "backend_from_name", lambda name: ScriptedBackend(script()))
    scratch = tmp_path / "scratch.jsonl"
    monkeypatch.setattr("sys.argv", ["run.py", "--run", "3", "--lead-contract", "blind", "--replay-workers", str(path), "--worker-model", "w", "--out", str(scratch)])
    run_module.main()
    printed = capsys.readouterr().out
    assert "[blind leads]" in printed and "replayed from" in printed
    assert [json.loads(line)["arm"] for line in scratch.read_text().splitlines()] == rotated(list(ARMS), 2)


def test_end_to_end_blind_removes_rule8_channel(tmp_path):
    full = by_arm(pair(script()))
    blind = by_arm(pair(script(), lead_contract="blind"))
    assert full["by-runtime"]["score"]["regressed"] == [] and full["by-runtime"]["final_hub"]["http.default_timeout"] == "300ms"
    assert blind["by-runtime"]["score"]["regressed"] == [PRICING] and blind["by-runtime"]["ladder"][PRICING] == "reached-hub"
    assert blind["by-runtime"]["final_hub"]["http.default_timeout"] == "15s"
    for arm in ("flat", "by-runtime-registry"):  # they keep the needs by design
        assert blind[arm]["score"]["regressed"] == [] and blind[arm]["final_hub"]["http.default_timeout"] == "300ms"
    assert {row["lead_contract"] for row in blind.values()} == {"blind"} and {row["lead_contract"] for row in full.values()} == {"full"}


def test_rotation_matches_original(tmp_path):
    path, rows = published(tmp_path, runs=(1, 2, 3))
    replay = load_replay(path)
    for run in (1, 2, 3):
        blind = pair(script(), run=run, lead_contract="blind", workers=replay.workers_for(run), workers_replayed_from=replay.source())
        assert [row["arm"] for row in blind] == [row["arm"] for row in rows if row["run"] == run] == rotated(list(ARMS), run - 1)
    assert CUTS  # the cuts are not part of the contract variant
