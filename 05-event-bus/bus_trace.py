"""Ground truth for why a risk went unfixed, the records the investigator reads, and how its answer is scored."""

from bus_channel import Bus
from bus_config import LOSS_FAULT_KINDS, OWNER, TEAMS
from bus_owner import compact
from bus_prompts import json_lines
from topologies.fixcheck import CheckResult
from topologies.harbour import RISKS_BY_ID

CHANNEL_CAUSES = ("no_subscriber", "missing_fields", "wrong_team", "consumer_failed")
CAUSE_OF_FAULT = {"malformed": "missing_fields", "wrong-service": "wrong_team", "consumer-crash": "consumer_failed"}
ANSWERED = ("fixed", "not_mine", "cannot_fix")


def ground_truth(*, faults: dict[str, str], deliveries: list, check_result: CheckResult) -> dict[str, dict]:
    """For each risk: fixed, processed_fixed, and for an unfixed risk the cause the harness knows."""
    fault_cause = {faults[kind]: CAUSE_OF_FAULT[kind] for kind in LOSS_FAULT_KINDS}
    truth = {}
    for risk_id, risk in RISKS_BY_ID.items():
        mine = [item for item in deliveries if item.risk_id == risk_id]
        answers = [item.outcome for item in mine if item.outcome in ANSWERED]
        fixed = risk_id in check_result.fixed
        processed_fixed = "fixed" in answers
        cause = "none"
        if not fixed:
            if OWNER[risk.service] is None and not mine:
                cause = "no_subscriber"
            elif risk_id in fault_cause and not processed_fixed:
                cause = fault_cause[risk_id]
            elif answers and all(outcome != "fixed" for outcome in answers):
                cause = "consumer_declined"
            elif processed_fixed:
                cause = "fix_did_not_hold"
            else:
                cause = "unknown"
        truth[risk_id] = {"fixed": fixed, "processed_fixed": processed_fixed, "cause": cause, "channel_caused": cause in CHANNEL_CAUSES}
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
    """Score against harness ground truth; a risk the answer leaves out counts as wrong."""
    said = {item["risk_id"]: item for item in (answer or {}).get("risks", [])}
    losses = [risk_id for risk_id, item in truth.items() if item["channel_caused"]]
    correct = [risk_id for risk_id in losses if said.get(risk_id, {}).get("cause") == truth[risk_id]["cause"]]
    false_channel = [risk_id for risk_id in losses if said.get(risk_id, {}).get("status") == "handled"]
    false_fix = [risk_id for risk_id, item in truth.items() if item["cause"] == "fix_did_not_hold" and said.get(risk_id, {}).get("status") == "handled"]
    return {
        "channel_losses": len(losses),
        "correct": len(correct),
        "accuracy": len(correct) / len(losses) if losses else None,
        "false_assurance_channel": len(false_channel),
        "false_assurance_fix": len(false_fix),
        "fix_did_not_hold": sum(1 for item in truth.values() if item["cause"] == "fix_did_not_hold"),
        "missing_risks": [risk_id for risk_id in truth if risk_id not in said],
    }
