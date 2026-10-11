"""Run every arm n times, append every row to results/runs.jsonl and write results/summary.md.

One run index runs the 8 workers once and then each arm on the same worker replies (paired design):
  flat                 the platform lead manages the 8 workers directly and sees every service's needs
  by-team              3 sub-leads cut along team boundaries (grouped along the booking call graph)
  by-runtime           3 sub-leads cut by language, splitting both of bookings-api's call edges
  by-runtime-registry  the by-runtime sub-lead replies reused, plus the needs registry at the platform lead
Benchmark runs are refused until the predictions are frozen: the draft file must exist and not say DRAFT, and
PREDICTIONS.md must carry this folder's section.
"""

import argparse
import json
import os
import statistics
from pathlib import Path

from hier import run_pair
from hier_config import ARMS, LEAD_MODEL, WORKER_MODEL
from hier_harm import LADDER
from topologies.guards import SkippedWork, describe_skipped, may_start_group
from topologies.harbour import REGRESSION_NEEDS, RISKS
from topologies.model import backend_from_name

HERE = Path(__file__).parent
ROOT = HERE.parent
DRAFT_FILE = ROOT / "docs/specs/spec-issue-15-04-05/predictions-draft.md"
PREDICTIONS = ROOT / "PREDICTIONS.md"
HEADING = "## 04-hierarchical:"
DEFAULT_N = 10


def refuse_unregistered() -> None:
    """Pre-registration: no benchmark run while the predictions are a draft, missing or not in PREDICTIONS.md."""
    if not DRAFT_FILE.exists():
        raise SystemExit(f"Refusing to run: {DRAFT_FILE} is missing. Write and approve the predictions before the benchmark.")
    if "DRAFT" in DRAFT_FILE.read_text(encoding="utf-8"):
        raise SystemExit(f"Refusing to run: {DRAFT_FILE.name} still says DRAFT. Get the predictions approved and frozen first (run.py is still allowed).")
    if not PREDICTIONS.exists() or HEADING not in PREDICTIONS.read_text(encoding="utf-8"):
        raise SystemExit(f"Refusing to run: {PREDICTIONS.name} has no section starting {HEADING!r}. Append the frozen predictions and commit them first.")


def pair_tokens(rows: list[dict]) -> int:
    """Tokens actually spent on one run index: shared calls (workers, reused sub-leads) are counted once."""
    workers = rows[0]["tokens_by_tier"]["workers"]
    sub_leads = sum(row["tokens_by_tier"]["sub_leads"] for row in rows if not row["sub_leads_shared_with"])
    return workers + sub_leads + sum(row["tokens_by_tier"]["top"] for row in rows)


def run_pairs(n: int, arms: list[str], out: Path, backend_name: str | None, worker_model: str, lead_model: str, parallel_arms: bool = False, token_ceiling: int | None = None) -> None:
    backend_name = backend_name or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    backend = backend_from_name(backend_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    spent, skipped = 0, []
    for index in range(n):
        if not may_start_group(spent, token_ceiling):
            skipped.append(SkippedWork(f"run {index + 1}", f"token ceiling {token_ceiling} reached at {spent}"))
            continue
        rows = run_pair(run=index + 1, backend=backend, backend_name=backend_name, worker_model=worker_model, lead_model=lead_model, arms=arms, parallel_arms=parallel_arms)
        spent += pair_tokens(rows)
        with out.open("a") as handle:
            handle.writelines(json.dumps(row) + "\n" for row in rows)
        for row in rows:
            score = row["score"]
            print(
                f"== {row['arm']} run {index + 1}: fixed {score['risks_fixed']}/{score['risks_total']}, regressions {score['regressions']} {score['regressed']}, "
                f"escalations {row['escalations']}, harmful up {row['harmful_up_inside'] + row['harmful_up_outside']}, management tokens {row['management_tokens']}",
                flush=True,
            )
    if skipped:
        print(describe_skipped(skipped), flush=True)


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def mean(values) -> float:
    values = list(values)
    return statistics.mean(values) if values else 0.0


def short(need_id: str) -> str:
    return need_id.split(".")[0]


def summarise(path: Path) -> str:
    if not path.exists():
        return f"No runs at {path}."
    rows = load_rows(path)
    setups = {(row["worker_model"], row["lead_model"], row["backend"]) for row in rows}
    worker_served = {tuple(row["worker_served_primary"]) for row in rows}  # the same in every row of a run
    lead_served = {model for row in rows for model in row["lead_served_primary"]}
    if len(setups) > 1 or len(worker_served) > 1 or len(lead_served) > 1:
        raise ValueError(f"{path.name} mixes models, served models or backends; summarise one setup per file")
    commits = {row["code_commit"] for row in rows}
    if any(commit.endswith("-dirty") for commit in commits):
        raise ValueError(f"{path.name} has rows run on uncommitted code ({sorted(commits)}); commit, then re-run")
    if len(commits) > 1:
        raise ValueError(f"{path.name} mixes code commits {sorted(commits)}; summarise one commit per file")
    worker_model, lead_model, backend = setups.pop()
    worker_served = sorted(worker_served.pop())
    by_arm = {arm: [row for row in rows if row["arm"] == arm] for arm in ARMS}
    present = [arm for arm, runs in by_arm.items() if runs]
    regression_columns = " | ".join(f"{short(need)} regressed" for need in REGRESSION_NEEDS)
    lines = [
        f"| arm | n | risks fixed of {len(RISKS)}, mean (min) | runs with a regression | {regression_columns} | outside contract, mean | escalations, mean (hub-changing) | harmful requests up, mean (inside / outside) | overrides granted, mean | distinct effective timeouts, mean | tokens, mean (workers / sub-leads / top) | management tokens, mean | platform-lead prompt chars, mean |",
        "|---|---|---|---|" + "---|" * len(REGRESSION_NEEDS) + "---|---|---|---|---|---|---|---|",
    ]
    for arm in present:
        runs = by_arm[arm]
        fixed = [row["score"]["risks_fixed"] for row in runs]
        tiers = [row["tokens_by_tier"] for row in runs]
        per_need = " | ".join(f"{sum(1 for row in runs if need in row['score']['regressed'])} of {len(runs)}" for need in REGRESSION_NEEDS)
        lines.append(
            f"| {arm} | {len(runs)} | {mean(fixed):.1f} ({min(fixed)}) "
            f"| {sum(1 for row in runs if row['score']['regressions'])} of {len(runs)} | {per_need} "
            f"| {mean(row['score']['outside_contract'] for row in runs):.1f} | {mean(row['escalations'] for row in runs):.1f} ({mean(row['hub_changing_escalations'] for row in runs):.1f}) "
            f"| {mean(row['harmful_up_inside'] + row['harmful_up_outside'] for row in runs):.1f} ({mean(row['harmful_up_inside'] for row in runs):.1f} / {mean(row['harmful_up_outside'] for row in runs):.1f}) "
            f"| {mean(len(row['overrides_granted']) for row in runs):.1f} | {mean(row['distinct_timeouts'] for row in runs):.1f} "
            f"| {mean(row['total_tokens'] for row in runs):,.0f} ({mean(t['workers'] for t in tiers):,.0f} / {mean(t['sub_leads'] for t in tiers):,.0f} / {mean(t['top'] for t in tiers):,.0f}) "
            f"| {mean(row['management_tokens'] for row in runs):,.0f} | {mean(row['top_prompt_chars'] for row in runs):,.0f} |"
        )
    lines += [
        "",
        "Reliability (per arm; sub-lead calls are shared by `by-runtime-registry` and `by-runtime`, so read the registry row's fallbacks as a repeat):",
        "",
        "| arm | platform-lead calls that failed | sub-lead fallbacks (forwarded-unchanged) | unreadable requests up | unreadable platform-lead decisions | rows with any call error | rows with a served-model mismatch | management call seconds, mean (summed, not elapsed) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arm in present:
        runs = by_arm[arm]
        lines.append(
            f"| {arm} | {sum(1 for row in runs if row['top']['error'])} of {sum(1 for row in runs if row['top']['called'])} "
            f"| {sum(row['sub_lead_fallbacks'] for row in runs)} of {sum(len(row['sub_leads']) for row in runs)} "
            f"| {sum(row['unreadable_requests_up'] for row in runs)} | {sum(len(row['unreadable_decisions']) for row in runs)} "
            f"| {sum(1 for row in runs if row['call_errors'])} of {len(runs)} | {sum(1 for row in runs if row['model_mismatches'])} of {len(runs)} "
            f"| {mean(row['management_call_seconds'] for row in runs):.0f} |"
        )
    lines += ["", "Coupling ladder, runs per outcome (where a threat to each regression need was stopped):", "", "| arm | need | " + " | ".join(LADDER) + " |", "|---|---|" + "---|" * len(LADDER)]
    for arm in present:
        for need in REGRESSION_NEEDS:
            counts = [sum(1 for row in by_arm[arm] if row["ladder"][need] == outcome) for outcome in LADDER]
            lines.append(f"| {arm} | {short(need)} | " + " | ".join(str(count) for count in counts) + " |")
    lines += ["", "Per run: harmful worker requests (shared by all arms), then each arm's final `http.default_timeout` / `http.default_rate_limit` / regressed services:", ""]
    lines += ["| run | harmful worker requests | " + " | ".join(present) + " |", "|---|---|" + "---|" * len(present)]
    for run in sorted({row["run"] for row in rows}):
        cells, harmful = [], "-"
        for arm in present:
            found = [row for row in by_arm[arm] if row["run"] == run]
            if not found:
                cells.append("-")
                continue
            row = found[0]
            harmful = str(row["harmful_worker_requests"])
            regressed = ", ".join(short(need) for need in row["score"]["regressed"]) or "none"
            cells.append(f"{row['final_hub']['http.default_timeout']} / {row['final_hub']['http.default_rate_limit']} / {regressed}")
        lines.append(f"| {run} | {harmful} | " + " | ".join(cells) + " |")
    served = f"workers {', '.join(worker_served) or 'not reported'}, leads {', '.join(sorted(lead_served)) or 'not reported'}"
    lines.append(
        f"\nWorkers {worker_model}, leads {lead_model} (served: {served}), backend {backend}. Run dates: {', '.join(sorted({row['at'][:10] for row in rows}))}. "
        "Worker tokens are the same in all arm rows of a run, and the by-runtime-registry row repeats the by-runtime sub-lead tokens (the calls ran once), so do not sum tokens across rows."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="run indexes (each runs every arm on one set of worker replies)")
    parser.add_argument("--arms", nargs="+", default=list(ARMS), choices=ARMS)
    parser.add_argument("--parallel-arms", action="store_true", help="run the arms of one index in parallel")
    parser.add_argument("--token-ceiling", type=int, default=None, help="stop starting new run indexes once this many tokens are spent")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "runs.jsonl")
    parser.add_argument("--summary", type=Path, default=HERE / "results" / "summary.md")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--worker-model", default=WORKER_MODEL)
    parser.add_argument("--lead-model", default=LEAD_MODEL)
    parser.add_argument("--summarise-only", action="store_true")
    args = parser.parse_args()
    if not args.summarise_only:
        refuse_unregistered()
        run_pairs(args.n, args.arms, args.out, args.backend, args.worker_model, args.lead_model, args.parallel_arms, args.token_ceiling)
    table = summarise(args.out)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(table + "\n")
    print(table)


if __name__ == "__main__":
    main()
