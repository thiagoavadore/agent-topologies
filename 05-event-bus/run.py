"""Run one arm of the event-bus experiment once and print the faults, every delivery, the owner's actions and the score.

Single runs are for looking, not for the benchmark: output goes to a scratch file, never to results/runs.jsonl.
"""

import argparse
import json
import os
from pathlib import Path

from bus import run_row
from bus_config import ARMS, CONSUMER_MODEL, OWNER_MODEL
from topologies.model import backend_from_name

BENCHMARK_FILE = Path(__file__).resolve().parent / "results" / "runs.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=ARMS, default="platform-owned")
    parser.add_argument("--run-index", type=int, default=0, help="0-based run whose fault schedule to use")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--consumer-model", default=CONSUMER_MODEL)
    parser.add_argument("--owner-model", default=OWNER_MODEL)
    parser.add_argument("--out", type=Path, default=None, help="append the full run record to this scratch JSONL file")
    parser.add_argument("--keep", type=Path, default=None, help="keep the scratch repo here")
    parser.add_argument("--json", action="store_true", help="print the full run record as JSON")
    return parser.parse_args()


def describe(row: dict) -> str:
    score, truth = row["score"], row["truth"]
    lines = [f"arm {row['arm']}, run index {row['run_index']}; faults: " + ", ".join(f"{kind} {risk}" for kind, risk in row["faults"].items())]
    lines.append(f"wrong-service target moved to {row['wrong_destination']}")
    for item in row["published"]:
        lines.append(f"published {item['message_id']} {item['risk_id']}: {item['route']}")
    for item in row["deliveries"]:
        note = " (duplicate)" if item["duplicate"] else ""
        lines.append(
            f"pass {item['pass_no']} {item['team']} <- {item['message_id']} {item['risk_id']} attempt {item['attempt']}{note}: "
            f"{item['outcome']}, files {sorted(item['files'])}, {item['tokens']} tokens, {item['wall_seconds']}s"
        )
    owner = row["owner"]
    lines.append(f"owner called: {owner['called']}, {owner['tokens']} tokens, prompt {owner['prompt_chars']} chars, error: {owner['error'] or 'none'}")
    lines += [f"  action {item['action']} {item.get('message_id', '')} {item.get('service', '')} {item.get('team', '')} -> {item['result']}" for item in owner["action_log"]]
    lines.append(f"fixed {score['risks_fixed']}/{score['risks_total']}, regressions {score['regressions']} {score['regressed']}, outside contract {score['outside_contract']} {score['outside_contract_ids']}")
    lines.append(f"fault outcomes {row['fault_outcomes']}; fleet-telemetry fixed {row['fleet_fixed']} of 2; clean fixed {row['clean_fixed']} of {row['clean_total']}")
    lines.append(f"duplicate {row['duplicate']}; wrong-service {row['wrong_service']}")
    lines.append(f"unfixed causes: { {risk: item['cause'] for risk, item in truth.items() if not item['fixed']} }")
    for name in ("investigator", "investigator_no_trace"):
        if row[name]:
            item = row[name]["score"]
            lines.append(f"{name}: correct {item['correct']} of {item['channel_losses']} channel losses, false assurance {item['false_assurance_channel']}, missing {len(item['missing_risks'])}, error: {row[name]['error'] or 'none'}")
    lines.append(f"tokens {row['total_tokens']} {row['tokens_by_role']}; consumer calls {row['consumer_calls']}; wall {row['wall_seconds']}s")
    lines.append(f"served: consumers {row['consumer_served_models']}, owner roles {row['owner_served_primary']}")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if args.out and args.out.resolve() == BENCHMARK_FILE:
        raise SystemExit("run.py never writes results/runs.jsonl: that file is for experiment.py. Pick a scratch file for --out.")
    backend_name = args.backend or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    row = run_row(
        run=1, run_index=args.run_index, backend_name=backend_name, arm=args.arm, backend=backend_from_name(backend_name),
        consumer_model=args.consumer_model, owner_model=args.owner_model, workdir=args.keep,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
    print(json.dumps(row, indent=2) if args.json else describe(row))


if __name__ == "__main__":
    main()
