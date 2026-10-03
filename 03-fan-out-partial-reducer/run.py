"""Run one fan-out under one arm and print the conflicts, the score and the merge report."""

import argparse
import json
import os
from pathlib import Path

from fanout import ARMS, WORKERS, run_row
from topologies.model import backend_from_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=ARMS, default="first-wins")
    parser.add_argument("--fail-worker", choices=list(WORKERS), default=None, help="make this worker return an invalid payload after its retry")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--reducer-model", default="claude-opus-5-5")
    parser.add_argument("--worker-model", default="claude-sonnet-5-5")
    parser.add_argument("--out", type=Path, default=None, help="append the full run record to this JSONL file")
    parser.add_argument("--keep", type=Path, default=None, help="keep the scratch repo and worktrees here (the directory must not exist)")
    parser.add_argument("--json", action="store_true", help="print the full run record as JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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
    if args.json:
        print(json.dumps(row, indent=2))
        return
    score = row["score"]
    print(f"\nworkers: {', '.join(f'{w['worker']} {w['status']}' for w in row['workers'])}; commit order {row['commit_order']}")
    print(f"shared hub keys: {row['shared_keys']}; textual conflicts: {len(row['textual_conflicts'])}")
    for item in row["conflicts"]:
        options = "; ".join(f"{o['value']} ({','.join(o['workers'])})" for o in item["options"])
        print(f"conflict {item['key']}: {options} -> {item['value']} [{item['decided_by']}]")
    print(f"fixed {score['risks_fixed']}/{score['risks_total']}, regressed {score['regressed']}, tokens {row['total_tokens']} {row['tokens_by_role']}")
    if score["unfixed"]:
        print(f"unfixed: {', '.join(score['unfixed'])}")
    print(f"\n{row['summary']}")


if __name__ == "__main__":
    main()
