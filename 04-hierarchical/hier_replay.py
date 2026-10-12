"""Load the worker replies of a published run so another variant can reuse them without calling a worker."""

import json
from dataclasses import dataclass
from pathlib import Path

from hier import WorkerResult


@dataclass(frozen=True)
class Replay:
    path: str
    code_commit: str
    worker_model: str
    workers_by_run: dict[int, list[WorkerResult]]

    def source(self) -> dict:
        return {"path": self.path, "code_commit": self.code_commit}

    def workers_for(self, run: int) -> list[WorkerResult]:
        if run not in self.workers_by_run:
            raise ValueError(f"{self.path} has no run index {run} (it has {sorted(self.workers_by_run)})")
        return self.workers_by_run[run]


def load_replay(path: Path) -> Replay:
    """Worker results per run index from a runs.jsonl; refuses a source that mixes code commits or worker models."""
    if not path.exists():
        raise ValueError(f"replay source {path} does not exist")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"replay source {path} has no rows")
    commits, models = {row["code_commit"] for row in rows}, {row["worker_model"] for row in rows}
    if len(commits) > 1 or len(models) > 1:
        raise ValueError(f"replay source {path} mixes code commits {sorted(commits)} or worker models {sorted(models)}")
    by_run: dict[int, list[WorkerResult]] = {}
    for row in rows:
        by_run.setdefault(row["run"], [WorkerResult(**worker) for worker in row["workers"]])
    return Replay(path=str(path), code_commit=commits.pop(), worker_model=models.pop(), workers_by_run=by_run)
