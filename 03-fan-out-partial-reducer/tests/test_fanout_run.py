import json
import time

import pytest
from fanout_helpers import W1, W2, W3, Script, colliding_payloads, fixes_for, payload

from fanout import names_service, run_fanout
from fanout_config import ARMS, WORKERS
from fanout_prompts import service_paths, worker_prompt
from topologies.fixcheck import check
from topologies.harbour import HUB_KEYS, RISKS, SERVICES, worker_contract
from topologies.model import ScriptedBackend


def run(arm, script, **kwargs):
    return run_fanout(arm=arm, backend=ScriptedBackend(script), worker_model="worker", reducer_model="reducer", **kwargs)


def test_split_covers_every_service_once_and_every_shared_key_collides():
    assigned = [service for services in WORKERS.values() for service in services]
    assert sorted(assigned) == sorted(SERVICES)
    owner = {service: worker for worker, services in WORKERS.items() for service in services}
    wanting = {
        "http.default_timeout": {"bookings-api.missing_timeout", "notifications.missing_timeout"},
        "backup.policy": {"customer-profiles.missing_backup", "maintenance-scheduler.missing_backup"},
        "availability.min_instances": {"fleet-telemetry.single_point_of_failure", "maintenance-scheduler.single_point_of_failure"},
    }
    for key, risk_ids in wanting.items():
        assert len({owner[risk_id.split(".")[0]] for risk_id in risk_ids}) >= 2, key
    assert set(wanting) <= set(HUB_KEYS)
    assert {risk.id for risk in RISKS} >= set().union(*wanting.values())


def test_two_committing_workers_share_a_hub_key():
    record = run("first-wins", Script(colliding_payloads()))
    assert {w["worker"]: w["status"] for w in record["workers"]} == {W1: "committed", W2: "committed", W3: "no-change"}
    assert sorted(record["commit_order"]) == [W1, W2]
    assert record["shared_keys"] == ["http.default_timeout"]
    assert record["conflicted_keys"] == ["http.default_timeout"]
    assert len(record["textual_conflicts"]) == 1 and record["textual_conflicts"][0]["files"] == ["platform.yaml"]


def test_all_three_workers_commit_when_each_has_a_fix():
    workers = {worker: payload(fixes_for(worker)) for worker in WORKERS}
    record = run("first-wins", Script(workers))
    assert [w["status"] for w in record["workers"]] == ["committed"] * 3
    assert sorted(record["commit_order"]) == sorted(WORKERS)


def test_first_wins_keeps_the_first_commit_and_logs_the_drop():
    record = run("first-wins", Script(colliding_payloads()))
    [conflict] = record["conflicts"]
    first = record["commit_order"][0]
    expected = {W1: "15s", W2: "10s"}[first]
    assert conflict["value"] == expected and conflict["decided_by"] == "first-wins"
    assert conflict["dropped_workers"] == [({W1, W2} - {first}).pop()]
    assert record["final_hub"]["http.default_timeout"] == expected
    assert record["final_hub"]["backup.policy"] == "daily/30d"
    assert "regressions" in record["score"] and "pricing-engine.demand_model_timeout" in record["score"]["regressed"]


def test_supervisor_value_lands_in_the_merged_hub_and_the_prompt_has_values_and_reasons():
    script = Script(colliding_payloads(), supervisor='{"value": "300ms", "reason": "pricing-engine needs 300ms"}')
    record = run("supervisor-merges", script)
    assert record["final_hub"]["http.default_timeout"] == "300ms"
    assert record["conflicts"][0]["decided_by"] == "supervisor"
    [prompt] = script.prompts_for("supervisor")
    assert "'15s'" in prompt and "'10s'" in prompt and "charges take up to 12 s" in prompt and "the SMTP relay is slow" in prompt
    assert record["tokens_by_role"]["reducer"] > 0


def test_supervisor_failure_falls_back_to_first_wins_and_says_so():
    script = Script(colliding_payloads(), supervisor="not json at all")
    record = run("supervisor-merges", script)
    assert record["conflicts"][0]["decided_by"] == "first-wins (supervisor invalid)"
    assert record["conflicts"][0]["value"] == {W1: "15s", W2: "10s"}[record["commit_order"][0]]


def test_hub_owner_workers_never_edit_the_hub_and_the_owner_applies_requests():
    owner = json.dumps({"hub": [
        {"key": "http.default_timeout", "value": "300ms", "reason": "pricing needs 300ms"},
        {"key": "backup.policy", "value": "weekly/28d", "reason": "cheapest"},
    ]})
    script = Script(colliding_payloads(), hub_owner=owner)
    record = run("hub-owner", script)
    assert record["textual_conflicts"] == []
    assert record["final_hub"]["http.default_timeout"] == "300ms" and record["final_hub"]["backup.policy"] == "weekly/28d"
    assert [item["decided_by"] for item in record["resolutions"]] == ["hub-owner", "hub-owner"]
    assert record["conflicted_keys"] == ["http.default_timeout"]
    assert "cannot edit platform.yaml" in next(system for role, system, _ in script.prompts if role == "worker")


def test_hub_owner_without_a_decision_falls_back_to_first_wins_and_says_so():
    record = run("hub-owner", Script(colliding_payloads(), hub_owner="garbage"))
    assert all(item["decided_by"].startswith("first-wins (hub owner gave no decision)") for item in record["resolutions"])


def test_human_arm_logs_choice_seconds_and_the_shadow_pick_and_times_from_render():
    asked = []

    def show(text):
        asked.append(text)
        time.sleep(0.25)  # reading time before the timer matters: the timer starts after render

    script = Script(colliding_payloads(), supervisor='{"value": "300ms", "reason": "pricing-engine needs 300ms"}')
    record = run("human-decides", script, ask=lambda _: "a", show=show)
    [conflict] = record["conflicts"]
    assert conflict["decided_by"] == "human" and conflict["human_choice"] == "a"
    assert conflict["value"] == "15s"  # option a is the first worker's value
    assert conflict["human_seconds"] < 0.25
    assert conflict["supervisor_value"] == "300ms" and conflict["supervisor_decided_by"] == "supervisor"
    assert "http.default_timeout" in asked[0] and "[a]" in asked[0] and "[b]" in asked[0] and "[e]dit value" in asked[0]
    assert "charges take up to 12 s" in asked[0] and "the SMTP relay is slow" in asked[0]
    assert record["tokens_by_role"]["supervisor_shadow"] > 0 and record["tokens_by_role"]["reducer"] == 0


def test_human_edit_value_is_taken_verbatim():
    answers = iter(["x", "e", "", "20s"])
    record = run("human-decides", Script(colliding_payloads()), ask=lambda _: next(answers), show=lambda _: None)
    assert record["conflicts"][0]["human_choice"] == "edit" and record["final_hub"]["http.default_timeout"] == "20s"


def test_injected_failure_makes_no_call_commits_nothing_and_tells_the_report():
    script = Script({**colliding_payloads(), W1: AssertionError("must not be called")}, report="W1's services were not touched: bookings-api, maintenance-scheduler and admin-console.")
    record = run("first-wins", script, fail_worker=W1)
    failed = next(w for w in record["workers"] if w["worker"] == W1)
    assert failed["status"] == "injected-failure" and failed["attempts"] == 2 and failed["commit_order"] is None
    assert record["failed_services"] == WORKERS[W1]
    assert sorted(record["named_failed_services"]) == sorted(WORKERS[W1]) and record["silent_success"] is False
    [report_prompt] = script.prompts_for("report")
    assert f"w1 ({', '.join(WORKERS[W1])}): returned nothing usable (injected-failure)" in report_prompt


def test_report_that_claims_success_silently_is_scored_as_silent():
    record = run("first-wins", Script(colliding_payloads(), report="All fixes merged cleanly."), fail_worker=W3)
    assert record["failed_services"] == WORKERS[W3]
    assert record["named_failed_services"] == [] and record["silent_success"] is True


def test_clean_run_is_never_silent_success():
    record = run("first-wins", Script(colliding_payloads()))
    assert record["failed_services"] == [] and record["silent_success"] is False


def test_backend_error_takes_one_worker_out_not_the_run():
    record = run("first-wins", Script({**colliding_payloads(), W2: RuntimeError("claude -p failed (1)")}))
    statuses = {w["worker"]: w["status"] for w in record["workers"]}
    assert statuses[W2] == "error" and "claude -p failed" in next(w for w in record["workers"] if w["worker"] == W2)["error"]
    assert record["failed_services"] == WORKERS[W2]
    assert record["conflicts"] == []


def test_invalid_payload_after_the_retry_is_a_failed_worker():
    record = run("first-wins", Script({**colliding_payloads(), W3: "no json here"}))
    failed = next(w for w in record["workers"] if w["worker"] == W3)
    assert failed["status"] == "invalid" and failed["attempts"] == 2
    assert record["failed_services"] == WORKERS[W3]


def test_a_file_outside_the_workers_services_is_invalid():
    bad = payload({"pricing-engine/package.json": "{}"})
    record = run("first-wins", Script({**colliding_payloads(), W1: bad}))
    assert next(w for w in record["workers"] if w["worker"] == W1)["status"] == "invalid"


def test_score_comes_only_from_the_shared_checker_reference_fix_is_12_of_12():
    workers = {worker: payload(fixes_for(worker)) for worker in WORKERS}
    for arm in ("first-wins", "hub-owner"):
        record = run(arm, Script(workers))
        assert record["score"]["risks_fixed"] == 12 and record["score"]["regressions"] == 0 and record["score"]["outside_contract"] == 0
        assert record["conflicts"] == []


def test_unchanged_world_scores_zero_with_reasons_per_risk():
    record = run("first-wins", Script({worker: payload() for worker in WORKERS}))
    assert record["score"]["risks_fixed"] == 0 and record["score"]["regressions"] == 0
    assert set(record["score"]["risks"]) == {risk.id for risk in RISKS}
    assert all(item["reason"] for item in record["score"]["risks"].values())
    assert record["unfixed_services"] == sorted(SERVICES)


def test_record_is_json_serialisable_and_carries_what_a_re_read_needs():
    record = run("supervisor-merges", Script(colliding_payloads()), fail_worker=W3)
    reread = json.loads(json.dumps(record))
    for field in ("workers", "conflicts", "resolutions", "report", "score", "tokens_by_role", "merged_diff", "final_hub", "worker_served_models", "reducer_served_models"):
        assert field in reread
    worker = next(w for w in reread["workers"] if w["worker"] == W1)
    assert worker["files"] and worker["hub_changes"][0]["reason"] == "charges take up to 12 s"
    assert "+timeout=" in reread["merged_diff"] or "timeout=http_timeout" in reread["merged_diff"]
    assert set(reread["tokens_by_role"]) == {"workers", "reducer", "report", "supervisor_shadow"}


def test_worker_prompt_has_contract_verbatim_own_files_and_no_answer_key(tmp_path):
    script = Script(colliding_payloads())
    run("first-wins", script, workdir=tmp_path / "kept")
    prompts = [(system, prompt) for role, system, prompt in script.prompts if role == "worker"]
    assert len(prompts) == 3
    for system, prompt in prompts:
        assert worker_contract() in system
        assert "CHECKER" not in system + prompt and "fixcheck" not in system + prompt
    w1_prompt = next(prompt for _, prompt in prompts if prompt.startswith("Your services: bookings-api"))
    assert "=== bookings-api/bookings_api.py ===" in w1_prompt and "=== platform.yaml ===" in w1_prompt
    assert "pricing-engine/" not in w1_prompt.split("Your files:")[1]
    assert not (tmp_path / "kept" / "main" / "CHECKER.md").exists()
    assert (tmp_path / "kept" / "main" / "CONTRACT.md").exists()


def test_each_worker_has_its_own_git_worktree(tmp_path):
    run("first-wins", Script({worker: payload(fixes_for(worker)) for worker in WORKERS}), workdir=tmp_path / "kept")
    for worker in WORKERS:
        assert (tmp_path / "kept" / worker / ".git").exists()
    assert "bookings-api/service.yaml" in service_paths(tmp_path / "kept" / "main", WORKERS[W1])


def test_unknown_arm_or_worker_is_refused():
    with pytest.raises(ValueError):
        run("nope", Script({}))
    with pytest.raises(ValueError):
        run("first-wins", Script({}), fail_worker="w9")


def test_names_service_ignores_case_and_separators():
    assert names_service("Bookings API was not fixed", "bookings-api")
    assert names_service("the maintenance_scheduler", "maintenance-scheduler")
    assert not names_service("everything is fine", "admin-console")


def test_all_arms_run_and_the_score_is_the_checkers(tmp_path):
    for arm in ARMS:
        record = run(arm, Script(colliding_payloads()), ask=lambda _: "a", show=lambda _: None, workdir=tmp_path / arm)
        assert check(tmp_path / arm / "merged").fixed == record["score"]["fixed"]


def test_report_prompt_carries_who_asked_for_each_hub_change_and_why():
    script = Script(colliding_payloads())
    run("first-wins", script)
    [prompt] = script.prompts_for("report")
    assert "backup.policy: none -> daily/30d [uncontested" in prompt and "profiles has no backup" in prompt
    assert "http.default_timeout" in prompt and "[contested, decided by first-wins]" in prompt
    assert "charges take up to 12 s" in prompt and "the SMTP relay is slow" in prompt
