import json

import pytest
from fanout_helpers import BASE_CARDS, FIXES, Script, card_worker_payload, hub_change

from fanout import ARMS, WORKERS, run_fanout
from topologies.model import ScriptedBackend

W1, W2, W3 = (cards[0] for cards in WORKERS.values())


def timeout_collision_script(**kwargs) -> Script:
    """W1 and W2 want different global timeouts; W3 only pins versions."""
    workers = {
        W1: card_worker_payload(WORKERS["w1"], [hub_change("http.default_timeout", "5s", "charges take 4s")]),
        W2: card_worker_payload(WORKERS["w2"], [hub_change("http.default_timeout", "10s", "smtp is slow"), hub_change("backup.policy", "daily", "profiles has none")]),
        W3: card_worker_payload(WORKERS["w3"], [hub_change("dependencies.pinning", "lockfile", "pin it")]),
    }
    return Script(workers, **kwargs)


def run(arm, script, **kwargs):
    return run_fanout(arm=arm, backend=ScriptedBackend(script), worker_model="worker", reducer_model="reducer", **kwargs)


def test_clean_run_every_worker_commits_and_two_workers_share_a_key():
    record = run("first-wins", timeout_collision_script())
    assert [w["status"] for w in record["workers"]] == ["committed"] * 3
    assert sorted(record["commit_order"]) == ["w1", "w2", "w3"]
    assert record["shared_keys"] == ["http.default_timeout"]


def test_first_wins_keeps_the_first_commit_and_logs_the_drop():
    record = run("first-wins", timeout_collision_script())
    [conflict] = record["conflicts"]
    first = next(w for w in record["commit_order"] if w in ("w1", "w2"))
    expected = {"w1": "5s", "w2": "10s"}[first]
    assert conflict["value"] == expected and conflict["decided_by"] == "first-wins"
    assert conflict["dropped_workers"] == [({"w1", "w2"} - {first}).pop()]
    assert record["final_hub"]["http.default_timeout"] == expected
    assert record["final_hub"]["backup.policy"] == "daily"
    assert len(record["textual_conflicts"]) == 1


def test_supervisor_value_lands_in_the_merged_hub():
    script = timeout_collision_script(supervisor='{"value": "300ms", "reason": "pricing needs 300ms"}')
    record = run("supervisor-merges", script)
    assert record["final_hub"]["http.default_timeout"] == "300ms"
    assert record["conflicts"][0]["decided_by"] == "supervisor"
    assert record["score"]["regressed"] == []
    assert record["tokens_by_role"]["reducer"] > 0 and record["tokens_by_role"]["supervisor_shadow"] == 0


def test_supervisor_sees_the_conflict_and_the_merged_cards():
    script = timeout_collision_script()
    run("supervisor-merges", script)
    [prompt] = [p for role, p in script.calls if role == "supervisor"]
    assert "http.default_timeout" in prompt and "'5s'" in prompt and "'10s'" in prompt
    assert "charges take 4s" in prompt and "=== pricing-engine.md ===" in prompt


def test_unresolved_collision_breaks_pricing_and_the_checker_sees_it():
    record = run("first-wins", timeout_collision_script())
    assert record["score"]["regressed"] == ["pricing-engine:demand-model-300ms"]
    assert record["score"]["risks_fixed"] == 12


def test_supervisor_failure_falls_back_to_first_wins_and_says_so():
    record = run("supervisor-merges", timeout_collision_script(supervisor="not json"))
    [conflict] = record["conflicts"]
    assert conflict["decided_by"] == "first-wins (supervisor invalid)" and conflict["error"]


def test_hub_owner_workers_never_touch_the_hub_and_the_reducer_applies_requests():
    owner = json.dumps({"hub": [{"key": "http.default_timeout", "value": "300ms", "reason": "pricing"}, {"key": "backup.policy", "value": "daily", "reason": "ok"}]})
    script = timeout_collision_script(hub_owner=owner)
    record = run("hub-owner", script)
    assert record["textual_conflicts"] == []
    assert record["final_hub"]["http.default_timeout"] == "300ms"
    assert record["final_hub"]["dependencies.pinning"] == "lockfile"  # the owner skipped it, so the request stands
    by_key = {item["key"]: item for item in record["resolutions"]}
    assert by_key["http.default_timeout"]["decided_by"] == "hub-owner" and by_key["http.default_timeout"]["contested"]
    assert by_key["dependencies.pinning"]["decided_by"].startswith("first-wins")


def test_hub_owner_worker_prompt_says_it_cannot_edit_the_hub():
    seen = []
    script = timeout_collision_script()
    backend = ScriptedBackend(lambda system, prompt: (seen.append(system), script(system, prompt))[1])
    run_fanout(arm="hub-owner", backend=backend, worker_model="w", reducer_model="r")
    assert any("cannot edit platform.yaml" in system for system in seen)


def test_human_arm_logs_choice_seconds_and_the_supervisors_pick():
    answers = iter(["x", "e", "", "2s"])
    shown = []
    script = timeout_collision_script(supervisor='{"value": "300ms", "reason": "pricing"}')
    record = run("human-decides", script, ask=lambda _: next(answers), show=shown.append)
    [conflict] = record["conflicts"]
    assert conflict["decided_by"] == "human" and conflict["value"] == "2s" and conflict["human_choice"] == "edit"
    assert conflict["supervisor_value"] == "300ms" and conflict["human_seconds"] >= 0
    assert record["final_hub"]["http.default_timeout"] == "2s"
    assert "http.default_timeout" in shown[0] and "[a]" in shown[0] and "[b]" in shown[0] and "[e]dit" in shown[0]
    assert record["tokens_by_role"]["supervisor_shadow"] > 0 and record["tokens_by_role"]["reducer"] > 0  # reducer is the summary


def test_human_arm_choice_a_takes_the_first_option():
    record = run("human-decides", timeout_collision_script(), ask=lambda _: "a", show=lambda _: None)
    assert record["conflicts"][0]["value"] == "5s" and record["conflicts"][0]["human_choice"] == "a"


def test_injected_failure_leaves_the_worker_out_and_names_it_when_the_summary_does():
    script = timeout_collision_script(summary="Merged w1 and w2. pricing-engine and payments-gateway were not fixed.")
    record = run("first-wins", script, fail_worker="w3")
    failed = next(w for w in record["workers"] if w["worker"] == "w3")
    assert failed["status"] == "injected-failure" and failed["tokens"] == 0 and failed["files"] == {}
    assert "w3" not in record["commit_order"]
    assert record["failed_cards"] == WORKERS["w3"]
    assert record["named_failed_cards"] == WORKERS["w3"] and not record["silent_success"]
    assert "pricing-engine.md" in record["harness_skip_note"]
    assert not any(role == "worker" and "=== pricing-engine.md" in prompt for role, prompt in script.calls)


def test_injected_failure_with_a_cheerful_summary_is_silent_success():
    record = run("first-wins", timeout_collision_script(summary="All fixes merged successfully."), fail_worker="w3")
    assert record["silent_success"] and record["named_failed_cards"] == []
    assert record["score"]["unfixed"] and "pricing-engine.md:unpinned_dependency" in record["score"]["unfixed"]


def test_partial_naming_is_neither_silent_nor_complete():
    record = run("first-wins", timeout_collision_script(summary="pricing-engine was skipped."), fail_worker="w3")
    assert record["named_failed_cards"] == ["pricing-engine.md"] and not record["silent_success"]


def test_a_backend_error_in_one_worker_does_not_stop_the_run():
    script = timeout_collision_script()
    script.workers[W3] = RuntimeError("claude -p failed (1): boom")
    record = run("first-wins", script)
    failed = next(w for w in record["workers"] if w["worker"] == "w3")
    assert failed["status"] == "error" and "boom" in failed["error"]
    assert record["failed_cards"] == WORKERS["w3"]


def test_invalid_payload_after_retry_is_a_failed_worker():
    script = timeout_collision_script()
    script.workers[W3] = '{"files": [{"path": "bookings-api.md", "content": "x"}], "hub_changes": []}'  # not W3's card
    record = run("first-wins", script)
    failed = next(w for w in record["workers"] if w["worker"] == "w3")
    assert failed["status"] == "invalid" and failed["attempts"] == 2 and failed["tokens"] > 0


def test_worker_that_changes_nothing_is_reported_not_failed():
    script = timeout_collision_script()
    script.workers[W3] = '{"files": [], "hub_changes": []}'
    record = run("first-wins", script)
    assert next(w for w in record["workers"] if w["worker"] == "w3")["status"] == "no-change"
    assert record["failed_cards"] == []


def test_record_is_json_and_rescorable_from_what_it_stores():
    from fixcheck import check

    record = run("supervisor-merges", timeout_collision_script())
    again = json.loads(json.dumps(record))
    assert check(again["final_cards"], again["final_hub"]) == again["score"]
    assert set(again["final_cards"]) == set(BASE_CARDS)


def test_unknown_arm_is_rejected():
    with pytest.raises(ValueError):
        run("majority-vote", timeout_collision_script())
    assert len(ARMS) == 4 and len(FIXES) == 12


def test_reducer_summary_prompt_lists_every_side_of_a_conflict_and_each_workers_status():
    script = timeout_collision_script()
    run("first-wins", script, fail_worker="w3")
    [prompt] = [p for role, p in script.calls if role == "summary"]
    assert "w1 wanted 5s" in prompt and "w2 wanted 10s" in prompt
    assert "w3 (pricing-engine.md, payments-gateway.md): no usable output (injected-failure)" in prompt
