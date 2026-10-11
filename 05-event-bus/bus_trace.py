"""Ground truth for what went wrong with each risk, the records the investigator reads, and how its answer is scored."""

from bus_channel import Bus
from bus_config import LOSS_FAULT_KINDS, OWNER, TEAMS
from bus_owner import compact
from bus_prompts import json_lines
from topologies.fixcheck import CheckResult
from topologies.harbour import RISKS_BY_ID

CHANNEL_CAUSES = ("no_subscriber", "missing_fields", "wrong_team", "consumer_failed")
CAUSE_OF_FAULT = {"malformed": "missing_fields", "wrong-service": "wrong_team", "consumer-crash": "consumer_failed"}
FIRST_PROBLEM_OF_FAULT = {**CAUSE_OF_FAULT, "duplicate": "delivered_twice"}
ANSWERED = ("fixed", "not_mine", "cannot_fix")
DECLINED = ("not_mine", "cannot_fix")
UNOWNED = "unowned"


def first_problem(risk_id: str, service: str, kind_of: dict[str, str], answers: list[str]) -> tuple[str, str | None]:
    """The first thing that went wrong with a risk's message and the scored kind it belongs to, even if it was later put right."""
    if OWNER[service] is None:
        return "no_subscriber", UNOWNED
    if risk_id in kind_of:
        return FIRST_PROBLEM_OF_FAULT[kind_of[risk_id]], kind_of[risk_id]
    return ("consumer_declined" if answers and answers[0] in DECLINED else "none"), None


def ground_truth(*, faults: dict[str, str], deliveries: list, check_result: CheckResult) -> dict[str, dict]:
    """For each risk: fixed, processed_fixed, the cause of a loss and the first problem.

    Only a complete delivery (all fields, the risk's own service, the team that owns it) counts as the risk's message being
    processed: a consumer that guesses "fixed" from a malformed or misrouted message does not.
    """
    fault_cause = {faults[kind]: CAUSE_OF_FAULT[kind] for kind in LOSS_FAULT_KINDS}
    kind_of = {risk_id: kind for kind, risk_id in faults.items()}
    truth = {}
    for risk_id, risk in RISKS_BY_ID.items():
        answers = [item.outcome for item in deliveries if item.risk_id == risk_id and item.complete and item.outcome in ANSWERED]
        fixed = risk_id in check_result.fixed
        processed_fixed = "fixed" in answers
        cause = "none"
        if not fixed:
            if answers:
                cause = "fix_did_not_hold" if processed_fixed else "consumer_declined"
            elif risk_id in fault_cause:
                cause = fault_cause[risk_id]
            elif OWNER[risk.service] is None:
                cause = "no_subscriber"
            else:
                cause = "unknown"
        problem, problem_kind = first_problem(risk_id, risk.service, kind_of, answers)
        truth[risk_id] = {
            "fixed": fixed, "processed_fixed": processed_fixed, "cause": cause, "channel_caused": cause in CHANNEL_CAUSES,
            "first_problem": problem, "problem_kind": problem_kind,
        }
    return truth


def fault_outcomes(truth: dict[str, dict], faults: dict[str, str]) -> dict[str, str]:
    """For the three loss faults: recovered, absorbed (fixed through another message) or lost."""
    outcomes = {}
    for kind in LOSS_FAULT_KINDS:
        item = truth[faults[kind]]
        if not item["fixed"]:
            outcomes[kind] = "lost"
        else:
            outcomes[kind] = "recovered" if item["processed_fixed"] else "absorbed"
    return outcomes


class Relabeller:
    """Replaces the id of every corrective copy with a fresh one, the same way everywhere in a block."""

    def __init__(self) -> None:
        self.fresh: dict[tuple, str] = {}

    def apply(self, entry: dict) -> dict:
        entry = dict(entry)
        if entry.get("correlation_id"):
            key = (entry["message_id"], tuple(entry.get("history", [])))
            entry["message_id"] = self.fresh.setdefault(key, f"c{len(self.fresh) + 1:02d}")
        entry.pop("correlation_id", None)
        entry.pop("history", None)
        return entry


def records_for_investigator(arm: str, bus: Bus, *, strip_trace: bool = False) -> str:
    """The text of what the arm's platform kept; `strip_trace` removes Correlation Identifiers and Message History."""
    if arm == "no-owner":
        blocks = []
        for team in TEAMS:
            log = bus.team_logs[team]
            blocks.append(f"Local log of {team}:\n{json_lines(log) if log else '(nothing received)'}")
        return "\n\n".join(blocks)
    relabel = Relabeller()
    prepare = (lambda entry: compact(relabel.apply(entry))) if strip_trace else compact
    blocks = ["Message Store, oldest first:\n" + json_lines([prepare(event) for event in bus.store])]
    if arm == "platform-owned":
        blocks.append("Dead Letter Channel:\n" + json_lines([prepare(entry) for entry in bus.dead_letters]))
        blocks.append("Invalid Message Channel:\n" + json_lines([prepare(entry) for entry in bus.invalid]))
    return "\n\n".join(blocks)


def score_investigator(answer: dict | None, truth: dict[str, dict]) -> dict:
    """Score against harness ground truth; a risk the answer leaves out counts as wrong.

    Headline: `first_problem` on the six risks that had a problem at the start (four faulted, two unowned), recovered or not.
    Secondary: the cause of risks still unfixed at the end, and false assurance.
    """
    said = {item["risk_id"]: item for item in (answer or {}).get("risks", [])}
    right = {risk_id: said.get(risk_id, {}).get("first_problem") == item["first_problem"] for risk_id, item in truth.items()}
    by_kind: dict[str, list[int]] = {}
    for risk_id, item in truth.items():
        if item["problem_kind"]:
            counts = by_kind.setdefault(item["problem_kind"], [0, 0])
            counts[0] += right[risk_id]
            counts[1] += 1
    clean = [risk_id for risk_id, item in truth.items() if not item["problem_kind"]]
    losses = [risk_id for risk_id, item in truth.items() if item["channel_caused"]]
    correct = [risk_id for risk_id in losses if said.get(risk_id, {}).get("cause") == truth[risk_id]["cause"]]
    false_channel = [risk_id for risk_id in losses if said.get(risk_id, {}).get("status") == "handled"]
    false_fix = [risk_id for risk_id, item in truth.items() if item["cause"] == "fix_did_not_hold" and said.get(risk_id, {}).get("status") == "handled"]
    return {
        "first_problem_scored": sum(counts[1] for counts in by_kind.values()),
        "first_problem_correct": sum(counts[0] for counts in by_kind.values()),
        "first_problem_by_kind": by_kind,
        "clean_total": len(clean),
        "clean_correct": sum(right[risk_id] for risk_id in clean),
        "channel_losses": len(losses),
        "correct": len(correct),
        "accuracy": len(correct) / len(losses) if losses else None,
        "false_assurance_channel": len(false_channel),
        "false_assurance_fix": len(false_fix),
        "fix_did_not_hold": sum(1 for item in truth.values() if item["cause"] == "fix_did_not_hold"),
        "missing_risks": [risk_id for risk_id in truth if risk_id not in said],
    }
