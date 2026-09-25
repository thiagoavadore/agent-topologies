"""Run the three arms n times each, interleaved, append every run to a JSONL file, and print the table.

Arms:
  uncapped      the router's plan runs as is
  cap2          fan-out cap of 2 workers
  router-killed router skipped, deterministic fallback into 2 workers (isolates what the router buys)
"""

import argparse
import datetime as dt
import json
import statistics
import time
from pathlib import Path

from orchestrator import Guards, run_review
from scoring import load_planted, score
from topologies.model import backend_from_name

HERE = Path(__file__).parent
ARMS = {
    "uncapped": {"guards": Guards(fan_out_cap=None, token_ceiling=None), "break_router": False},
    "cap2": {"guards": Guards(fan_out_cap=2, token_ceiling=None), "break_router": False},
    "router-killed": {"guards": Guards(fan_out_cap=2, token_ceiling=None), "break_router": True},
}


def run_arms(n: int, arms: list[str], out: Path, backend_name: str | None, supervisor_model: str, worker_model: str) -> None:
    backend = backend_from_name(backend_name)
    planted = load_planted()
    out.parent.mkdir(parents=True, exist_ok=True)
    for index in range(n):
        for arm in arms:
            config = ARMS[arm]
            started = time.monotonic()
            record = run_review(HERE / "fixtures" / "services", backend, supervisor_model, worker_model, config["guards"], config["break_router"])
            row = {
                "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "arm": arm,
                "run": index + 1,
                "backend": backend_name or "claude-cli",
                "supervisor_model": supervisor_model,
                "worker_model": worker_model,
                "wall_seconds": round(time.monotonic() - started, 1),
                "router_status": record.router_status,
                "planned_subtasks": record.planned_subtasks,
                "dispatched_workers": record.dispatched_workers,
                "tokens_by_role": record.tokens_by_role,
                "total_tokens": record.total_tokens,
                "invalid_payloads": sum(1 for worker in record.workers if worker["status"] != "ok"),
                "retries": sum(worker["attempts"] - 1 for worker in record.workers if worker["attempts"]),
                "out_of_brief": sum(worker["out_of_brief"] for worker in record.workers),
                "workers": [{key: worker[key] for key in ("subtask_id", "files", "focus", "status", "tokens")} | {"findings": len(worker["findings"])} for worker in record.workers],
                "score": score(record.findings, planted),
            }
            with out.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            print(f"== {arm} run {index + 1}: {row['total_tokens']} tokens, recall {row['score']['planted_found']}/{row['score']['planted']}, {row['dispatched_workers']} workers", flush=True)


def summarise(path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    lines = [
        "| arm | n | workers (planned → dispatched) | total tokens, mean (min to max) | router + synthesis tokens, mean | recall, mean (min) | extra findings, mean | invalid payloads | wall s, mean |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        runs = [row for row in rows if row["arm"] == arm]
        if not runs:
            continue
        tokens = [row["total_tokens"] for row in runs]
        overhead = [row["tokens_by_role"]["router"] + row["tokens_by_role"]["synthesis"] for row in runs]
        recalls = [row["score"]["recall"] for row in runs]
        planned = sorted({row["planned_subtasks"] for row in runs})
        dispatched = sorted({row["dispatched_workers"] for row in runs})
        lines.append(
            f"| {arm} | {len(runs)} | {'/'.join(map(str, planned))} → {'/'.join(map(str, dispatched))} "
            f"| {statistics.mean(tokens):,.0f} ({min(tokens):,} to {max(tokens):,}) "
            f"| {statistics.mean(overhead):,.0f} "
            f"| {statistics.mean(recalls):.2f} ({min(recalls):.2f}) "
            f"| {statistics.mean(row['score']['extra_findings'] for row in runs):.1f} "
            f"| {sum(row['invalid_payloads'] for row in runs)} "
            f"| {statistics.mean(row['wall_seconds'] for row in runs):.0f} |"
        )
    models = sorted({f"{row['supervisor_model']} / {row['worker_model']} via {row['backend']}" for row in rows})
    dates = sorted({row["at"][:10] for row in rows})
    lines.append(f"\nSupervisor / worker models: {', '.join(models)}. Run dates: {', '.join(dates)}.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    parser.add_argument("--out", type=Path, default=HERE / "results" / "runs.jsonl")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--supervisor-model", default="claude-opus-5")
    parser.add_argument("--worker-model", default="claude-haiku-4-5")
    parser.add_argument("--summarise-only", action="store_true")
    args = parser.parse_args()
    if not args.summarise_only:
        run_arms(args.n, args.arms, args.out, args.backend, args.supervisor_model, args.worker_model)
    print(summarise(args.out))


if __name__ == "__main__":
    main()
