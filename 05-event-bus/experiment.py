"""Run the three ownership arms n times, append every run to results/runs.jsonl and write results/summary.md.

Arms, who looks after the shared channel between a scanner and six team consumers:
  no-owner        nobody: at-most-once delivery, no schema, teams keep local logs
  steward-agent   an Opus agent reads the whole Message Store once and acts; the channel itself guarantees nothing
  platform-owned  the platform enforces schema, acks with one redelivery, an Idempotent Receiver and a Dead Letter Channel,
                  keeps Correlation Identifiers and Message History; its Opus agent sees only the exceptions

Run i injects the same four faults in every arm (malformed, wrong-service, consumer-crash, duplicate), on four distinct
routable risks. fleet-telemetry has no owner in the fixture, so its two messages are unroutable in every run.
Arm order rotates per run. Benchmark runs are refused while the predictions are a draft or not yet in PREDICTIONS.md.
"""

import argparse
import json
import os
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bus import run_row
from bus_config import ARMS, CONSUMER_MODEL, LOSS_FAULT_KINDS, OWNER_MODEL
from topologies.guards import SkippedWork, describe_skipped, may_start_group
from topologies.harbour import RISKS
from topologies.model import backend_from_name

HERE = Path(__file__).parent
ROOT = HERE.parent
DRAFT_FILE = ROOT / "docs" / "specs" / "spec-issue-15-04-05" / "predictions-draft.md"
PREDICTIONS = ROOT / "PREDICTIONS.md"
HEADING = "## 05-event-bus:"
DEFAULT_N = 10


def refuse_unregistered() -> None:
    """Pre-registration: no benchmark run while the draft says DRAFT, is missing, or PREDICTIONS.md lacks this folder's section."""
    if not DRAFT_FILE.exists():
        raise SystemExit(f"Refusing to run: {DRAFT_FILE} is missing. Write and approve the predictions before the benchmark.")
    if "DRAFT" in DRAFT_FILE.read_text(encoding="utf-8"):
        raise SystemExit(f"Refusing to run: {DRAFT_FILE.name} still says DRAFT. Get the predictions approved and frozen first (run.py is still allowed).")
    if not PREDICTIONS.exists() or HEADING not in PREDICTIONS.read_text(encoding="utf-8"):
        raise SystemExit(f"Refusing to run: {PREDICTIONS.name} has no '{HEADING}' section. Append the frozen predictions and commit first.")


def rotated(arms: list[str], index: int) -> list[str]:
    shift = index % len(arms)
    return arms[shift:] + arms[:shift]


def line_of(row: dict) -> str:
    score = row["score"]
    return (
        f"== {row['arm']} run {row['run']}: fixed {score['risks_fixed']}/{score['risks_total']}, regressions {score['regressions']}, "
        f"faults {row['fault_outcomes']}, {row['total_tokens']} tokens, {row['wall_seconds']}s"
    )


def run_arms(
    n: int, arms: list[str], out: Path, backend_name: str | None, consumer_model: str, owner_model: str,
    parallel_arms: bool = False, token_ceiling: int | None = None,
) -> None:
    backend_name = backend_name or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    backend = backend_from_name(backend_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()
    spent, skipped = 0, []

    def one(index: int, arm: str) -> dict:
        row = run_row(run=index + 1, run_index=index, backend_name=backend_name, arm=arm, backend=backend, consumer_model=consumer_model, owner_model=owner_model)
        with write_lock:
            with out.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            print(line_of(row), flush=True)
        return row

    for index in range(n):
        order = rotated(arms, index)
        if not may_start_group(spent, token_ceiling):
            skipped += [SkippedWork(f"{arm} run {index + 1}", f"token ceiling {token_ceiling} reached at {spent}") for arm in order]
            continue
        if parallel_arms:
            with ThreadPoolExecutor(max_workers=len(order)) as pool:
                rows = list(pool.map(lambda arm: one(index, arm), order))
        else:
            rows = [one(index, arm) for arm in order]
        spent += sum(row["total_tokens"] for row in rows)
    if skipped:
        print(describe_skipped(skipped), flush=True)


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def setup_of(row: dict) -> tuple:
    return (row["consumer_model"], row["owner_model"], row["backend"], tuple(row["consumer_served_primary"]), tuple(row["owner_served_primary"]))


def mean(values) -> float:
    values = list(values)
    return statistics.mean(values) if values else 0.0


def table(header: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]


def arm_table(by_arm: dict[str, list[dict]]) -> list[str]:
    header = [
        "arm", "n", f"risks fixed of {len(RISKS)}, mean (min)", "clean fix rate", "fleet-telemetry fixed (of 2n)", "outside contract, mean",
        "regressions, mean", "tokens, mean (consumers / owner / investigator)", "consumer calls, mean", "owner prompt chars, mean", "wall s, mean",
    ]
    rows = []
    for arm, runs in by_arm.items():
        fixed = [row["score"]["risks_fixed"] for row in runs]
        roles = [row["tokens_by_role"] for row in runs]
        rows.append([
            arm, len(runs), f"{mean(fixed):.1f} ({min(fixed)})",
            f"{sum(row['clean_fixed'] for row in runs)} of {sum(row['clean_total'] for row in runs)}",
            f"{sum(row['fleet_fixed'] for row in runs)} of {2 * len(runs)}",
            f"{mean(row['score']['outside_contract'] for row in runs):.1f}", f"{mean(row['score']['regressions'] for row in runs):.1f}",
            f"{mean(row['total_tokens'] for row in runs):,.0f} ({mean(r['consumers'] for r in roles):,.0f} / {mean(r['owner'] for r in roles):,.0f} / {mean(r['investigator'] for r in roles):,.0f})",
            f"{mean(row['consumer_calls'] for row in runs):.1f}", f"{mean(row['owner']['prompt_chars'] for row in runs):,.0f}",
            f"{mean(row['wall_seconds'] for row in runs):.0f}",
        ])
    return table(header, rows)


def fault_tables(by_arm: dict[str, list[dict]]) -> list[str]:
    rows = []
    for arm, runs in by_arm.items():
        for kind in LOSS_FAULT_KINDS:
            counts = [sum(1 for row in runs if row["fault_outcomes"][kind] == label) for label in ("recovered", "absorbed", "lost")]
            rows.append([arm, kind, *(f"{count} of {len(runs)}" for count in counts)])
    lines = table(["arm", "fault", "recovered", "absorbed", "lost"], rows)
    rows = []
    for arm, runs in by_arm.items():
        decided = [row["wrong_service"] for row in runs if row["wrong_service"]["declined"] is not None]
        rows.append([
            arm, f"{sum(1 for row in runs if row['duplicate']['calls'] >= 2)} of {len(runs)}", sum(1 for row in runs if row["duplicate"]["rewrote"]),
            sum(1 for row in runs if row["duplicate"]["harm"]),
            f"{sum(1 for item in decided if item['declined'])} of {len(decided)}", sum(1 for item in decided if item["bogus_edit"]),
        ])
    return lines + ["", "Duplicate and wrong-service handling:", ""] + table(
        ["arm", "runs where the duplicate was processed twice", "runs where the second processing rewrote files", "harm (rewrote and target unfixed)",
         "wrong team declined", "wrong team edited its own files"], rows)


def traceability_table(by_arm: dict[str, list[dict]]) -> list[str]:
    rows = []
    for arm, runs in by_arm.items():
        variants = [("with trace context" if arm == "platform-owned" else "records as kept", "investigator")]
        if arm == "platform-owned":
            variants.append(("trace context stripped", "investigator_no_trace"))
        for label, key in variants:
            scores = [row[key]["score"] for row in runs if row.get(key)]
            if not scores:
                continue
            losses = sum(item["channel_losses"] for item in scores)
            rows.append([
                arm, label, len(scores), losses, f"{sum(item['correct'] for item in scores)} of {losses}",
                f"{sum(item['false_assurance_channel'] for item in scores)} of {losses}",
                f"{sum(item['false_assurance_fix'] for item in scores)} of {sum(item['fix_did_not_hold'] for item in scores)}",
                sum(len(item["missing_risks"]) for item in scores),
            ])
    return table(["arm", "records", "runs", "channel-caused losses", "cause correct", "false assurance (channel loss reported handled)",
                  "false assurance (fix did not hold reported handled)", "risks left out"], rows)


def per_run_table(rows: list[dict], arms: list[str]) -> list[str]:
    header = ["run", "malformed", "wrong-service", "consumer-crash", "duplicate", *(f"{arm}: faults, fixed" for arm in arms)]
    lines = []
    for run in sorted({row["run"] for row in rows}):
        group = {row["arm"]: row for row in rows if row["run"] == run}
        first = next(iter(group.values()))
        cells = [
            f"{' / '.join(row['fault_outcomes'][kind][:3] for kind in LOSS_FAULT_KINDS)}, {row['score']['risks_fixed']}" if (row := group.get(arm)) else "-"
            for arm in arms
        ]
        lines.append([run, *(first["faults"][kind] for kind in ("malformed", "wrong-service", "consumer-crash", "duplicate")), *cells])
    return table(header, lines)


def summarise(path: Path) -> str:
    if not path.exists():
        return f"No runs at {path}."
    rows = load_rows(path)
    setups = {setup_of(row) for row in rows}
    if len(setups) > 1:
        raise ValueError(f"{path.name} mixes models or backends ({len(setups)} setups); summarise one setup per file")
    consumer_model, owner_model, backend, consumer_served, owner_served = setups.pop()
    by_arm = {arm: [row for row in rows if row["arm"] == arm] for arm in ARMS}
    by_arm = {arm: runs for arm, runs in by_arm.items() if runs}
    dates = sorted({row["at"][:10] for row in rows})
    lines = [
        *arm_table(by_arm),
        "", "Loss faults (recovered: target fixed and its own message processed fixed; absorbed: fixed through another message; lost):", "",
        *fault_tables(by_arm),
        "", "Traceability (investigator scored against the harness's ground truth):", "",
        *traceability_table(by_arm),
        "", "Per run (fault outcomes malformed / wrong-service / consumer-crash as rec, abs or los, then risks fixed):", "",
        *per_run_table(rows, list(by_arm)),
        f"\nConsumers {consumer_model} (served: {', '.join(consumer_served) or 'not reported'}), owner roles {owner_model} "
        f"(served: {', '.join(owner_served) or 'not reported'}), backend {backend}. Run dates: {', '.join(dates)}.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="runs per arm")
    parser.add_argument("--arms", nargs="+", default=list(ARMS), choices=ARMS)
    parser.add_argument("--parallel-arms", action="store_true", help="run the arms of one run index at the same time")
    parser.add_argument("--token-ceiling", type=int, default=None, help="stop starting new run indexes once this many tokens are spent")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "runs.jsonl")
    parser.add_argument("--summary", type=Path, default=HERE / "results" / "summary.md")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--consumer-model", default=CONSUMER_MODEL)
    parser.add_argument("--owner-model", default=OWNER_MODEL)
    parser.add_argument("--summarise-only", action="store_true")
    args = parser.parse_args()
    if not args.summarise_only:
        refuse_unregistered()
        run_arms(args.n, args.arms, args.out, args.backend, args.consumer_model, args.owner_model, args.parallel_arms, args.token_ceiling)
    summary = summarise(args.out)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(summary + "\n")
    print(summary)


if __name__ == "__main__":
    main()
