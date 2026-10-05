"""Run one fan-out under one arm and print the conflicts, the score and the merge report.

Single runs are for looking, not for the benchmark: output goes to a scratch file, never to results/runs.jsonl.
"""

import argparse
import json
import os
from pathlib import Path

from fanout_config import ARMS, REDUCER_MODEL, WORKER_MODEL, WORKERS
from fanout import run_row
from topologies.model import backend_from_name

BENCHMARK_FILE = Path(__file__).resolve().parent / "results" / "runs.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=ARMS, default="first-wins")
    parser.add_argument("--fail-worker", choices=list(WORKERS), default=None, help="make this worker return an invalid payload after its retry")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--reducer-model", default=REDUCER_MODEL)
    parser.add_argument("--worker-model", default=WORKER_MODEL)
    parser.add_argument("--out", type=Path, default=None, help="append the full run record to this scratch JSONL file")
    parser.add_argument("--keep", type=Path, default=None, help="keep the scratch repo and worktrees here")
    parser.add_argument("--json", action="store_true", help="print the full run record as JSON")
    return parser.parse_args()


def describe(row: dict) -> str:
    score = row["score"]
    lines = [f"workers: {', '.join(f'{w['worker']} {w['status']}' for w in row['workers'])}; commit order {row['commit_order']}"]
    lines.append(f"hub keys changed by 2+ workers: {row['shared_keys']}; git textual conflicts: {len(row['textual_conflicts'])}")
    for item in row["conflicts"]:
        options = "; ".join(f"{o['value']} ({','.join(o['workers'])})" for o in item["options"])
        lines.append(f"conflict {item['key']}: {options} -> {item['value']} [{item['decided_by']}]")
    if not row["conflicts"]:
        lines.append("conflicts: none")
    lines.append(
        f"fixed {score['risks_fixed']}/{score['risks_total']}, regressions {score['regressions']} {score['regressed']}, "
        f"outside contract {score['outside_contract']} {score['outside_contract_ids']}"
    )
    lines.append(f"tokens {row['total_tokens']} {row['tokens_by_role']}; wall {row['wall_seconds']}s; served workers {row['worker_served_models']} reducer {row['reducer_served_models']}")
    if row["fail_worker"]:
        lines.append(f"failed services {row['failed_services']}; named in the report: {row['named_failed_services']}; silent success: {row['silent_success']}")
    lines.append(f"\n{row['report']}")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if args.out and args.out.resolve() == BENCHMARK_FILE:
        raise SystemExit("run.py never writes results/runs.jsonl: that file is for experiment.py. Pick a scratch file for --out.")
    backend_name = args.backend or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    row = run_row(
        run=1,
        backend_name=backend_name,
        worker_model=args.worker_model,
        reducer_model=args.reducer_model,
        arm=args.arm,
        backend=backend_from_name(backend_name),
        fail_worker=args.fail_worker,
        workdir=args.keep,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
    print(json.dumps(row, indent=2) if args.json else describe(row))


if __name__ == "__main__":
    main()
