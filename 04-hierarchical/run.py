"""Run one paired index (workers once, then every arm) and print what each tier sent, what the platform lead decided and the score.

Single runs are for looking, not for the benchmark: output goes to a scratch file, never to results/runs.jsonl.
"""

import argparse
import json
import os
from pathlib import Path

from hier import run_pair
from hier_replay import load_replay
from hier_config import ARMS, LEAD_MODEL, WORKER_MODEL
from hier_prompts import LEAD_CONTRACTS
from topologies.model import backend_from_name

BENCHMARK_FILE = Path(__file__).resolve().parent / "results" / "runs.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--worker-model", default=WORKER_MODEL)
    parser.add_argument("--lead-model", default=LEAD_MODEL)
    parser.add_argument("--run", type=int, default=1, help="run index, which sets the arm order")
    parser.add_argument("--parallel-arms", action="store_true")
    parser.add_argument("--lead-contract", choices=LEAD_CONTRACTS, default="full", help="blind withholds CONTRACT rule 8 from the sub-leads and the platform lead")
    parser.add_argument("--replay-workers", type=Path, default=None, help="reuse the worker replies of this runs.jsonl for --run (no worker call)")
    parser.add_argument("--out", type=Path, default=None, help="append the full run records to this scratch JSONL file")
    parser.add_argument("--json", action="store_true", help="print the full run records as JSON")
    return parser.parse_args()


def request_text(item: dict) -> str:
    harm = ", ".join(f"{h['need'].split('.')[0]} ({'inside' if h['inside'] else 'outside'})" for h in item["harms"]) or "harmless"
    return f"{item['from']}: {item['key']}={item['value']} [{harm}]"


def describe(row: dict) -> str:
    score = row["score"]
    lines = [f"== {row['arm']} (run {row['run']}) [{row['lead_contract']} leads]"]
    for lead in row["sub_leads"]:
        flag = f" [{lead['fallback']}]" if lead["fallback"] else ""
        lines.append(f"sub-lead {lead['group']}: {len(lead['requests'])} requests{flag}")
    lines.append("requests up: " + ("; ".join(request_text(item) for item in row["requests_up"]) or "none"))
    top = row["top"]
    decided = ", ".join(f"{key}={item['value']}" for key, item in top["decisions"].items()) or ("not called" if not top["called"] else "no decision")
    grants = ", ".join(f"{g['service']} {g['key']}={g['value']}" for g in top["grants"]) or "none"
    lines.append(f"platform lead: {decided}; overrides granted: {grants}" + (f"; error: {top['error']}" if top["error"] else ""))
    lines.append(
        f"fixed {score['risks_fixed']}/{score['risks_total']}, regressions {score['regressions']} {score['regressed']}, "
        f"outside contract {score['outside_contract']}; ladder {row['ladder']}"
    )
    lines.append(f"tokens {row['total_tokens']} {row['tokens_by_tier']}; management call seconds {row['management_call_seconds']}; served workers {row['worker_served_primary']} leads {row['lead_served_primary']}")
    if row["call_errors"] or row["model_mismatches"] or row["unreadable_decisions"]:
        lines.append(f"PROBLEMS call errors {row['call_errors']}; model mismatches {row['model_mismatches']}; unreadable decisions {row['unreadable_decisions']}")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if args.out and args.out.resolve() == BENCHMARK_FILE:
        raise SystemExit("run.py never writes results/runs.jsonl: that file is for experiment.py. Pick a scratch file for --out.")
    backend_name = args.backend or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    replay = load_replay(args.replay_workers) if args.replay_workers else None
    rows = run_pair(
        run=args.run,
        backend=backend_from_name(backend_name),
        backend_name=backend_name,
        worker_model=replay.worker_model if replay else args.worker_model,
        lead_contract=args.lead_contract,
        workers=replay.workers_for(args.run) if replay else None,
        workers_replayed_from=replay.source() if replay else None,
        lead_model=args.lead_model,
        arms=args.arms,
        parallel_arms=args.parallel_arms,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as handle:
            handle.writelines(json.dumps(row) + "\n" for row in rows)
    first = rows[0]
    print(f"workers {'replayed from ' + first['workers_replayed_from']['path'] if first['workers_replayed_from'] else str(first['wall_seconds_workers']) + 's'}: " + ", ".join(f"{w['service']} {w['status']}" for w in first["workers"]))
    print(json.dumps(rows, indent=2) if args.json else "\n\n".join(describe(row) for row in rows))


if __name__ == "__main__":
    main()
