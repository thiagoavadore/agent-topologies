import json

import pytest
from hier_helpers import Script, asking_workers, forward, protective_top

import experiment
import run as run_module
from hier import run_pair
from hier_config import ARMS
from topologies.model import ScriptedBackend


def script() -> Script:
    return Script(workers=asking_workers(), sub_leads={"python": forward(("http.default_timeout", "15s", "charges take up to 12 s"))}, top=protective_top)


def rows_for(runs=(1, 2)) -> list[dict]:
    return [row for run in runs for row in run_pair(run=run, backend=ScriptedBackend(script()), backend_name="scripted", worker_model="w", lead_model="l")]


def write(path, rows):
    path.write_text("".join(json.dumps(item) + "\n" for item in rows))


def point_at(monkeypatch, tmp_path, draft="Frozen.", predictions="## 04-hierarchical: should the layers match?"):
    monkeypatch.setattr(experiment, "DRAFT_FILE", tmp_path / "predictions-draft.md")
    monkeypatch.setattr(experiment, "PREDICTIONS", tmp_path / "PREDICTIONS.md")
    for path, text in ((experiment.DRAFT_FILE, draft), (experiment.PREDICTIONS, predictions)):
        if text is not None:
            path.write_text(text)


def test_experiment_refuses_draft(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path, draft=None)
    with pytest.raises(SystemExit, match="missing"):
        experiment.refuse_unregistered()
    point_at(monkeypatch, tmp_path, draft="**DRAFT. Not frozen.**")
    with pytest.raises(SystemExit, match="DRAFT"):
        experiment.refuse_unregistered()
    point_at(monkeypatch, tmp_path, predictions="## 05-event-bus: who looks after the channel?")
    with pytest.raises(SystemExit, match="no section"):
        experiment.refuse_unregistered()
    point_at(monkeypatch, tmp_path, predictions=None)
    with pytest.raises(SystemExit, match="no section"):
        experiment.refuse_unregistered()
    point_at(monkeypatch, tmp_path)
    experiment.refuse_unregistered()


def test_main_refuses_before_any_model_call(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path, draft="DRAFT")
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: pytest.fail("a backend was built"))
    monkeypatch.setattr("sys.argv", ["experiment.py", "--out", str(tmp_path / "runs.jsonl")])
    with pytest.raises(SystemExit, match="DRAFT"):
        experiment.main()
    assert not (tmp_path / "runs.jsonl").exists()


def test_the_real_draft_still_blocks_the_benchmark():
    assert experiment.DRAFT_FILE.name == "predictions-draft.md" and experiment.DRAFT_FILE.exists()
    assert experiment.PREDICTIONS.name == "PREDICTIONS.md" and experiment.HEADING == "## 04-hierarchical:"


def test_run_pairs_appends_one_row_per_arm_and_run(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: ScriptedBackend(script()))
    out = tmp_path / "runs.jsonl"
    experiment.run_pairs(2, list(ARMS), out, "scripted", "w", "l")
    rows = experiment.load_rows(out)
    assert [row["run"] for row in rows] == [1] * 4 + [2] * 4 and len({row["arm"] for row in rows}) == 4


def test_token_ceiling_skips_the_rest_by_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: ScriptedBackend(script()))
    out = tmp_path / "runs.jsonl"
    experiment.run_pairs(3, list(ARMS), out, "scripted", "w", "l", token_ceiling=1)
    assert len(experiment.load_rows(out)) == 4
    assert "Not done: run 2" in capsys.readouterr().out


def test_summary_renders(tmp_path):
    write(tmp_path / "runs.jsonl", rows_for())
    table = experiment.summarise(tmp_path / "runs.jsonl")
    for arm in ARMS:
        assert f"| {arm} | 2 |" in table
    assert "Coupling ladder" in table and "| by-runtime | pricing-engine |" in table
    assert "| 1 | 2 |" in table and "15s / none / pricing-engine" in table
    assert "Workers w, leads l" in table


def test_summary_refuses_mixed_setups(tmp_path):
    first, second = rows_for(runs=(1,))[0], rows_for(runs=(2,))[0]
    for name, change in {
        "worker.jsonl": {"worker_model": "other"},
        "lead.jsonl": {"lead_model": "other"},
        "backend.jsonl": {"backend": "anthropic-sdk"},
        "served.jsonl": {"worker_served_primary": ["claude-haiku-4-5"]},
    }.items():
        write(tmp_path / name, [first, {**second, **change}])
        with pytest.raises(ValueError, match="mixes"):
            experiment.summarise(tmp_path / name)
    write(tmp_path / "served-lead.jsonl", [{**first, "lead_served_primary": ["a"]}, {**second, "lead_served_primary": ["b"]}])
    with pytest.raises(ValueError, match="mixes"):
        experiment.summarise(tmp_path / "served-lead.jsonl")


def test_pair_tokens_counts_shared_calls_once():
    rows = rows_for(runs=(1,))
    by_arm = {row["arm"]: row for row in rows}
    expected = rows[0]["tokens_by_tier"]["workers"] + sum(r["tokens_by_tier"]["top"] for r in rows)
    expected += by_arm["by-team"]["tokens_by_tier"]["sub_leads"] + by_arm["by-runtime"]["tokens_by_tier"]["sub_leads"]
    assert experiment.pair_tokens(rows) == expected


def test_run_py_prints_every_arm_and_never_writes_the_benchmark_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_module, "backend_from_name", lambda name: ScriptedBackend(script()))
    scratch = tmp_path / "scratch.jsonl"
    monkeypatch.setattr("sys.argv", ["run.py", "--out", str(scratch)])
    run_module.main()
    printed = capsys.readouterr().out
    assert all(f"== {arm} (run 1)" in printed for arm in ARMS) and "platform lead:" in printed and "ladder" in printed
    assert [json.loads(line)["arm"] for line in scratch.read_text().splitlines()] == [row for row in ARMS]
    monkeypatch.setattr("sys.argv", ["run.py", "--out", str(run_module.BENCHMARK_FILE)])
    with pytest.raises(SystemExit, match="never writes"):
        run_module.main()
