import importlib.util
import json
from pathlib import Path

import pytest
from fanout_helpers import Script, card_worker_payload, hub_change

from fanout import ARMS, WORKERS, run_row
from topologies.model import ScriptedBackend

# 01 also has an experiment.py, so load this folder's under its own name.
spec = importlib.util.spec_from_file_location("fanout_experiment", Path(__file__).parents[1] / "experiment.py")
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


def test_half_the_runs_fail_rounding_toward_failed():
    assert experiment.failing_run_indexes("first-wins", 5) == {0, 2, 4}
    assert experiment.failing_run_indexes("hub-owner", 4) == {0, 2}
    assert experiment.failing_run_indexes("human-decides", 3) == {1}


def test_failing_worker_rotates_across_all_workers():
    assert [experiment.failing_worker(i) for i in (0, 2, 4)] == ["w1", "w3", "w2"]


def test_arm_order_rotates_per_run():
    arms = experiment.AUTOMATED_ARMS
    assert [experiment.rotated(arms, i)[0] for i in range(4)] == [arms[0], arms[1], arms[2], arms[0]]
    assert sorted(experiment.rotated(arms, 1)) == sorted(arms)


def script() -> Script:
    return Script({
        "bookings-api.md": card_worker_payload(WORKERS["w1"], [hub_change("http.default_timeout", "5s")]),
        "notifications.md": card_worker_payload(WORKERS["w2"], [hub_change("http.default_timeout", "10s")]),
        "pricing-engine.md": card_worker_payload(WORKERS["w3"]),
    })


def row(arm, run=1, fail=None, **kwargs):
    return run_row(run=run, backend_name="scripted", worker_model="w", reducer_model="r", arm=arm, backend=ScriptedBackend(script()),
                   fail_worker=fail, ask=lambda _: "a", show=lambda _: None, **kwargs)


def write(path, rows):
    path.write_text("".join(json.dumps(item) + "\n" for item in rows))


def test_summary_has_one_line_per_arm_and_the_human_table(tmp_path):
    rows = [row(arm, fail="w3" if arm == "first-wins" else None) for arm in ARMS]
    write(tmp_path / "runs.jsonl", rows)
    table = experiment.summarise(tmp_path / "runs.jsonl")
    for arm in ARMS:
        assert f"| {arm} |" in table
    assert "Human decisions, one row per conflict" in table and "http.default_timeout" in table
    assert "| first-wins | 1 |" in table and "silent success" in table


def test_summary_refuses_mixed_models_and_backends(tmp_path):
    first, second = row("first-wins"), row("first-wins")
    write(tmp_path / "models.jsonl", [first, {**second, "reducer_model": "other"}])
    with pytest.raises(ValueError, match="mixes models or backends"):
        experiment.summarise(tmp_path / "models.jsonl")
    write(tmp_path / "backend.jsonl", [first, {**second, "backend": "anthropic-sdk"}])
    with pytest.raises(ValueError, match="mixes"):
        experiment.summarise(tmp_path / "backend.jsonl")
    write(tmp_path / "served.jsonl", [first, {**second, "worker_served_models": ["claude-haiku-4-5"]}])
    with pytest.raises(ValueError, match="mixes"):
        experiment.summarise(tmp_path / "served.jsonl")


def test_run_arms_writes_rows_with_the_failure_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: ScriptedBackend(script()))
    out = tmp_path / "runs.jsonl"
    experiment.run_arms(2, ["first-wins"], out, "scripted", "r", "w")
    rows = experiment.load_rows(out)
    assert [item["fail_worker"] for item in rows] == ["w1", None]
    assert [item["run"] for item in rows] == [1, 2]
