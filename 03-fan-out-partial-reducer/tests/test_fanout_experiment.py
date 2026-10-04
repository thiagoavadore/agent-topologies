import json

import pytest
from fanout_helpers import W1, W2, W3, Script, colliding_payloads

import experiment
import run as run_module
from fanout import run_row
from fanout_config import ARMS, AUTOMATED_ARMS, WORKERS
from topologies.model import ScriptedBackend


def test_failure_schedule_rounds_toward_failed_and_rotates_workers():
    assert experiment.failing_run_indexes("first-wins", 10) == [0, 2, 4, 6, 8]
    assert experiment.failing_run_indexes("hub-owner", 5) == [0, 2, 4]
    assert experiment.failing_run_indexes("human-decides", 3) == [1]
    assert experiment.failing_run_indexes("human-decides", 5) == [1, 3]
    assert [experiment.failing_worker(rank) for rank in range(5)] == [W1, W2, W3, W1, W2]


def test_arm_order_rotates_per_run():
    arms = list(AUTOMATED_ARMS)
    assert [experiment.rotated(arms, i)[0] for i in range(4)] == [arms[0], arms[1], arms[2], arms[0]]
    assert sorted(experiment.rotated(arms, 1)) == sorted(arms)


def row(arm, run=1, fail=None, **kwargs):
    script = Script(colliding_payloads())
    return run_row(run=run, backend_name="scripted", worker_model="w", reducer_model="r", arm=arm, backend=ScriptedBackend(script),
                   fail_worker=fail, ask=lambda _: "a", show=lambda _: None, **kwargs)


def write(path, rows):
    path.write_text("".join(json.dumps(item) + "\n" for item in rows))


def test_summary_has_one_line_per_arm_the_key_table_and_the_human_table(tmp_path):
    rows = [row(arm, fail=W3 if arm == "first-wins" else None) for arm in ARMS]
    write(tmp_path / "runs.jsonl", rows)
    table = experiment.summarise(tmp_path / "runs.jsonl")
    for arm in ARMS:
        assert f"| {arm} | 1 |" in table
    assert "Human decisions, one row per conflict" in table and "| http.default_timeout | 1 of 1 | 1 of 1 | 1 of 1 | 1 of 1 |" in table
    assert "silent success" in table and "Workers w (served: not reported)" in table


def test_summary_refuses_mixed_models_and_backends(tmp_path):
    first, second = row("first-wins"), row("first-wins")
    write(tmp_path / "models.jsonl", [first, {**second, "reducer_model": "other"}])
    with pytest.raises(ValueError, match="mixes models or backends"):
        experiment.summarise(tmp_path / "models.jsonl")
    write(tmp_path / "backend.jsonl", [first, {**second, "backend": "anthropic-sdk"}])
    with pytest.raises(ValueError, match="mixes"):
        experiment.summarise(tmp_path / "backend.jsonl")
    write(tmp_path / "served.jsonl", [first, {**second, "worker_served_primary": ["claude-haiku-4-5"]}])
    with pytest.raises(ValueError, match="mixes"):
        experiment.summarise(tmp_path / "served.jsonl")


def test_run_arms_writes_rows_with_the_failure_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: ScriptedBackend(Script(colliding_payloads())))
    out = tmp_path / "runs.jsonl"
    experiment.run_arms(3, ["first-wins"], out, "scripted", "r", "w")
    rows = experiment.load_rows(out)
    assert [item["fail_worker"] for item in rows] == [W1, None, W2]
    assert [item["run"] for item in rows] == [1, 2, 3]


def test_token_ceiling_skips_the_rest_by_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: ScriptedBackend(Script(colliding_payloads())))
    out = tmp_path / "runs.jsonl"
    experiment.run_arms(3, ["first-wins", "hub-owner"], out, "scripted", "r", "w", token_ceiling=1)
    assert len(experiment.load_rows(out)) == 1
    printed = capsys.readouterr().out
    assert "Not done: hub-owner run 1" in printed and "first-wins run 3" in printed


def test_benchmark_refuses_a_draft_prediction_file(tmp_path):
    draft = tmp_path / "PREDICTIONS.md"
    draft.write_text("> **DRAFT, awaiting approval.**")
    with pytest.raises(SystemExit, match="DRAFT"):
        experiment.refuse_unregistered(draft)
    with pytest.raises(SystemExit, match="missing"):
        experiment.refuse_unregistered(tmp_path / "none.md")
    draft.write_text("Approved predictions.")
    experiment.refuse_unregistered(draft)


def test_main_refuses_before_any_model_call_while_predictions_are_draft(tmp_path, monkeypatch):
    (tmp_path / "PREDICTIONS.md").write_text("DRAFT")
    monkeypatch.setattr(experiment, "PREDICTIONS", tmp_path / "PREDICTIONS.md")
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: pytest.fail("a backend was built"))
    monkeypatch.setattr("sys.argv", ["experiment.py", "--out", str(tmp_path / "runs.jsonl")])
    with pytest.raises(SystemExit, match="DRAFT"):
        experiment.main()
    assert not (tmp_path / "runs.jsonl").exists()


def test_the_real_predictions_file_is_checked_by_default():
    assert experiment.PREDICTIONS.name == "PREDICTIONS.md" and experiment.PREDICTIONS.exists()


def test_run_py_stays_allowed_with_a_draft_but_never_writes_the_benchmark_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_module, "backend_from_name", lambda name: ScriptedBackend(Script(colliding_payloads())))
    scratch = tmp_path / "scratch.jsonl"
    monkeypatch.setattr("sys.argv", ["run.py", "--arm", "first-wins", "--fail-worker", W1, "--out", str(scratch)])
    run_module.main()
    printed = capsys.readouterr().out
    assert "fixed " in printed and "failed services" in printed
    assert json.loads(scratch.read_text())["arm"] == "first-wins"
    monkeypatch.setattr("sys.argv", ["run.py", "--out", str(run_module.BENCHMARK_FILE)])
    with pytest.raises(SystemExit, match="never writes"):
        run_module.main()
