import json
import sys

import pytest
from bus_helpers import FOLDER, Script, consumer_fix, literal_investigator, smart_actions

import experiment
import run as run_module
from bus import run_row
from bus_config import ARMS
from bus_faults import faults_for_run, scanner_messages
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


def write(path, rows, *, stamp=True):
    """Write rows as a benchmark file; `stamp` gives them one clean commit, as a committed benchmark run would have."""
    path.write_text("".join(json.dumps({**item, "code_commit": "abc123"} if stamp else item) + "\n" for item in rows))


def test_code_commit_marks_uncommitted_code_in_the_folder_but_not_results(tmp_path, monkeypatch):
    import bus

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        out = "deadbeef\n" if command[1] == "rev-parse" else dirty
        return type("Done", (), {"returncode": 0, "stdout": out})()

    monkeypatch.setattr(bus.subprocess, "run", fake_run)
    dirty = ""
    assert bus.code_commit() == "deadbeef"
    dirty = " M 05-event-bus/bus.py\n"
    assert bus.code_commit() == "deadbeef-dirty"
    status = next(command for command in calls if command[1] == "status")
    assert "05-event-bus" in status and "src" in status and "fixtures" in status and ":(exclude)05-event-bus/results" in status


def test_summary_refuses_dirty_unknown_or_mixed_commits(tmp_path, three_rows):
    path = tmp_path / "commits.jsonl"
    write(path, [{**three_rows[0], "code_commit": "abc-dirty"}], stamp=False)
    with pytest.raises(ValueError, match="uncommitted"):
        experiment.summarise(path)
    write(path, [{**three_rows[0], "code_commit": ""}], stamp=False)
    with pytest.raises(ValueError, match="uncommitted or unknown"):
        experiment.summarise(path)
    write(path, [{**three_rows[0], "code_commit": "aaa"}, {**three_rows[1], "code_commit": "bbb"}], stamp=False)
    with pytest.raises(ValueError, match="mixes code commits"):
        experiment.summarise(path)


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


def test_the_guard_points_at_the_real_predictions():
    # Never launch the real script here: once predictions are frozen it would start a live benchmark.
    assert experiment.DRAFT_FILE.name == "predictions-draft.md" and experiment.DRAFT_FILE.exists()
    assert experiment.PREDICTIONS.name == "PREDICTIONS.md"


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


CLEAN = "customer-profiles.missing_backup"


def decline_clean_once():
    seen = {"n": 0}

    def consumer(repo, message, owned):
        if message.get("service") == "customer-profiles" and message.get("category") == "missing_backup" and seen["n"] == 0:
            seen["n"] += 1
            return {"outcome": "cannot_fix", "files": [], "reason": "flaky decline"}
        return consumer_fix(repo, message, owned)

    return consumer


def test_owner_redelivery_and_republish_are_not_swallowed_by_the_idempotent_receiver(tmp_path):
    message_id = next(item["message_id"] for item in scanner_messages(0)[0] if item["risk_id"] == CLEAN)
    actions = lambda entries: [
        {"action": "redeliver", "message_id": message_id, "reason": "try again"},
        {"action": "return_to_producer", "message_id": message_id, "reason": "try again"},
    ]
    for arm in ("steward-agent", "platform-owned"):
        row, _ = row_for(arm, tmp_path, owner=actions, consumer=decline_clean_once())
        pass_two = [item for item in row["deliveries"] if item["risk_id"] == CLEAN and item["pass_no"] == 2]
        assert len(pass_two) == 2 and "skipped-duplicate" not in {item["outcome"] for item in pass_two}, arm
        assert row["truth"][CLEAN]["fixed"] and row["truth"][CLEAN]["cause"] == "none", arm
        assert [item["result"] for item in row["owner"]["action_log"]] == ["queued:Team Identity"] * 2


def test_platform_does_not_hand_the_transport_duplicate_on_when_the_first_processing_failed(tmp_path):
    dup_message = next(item["message_id"] for item in scanner_messages(0)[0] if item["risk_id"] == faults_for_run(0)["duplicate"])

    class JunkOnFirstDelivery(Script):
        junk_left = 2  # both backend attempts of the first delivery

        def __call__(self, system, prompt):
            if self.role_of(system) == "consumer" and f'"message_id": "{dup_message}"' in prompt and self.junk_left:
                self.junk_left -= 1
                return "junk, not json"
            return super().__call__(system, prompt)

    work = tmp_path / "junky"
    script = JunkOnFirstDelivery(work / "repo")
    row = run_row(run=1, run_index=0, backend_name="scripted", arm="platform-owned", backend=ScriptedBackend(script), consumer_model="c", owner_model="o", workdir=work)
    mine = [(item["attempt"], item["duplicate"], item["outcome"]) for item in row["deliveries"] if item["message_id"] == dup_message]
    assert mine == [(1, False, "invalid"), (2, False, "fixed"), (1, True, "skipped-duplicate")]


def test_operating_tokens_leave_out_the_investigator(tmp_path):
    row, _ = row_for("steward-agent", tmp_path, owner=smart_actions)
    roles = row["tokens_by_role"]
    assert row["operating_tokens"] == roles["consumers"] + roles["owner"] and row["total_tokens"] == row["operating_tokens"] + roles["investigator"]


def test_a_guessing_consumer_does_not_hide_a_channel_loss_in_a_run(tmp_path):
    faults = faults_for_run(0)
    assert faults["malformed"] == "bookings-api.missing_timeout"

    def guesser(repo, message, owned):
        if "category" not in message:
            sibling = {**message, "category": "no_rate_limit", "file": "bookings-api/service.yaml", "locator": "x"}
            return {**consumer_fix(repo, sibling, owned), "outcome": "fixed", "reason": "guessed"}
        return consumer_fix(repo, message, owned)

    row, _ = row_for("no-owner", tmp_path, consumer=guesser)
    truth = row["truth"][faults["malformed"]]
    assert not truth["fixed"] and truth["cause"] == "missing_fields" and truth["channel_caused"]
    assert row["fault_outcomes"]["malformed"] == "lost"
    assert row["investigator"]["score"]["channel_losses"] == 5


def test_first_problem_is_scored_in_every_arm_even_when_everything_is_repaired(tmp_path):
    for arm in ARMS:
        row, _ = row_for(arm, tmp_path, owner=smart_actions)
        score = row["investigator"]["score"]
        assert score["first_problem_scored"] == 6 and score["clean_total"] == 5, arm
        if arm != "no-owner":
            assert score["first_problem_correct"] == 6 and score["clean_correct"] == 5, (arm, score)
    platform, _ = row_for("platform-owned", tmp_path, owner=smart_actions, run=2)
    assert platform["investigator_no_trace"]["score"]["first_problem_scored"] == 6


def bad_investigator(kind):
    def investigate(prompt):
        if kind == "junk":
            return "not json"
        risks = literal_investigator(prompt)["risks"]
        return {"risks": [*risks[:-1], risks[0]]}  # repeats one risk and leaves another out

    return investigate


def test_failed_calls_are_counted_and_excluded_not_scored_as_wrong(tmp_path):
    for kind in ("junk", "repeat"):
        work = tmp_path / kind
        script = Script(work / "repo", owner=smart_actions)
        plain = script.__call__

        def backend(system, prompt, kind=kind, plain=plain):
            if Script.role_of(system) == "investigator":
                return json.dumps(bad_investigator(kind)(prompt)) if kind == "repeat" else "not json"
            if Script.role_of(system) == "platform-agent" and kind == "junk":
                raise RuntimeError("backend down")
            return plain(system, prompt)

        row = run_row(run=1, run_index=0, backend_name="scripted", arm="platform-owned", backend=ScriptedBackend(backend), consumer_model="c", owner_model="o", workdir=work)
        assert row["investigator"]["answer"] is None and row["investigator"]["error"] and row["investigator"]["score"] is None
        assert row["investigator_no_trace"]["score"] is None
        if kind == "junk":
            assert row["owner"]["called"] and "backend down" in row["owner"]["error"] and row["owner"]["actions"] == []
        good, _ = row_for("platform-owned", tmp_path, owner=smart_actions, run=3 if kind == "junk" else 4)
        path = tmp_path / f"{kind}.jsonl"
        write(path, [row, good])
        text = experiment.summarise(path)
        assert "| platform-owned | 2 | " in text
        failures = text.split("Failures (not hidden in the other tables):")[1]
        assert ("| platform-owned | 2 | 1 | 1 | 1 | 0 |" if kind == "junk" else "| platform-owned | 2 | 0 | 1 | 1 | 0 |") in failures
        assert "| platform-owned | trace context stripped | 1 |" in text  # only the good run is scored
