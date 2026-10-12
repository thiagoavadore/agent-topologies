import json

import pytest
from hier_helpers import Script, asking_workers, forward, protective_top, stable_commit  # noqa: F401
from test_hier_blind import published, rule8_top, script, write

import experiment
import run as run_module
from hier import run_pair
from hier_config import ARMS
from hier_replay import load_replay
from topologies.model import ScriptedBackend


def blind_rows(path, runs=(1, 2), top=rule8_top):
    replay = load_replay(path)
    return [
        row
        for run in runs
        for row in run_pair(
            run=run, backend=ScriptedBackend(script(top)), backend_name="scripted", worker_model="w", lead_model="l", lead_contract="blind",
            workers=replay.workers_for(run), workers_replayed_from=replay.source(),
        )
    ]


def test_compare_pairs_rows_and_counts_flips(tmp_path):
    path, _ = published(tmp_path, runs=(1, 2))
    write(tmp_path / "blind.jsonl", blind_rows(path))
    table = experiment.compare(tmp_path / "blind.jsonl", path)
    by_arm = {line.split("|")[1].strip(): [cell.strip() for cell in line.split("|")[2:-1]] for line in table.splitlines() if line.startswith("| ") and "arm" not in line[:8]}
    # by-runtime: the published (rule 8) top keeps 300ms, the blind one raises it and breaks pricing in both runs
    assert by_arm["by-runtime"][1:6] == ["2 of 2", "0 of 2", "0 of 2", "2 of 2", "2 (run 1, 2)"]
    assert by_arm["flat"][1:6] == ["0 of 2", "0 of 2", "2 of 2", "2 of 2", "0"]
    assert float(by_arm["flat"][-1]) > 0 and "worker replies identical" in table


def test_compare_refuses_unpaired_files(tmp_path):
    path, rows = published(tmp_path, runs=(1, 2))
    blind = blind_rows(path)
    write(tmp_path / "blind.jsonl", blind)
    write(tmp_path / "short.jsonl", rows[:-1])
    with pytest.raises(ValueError, match="do not cover the same"):
        experiment.compare(tmp_path / "blind.jsonl", tmp_path / "short.jsonl")
    other_workers = [{**row, "workers": [{**row["workers"][0], "notes": "different"}, *row["workers"][1:]]} if row["run"] == 2 else row for row in rows]
    write(tmp_path / "other.jsonl", other_workers)
    with pytest.raises(ValueError, match="worker replies differ"):
        experiment.compare(tmp_path / "blind.jsonl", tmp_path / "other.jsonl")


def test_compare_to_writes_compare_md_next_to_the_summary(tmp_path, monkeypatch, capsys):
    path, _ = published(tmp_path, runs=(1,))
    out = tmp_path / "blind" / "runs.jsonl"
    out.parent.mkdir()
    write(out, blind_rows(path, runs=(1,)))
    monkeypatch.setattr("sys.argv", ["experiment.py", "--lead-contract", "blind", "--summarise-only", "--out", str(out), "--summary", str(out.parent / "summary.md"), "--compare-to", str(path)])
    experiment.main()
    assert (tmp_path / "blind" / "summary.md").exists() and (tmp_path / "blind" / "compare.md").exists()
    assert "Paired comparison" in capsys.readouterr().out


def test_summary_refuses_duplicate_run_arm_rows(tmp_path):
    path, rows = published(tmp_path, runs=(1,))
    write(tmp_path / "dup.jsonl", [*rows, rows[0]])
    with pytest.raises(ValueError, match="duplicate"):
        experiment.summarise(tmp_path / "dup.jsonl")


def test_run_py_guards_the_blind_file_and_a_disagreeing_worker_model(tmp_path, monkeypatch):
    path, _ = published(tmp_path, runs=(1,))
    monkeypatch.setattr(run_module, "backend_from_name", lambda name: pytest.fail("a backend was built"))
    monkeypatch.setattr("sys.argv", ["run.py", "--out", str(run_module.BENCHMARK_FILE.parent / "blind" / "runs.jsonl")])
    with pytest.raises(SystemExit, match="never writes"):
        run_module.main()
    monkeypatch.setattr("sys.argv", ["run.py", "--replay-workers", str(path), "--out", str(tmp_path / "s.jsonl")])
    with pytest.raises(SystemExit, match="disagrees"):
        run_module.main()


def test_blind_benchmark_needs_replay_workers_and_an_agreeing_worker_model(tmp_path, monkeypatch):
    path, _ = published(tmp_path, runs=(1,))
    monkeypatch.setattr(experiment, "refuse_unregistered", lambda contract="full": None)
    monkeypatch.setattr(experiment, "backend_from_name", lambda name: pytest.fail("a backend was built"))
    monkeypatch.setattr("sys.argv", ["experiment.py", "--lead-contract", "blind", "--out", str(tmp_path / "blind.jsonl")])
    with pytest.raises(SystemExit, match="without --replay-workers"):
        experiment.main()
    monkeypatch.setattr("sys.argv", ["experiment.py", "--lead-contract", "blind", "--replay-workers", str(path), "--out", str(tmp_path / "blind.jsonl")])
    with pytest.raises(SystemExit, match="disagrees"):
        experiment.main()
    assert not (tmp_path / "blind.jsonl").exists()


def test_replay_refuses_arm_rows_of_one_index_with_different_workers(tmp_path):
    path, rows = published(tmp_path, runs=(1,))
    tampered = [rows[0], {**rows[1], "workers": [{**rows[1]["workers"][0], "notes": "different"}, *rows[1]["workers"][1:]]}]
    write(tmp_path / "tampered.jsonl", tampered)
    with pytest.raises(ValueError, match="do not carry the same workers"):
        load_replay(tmp_path / "tampered.jsonl")
    assert json.loads(path.read_text().splitlines()[0])["workers"]
