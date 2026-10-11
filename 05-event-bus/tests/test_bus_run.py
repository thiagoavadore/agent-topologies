import json
import subprocess
import sys

import pytest
from bus_helpers import FOLDER, Script, smart_actions

import experiment
import run as run_module
from bus import run_row
from bus_config import ARMS
from bus_faults import faults_for_run
from topologies.model import ScriptedBackend


def row_for(arm, tmp_path, *, index=0, owner=None, run=1, **kwargs):
    work = tmp_path / f"{arm}-{index}-{run}"
    script = Script(work / "repo", owner=owner, **kwargs)
    row = run_row(run=run, run_index=index, backend_name="scripted", arm=arm, backend=ScriptedBackend(script), consumer_model="c", owner_model="o", workdir=work)
    return row, script


def deliveries_of(row, risk_id):
    return [item for item in row["deliveries"] if item["risk_id"] == risk_id]


def test_checker_answer_key_not_in_scratch(tmp_path):
    row_for("no-owner", tmp_path)
    assert not list((tmp_path / "no-owner-0-1").rglob("CHECKER.md"))


def test_no_owner_mechanics(tmp_path):
    row, script = row_for("no-owner", tmp_path)
    faults = faults_for_run(0)
    assert [item["route"] for item in row["published"] if item["risk_id"].startswith("fleet")] == ["discarded:no-subscriber"] * 2
    crash = deliveries_of(row, faults["consumer-crash"])
    assert [item["outcome"] for item in crash] == ["injected-failure"]  # at-most-once: never delivered again
    duplicate = deliveries_of(row, faults["duplicate"])
    assert [item["duplicate"] for item in duplicate] == [False, True] and row["duplicate"]["calls"] == 2
    assert all(item["outcome"] != "skipped-duplicate" for item in row["deliveries"])
    assert row["store"] == [] and row["dead_letters"] == [] and row["invalid"] == []
    assert script.prompts_for("steward") == [] and script.prompts_for("platform-agent") == [] and not row["owner"]["called"]
    declined = deliveries_of(row, faults["wrong-service"])
    assert [item["outcome"] for item in declined] == ["not_mine"] and row["wrong_service"] == {"declined": True, "bogus_edit": False}
    crashed_team = crash[0]["team"]
    received = [entry for entry in row["team_logs"][crashed_team] if entry["message_id"] == crash[0]["message_id"]]
    assert [entry["event"] for entry in received] == ["received"]  # a crashed delivery leaves a receipt and no outcome
    assert row["score"]["risks_fixed"] == 6 and row["fleet_fixed"] == 0 and row["clean_fixed"] == 5
    assert row["fault_outcomes"] == {"malformed": "lost", "wrong-service": "lost", "consumer-crash": "lost"}


def test_platform_mechanics_with_an_idle_owner(tmp_path):
    row, script = row_for("platform-owned", tmp_path)
    faults = faults_for_run(0)
    routes = {item["risk_id"]: item["route"] for item in row["published"]}
    assert routes[faults["malformed"]] == "invalid" and len(row["invalid"]) == 1
    assert [item["route"] for item in row["published"] if item["risk_id"].startswith("fleet")] == ["dead-letter:no-subscriber"] * 2
    crash = deliveries_of(row, faults["consumer-crash"])
    assert [(item["outcome"], item["attempt"]) for item in crash] == [("injected-failure", 1), ("fixed", 2)]
    retried = [event for event in row["store"] if event["message_id"] == crash[0]["message_id"] and event["correlation_id"]]
    assert retried and all(event["history"] == ["retry"] and event["correlation_id"] == crash[0]["message_id"] for event in retried)
    duplicate = deliveries_of(row, faults["duplicate"])
    assert [item["outcome"] for item in duplicate] == ["fixed", "skipped-duplicate"] and row["duplicate"]["calls"] == 1
    reasons = [entry["reason"] for entry in row["dead_letters"]]
    assert sum("no subscriber" in reason for reason in reasons) == 2 and sum("replied not_mine" in reason for reason in reasons) == 1
    assert row["owner"]["called"] and len(script.prompts_for("platform-agent")) == 1
    assert row["fault_outcomes"] == {"malformed": "lost", "wrong-service": "lost", "consumer-crash": "recovered"}


def test_owner_prompts_show_the_arms_view(tmp_path):
    steward, script = row_for("steward-agent", tmp_path)
    [prompt] = script.prompts_for("steward")
    shown = [line for line in prompt.split("Channel records, oldest first:\n")[1].splitlines() if line.startswith("{")]
    assert len(shown) == steward["store_size"]  # the idle owner changes nothing, so the final store is what it was shown
    assert any('"event": "delivered"' in line for line in shown) and any('"event": "outcome"' in line for line in shown)
    platform, script = row_for("platform-owned", tmp_path)
    [prompt] = script.prompts_for("platform-agent")
    shown = [line for line in prompt.split("Channel records, oldest first:\n")[1].splitlines() if line.startswith("{")]
    assert len(shown) == len(platform["dead_letters"]) + len(platform["invalid"])
    assert not any('"event"' in line for line in shown) and all('"channel"' in line for line in shown)


def test_consumer_prompt_hides_trace_context_and_the_arm(tmp_path):
    _, script = row_for("platform-owned", tmp_path, owner=smart_actions)
    consumer_prompts = script.prompts_for("consumer")
    messages = [json.loads(prompt.splitlines()[1]) for prompt in consumer_prompts]
    assert messages and not any({"correlation_id", "history"} & set(message) for message in messages)
    systems = {system for role, system, _ in script.prompts if role == "consumer" and "Team Rentals" in system.split("Your team owns")[0]}
    assert len(systems) == 1  # the same text for every Team Rentals call


def test_end_to_end_owner_decides_what_is_lost(tmp_path):
    """The folder's value: the same consumers and faults, three owners, three outcomes, and an investigator that can say why."""
    faults = faults_for_run(0)
    no_owner, _ = row_for("no-owner", tmp_path)
    steward, _ = row_for("steward-agent", tmp_path, owner=smart_actions)
    platform, _ = row_for("platform-owned", tmp_path, owner=smart_actions)

    assert (no_owner["score"]["risks_fixed"], no_owner["fleet_fixed"]) == (6, 0)
    assert no_owner["duplicate"]["calls"] == 2

    for row in (steward, platform):
        assert row["score"]["risks_fixed"] == 11 and row["score"]["regressions"] == 0, row["score"]["fixed"]
        assert row["fleet_fixed"] == 2 and row["clean_fixed"] == 5
        assert row["fault_outcomes"] == {"malformed": "recovered", "wrong-service": "recovered", "consumer-crash": "recovered"}
        assert [item["result"].split(":")[0].split(";")[0] for item in row["owner"]["action_log"]].count("ignored") == 0

    assert platform["duplicate"]["calls"] == 1
    assert steward["duplicate"]["calls"] == 2
    for row in (steward, platform):  # corrective copies get new ids but the wrong-service metric stays on the original delivery
        assert row["wrong_service"] == {"declined": True, "bogus_edit": False}
    assert [item["action"] for item in steward["owner"]["actions"]].count("assign_owner") == 1
    assert all(event["correlation_id"] == "" and event["history"] == [] for event in steward["store"])
    assert any(event["message_id"].startswith("c") for event in steward["store"])  # copies carry fresh ids
    assert all(item["message_id"].startswith("m") for item in platform["deliveries"]) and any(event["correlation_id"] for event in platform["store"])
    # The steward also redelivers the silent crash target; the platform did that in code.
    steward_kinds = sorted(item["action"] for item in steward["owner"]["actions"])
    assert steward_kinds == sorted([*(item["action"] for item in platform["owner"]["actions"]), "redeliver"])

    # With an owner that does nothing, the records alone decide what an investigator can say about each loss.
    idle_platform, _ = row_for("platform-owned", tmp_path, run=2)
    idle_no_owner, _ = row_for("no-owner", tmp_path, run=2)
    assert idle_platform["investigator"]["score"]["channel_losses"] == 4  # two fleet-telemetry risks, the malformed target and the wrong-service target
    assert idle_no_owner["investigator"]["score"]["channel_losses"] == 5
    assert idle_platform["investigator"]["score"]["accuracy"] > idle_no_owner["investigator"]["score"]["accuracy"]
    assert idle_platform["investigator"]["score"]["accuracy"] == 1.0
    assert idle_platform["investigator_no_trace"] is not None and idle_no_owner["investigator_no_trace"] is None
    assert platform["tokens_by_role"]["investigator_no_trace"] > 0
    assert platform["total_tokens"] == sum(platform["tokens_by_role"][key] for key in ("consumers", "owner", "investigator"))


def test_unusable_owner_reply_is_recorded_and_the_run_goes_on(tmp_path):
    work = tmp_path / "bad"
    script = Script(work / "repo")
    plain = script.__call__

    def broken(system, prompt):
        return "no json at all" if Script.role_of(system) == "platform-agent" else plain(system, prompt)

    row = run_row(run=1, run_index=0, backend_name="scripted", arm="platform-owned", backend=ScriptedBackend(broken), consumer_model="c", owner_model="o", workdir=work)
    assert row["owner"]["called"] and row["owner"]["error"] and row["owner"]["actions"] == []
    assert row["score"]["risks_fixed"] == 6 + 1  # the platform retries the crash target in code


def write(path, rows):
    path.write_text("".join(json.dumps(item) + "\n" for item in rows))


@pytest.fixture(scope="module")
def three_rows(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rows")
    return [row_for(arm, tmp, owner=smart_actions)[0] for arm in ARMS]


def test_experiment_refuses_while_unregistered(tmp_path, monkeypatch):
    draft, predictions = tmp_path / "predictions-draft.md", tmp_path / "PREDICTIONS.md"
    monkeypatch.setattr(experiment, "DRAFT_FILE", draft)
    monkeypatch.setattr(experiment, "PREDICTIONS", predictions)
    with pytest.raises(SystemExit, match="missing"):
        experiment.refuse_unregistered()
    draft.write_text("# Predictions\n\n**DRAFT.** not frozen\n")
    predictions.write_text("## 05-event-bus: frozen\n")
    with pytest.raises(SystemExit, match="DRAFT"):
        experiment.refuse_unregistered()
    draft.write_text("# Predictions\n")
    predictions.write_text("## 04-hierarchical: frozen\n")
    with pytest.raises(SystemExit, match="05-event-bus"):
        experiment.refuse_unregistered()
    predictions.write_text("## 05-event-bus: frozen\n")
    experiment.refuse_unregistered()


def test_experiment_script_refuses_the_real_draft():
    done = subprocess.run([sys.executable, str(FOLDER / "experiment.py"), "--n", "1"], capture_output=True, text=True)
    assert done.returncode != 0 and "Refusing to run" in (done.stderr + done.stdout)


def test_arm_order_rotates_per_run():
    arms = list(ARMS)
    assert [experiment.rotated(arms, i)[0] for i in range(4)] == [*arms, arms[0]]
    assert sorted(experiment.rotated(arms, 1)) == sorted(arms)


def test_summary_renders(tmp_path, three_rows):
    write(tmp_path / "runs.jsonl", three_rows)
    text = experiment.summarise(tmp_path / "runs.jsonl")
    for arm in ARMS:
        assert f"| {arm} | 1 |" in text
    assert "| no-owner | 1 | 6.0 (6) |" in text and "| platform-owned | 1 | 11.0 (11) |" in text
    assert "| platform-owned | malformed | 1 of 1 | 0 of 1 | 0 of 1 |" in text
    assert "trace context stripped" in text and "Per run" in text and "Consumers c (served: not reported)" in text


def test_summary_refuses_mixed_setups(tmp_path, three_rows):
    first, second = three_rows[0], three_rows[1]
    for changes in ({"consumer_model": "other"}, {"owner_model": "other"}, {"backend": "anthropic-sdk"}, {"consumer_served_primary": ["claude-haiku-4-5"]}, {"owner_served_primary": ["claude-haiku-4-5"]}):
        write(tmp_path / "mixed.jsonl", [first, {**second, **changes}])
        with pytest.raises(ValueError, match="mixes"):
            experiment.summarise(tmp_path / "mixed.jsonl")


def test_run_refuses_benchmark_file():
    with pytest.raises(SystemExit, match="never writes"):
        sys.argv = ["run.py", "--out", str(run_module.BENCHMARK_FILE)]
        run_module.main()
