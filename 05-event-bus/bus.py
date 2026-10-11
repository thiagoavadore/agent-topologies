"""An in-process event bus between a scanner and six team consumers, with faults injected and three owners for the channel.

The scanner publishes one message per planted risk. Teams fix what they receive, in a scratch copy of Harbour Bikes; the
harness writes their files. The arm decides who looks after the channel: nobody, a steward agent reading the whole log,
or a platform that enforces delivery guarantees in code and shows its agent only the exceptions. The score is
`topologies.fixcheck.check()` on the final repo; an investigator is then asked to explain what happened and is scored
against the harness's ground truth.
"""

import datetime as dt
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bus_channel import Bus, Job, Message
from bus_config import ARMS, CONSUMER_MODEL, OWNER_MODEL, POLICIES, ROUTABLE, SUBSCRIPTIONS
from bus_faults import faults_for_run, malform, misroute, scanner_messages
from bus_owner import ask_owner, execute, owner_view
from bus_prompts import INVESTIGATOR_SCHEMA, INVESTIGATOR_SYSTEM, consumer_prompt, consumer_schema, consumer_system, investigator_prompt, service_paths
from bus_trace import fault_outcomes, ground_truth, records_for_investigator, score_investigator
from topologies.fixcheck import CheckResult, check
from topologies.guards import InvalidAfterRetry, call_and_validate
from topologies.harbour import FIXTURE, RISKS_BY_ID, SERVICES
from topologies.model import Backend

CALLED = ("fixed", "not_mine", "cannot_fix", "invalid", "error")
DECLINED = ("not_mine", "cannot_fix")
CLEAN_TOTAL = 5


@dataclass
class Delivery:
    message_id: str
    risk_id: str
    team: str
    service: str
    pass_no: int
    attempt: int
    duplicate: bool  # the transport delivered the message again
    injected_failure: bool = False
    outcome: str = ""  # fixed, not_mine, cannot_fix, invalid, error, injected-failure or skipped-duplicate
    reason: str = ""
    files: dict[str, str] = field(default_factory=dict)  # files the reply changed, full new text
    tokens: int = 0
    served_models: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0


@dataclass
class Spend:
    """Tokens and served models of one role's calls."""

    tokens: int = 0
    served: set[str] = field(default_factory=set)
    primary: set[str] = field(default_factory=set)  # the model that answered most, per call
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, tokens: int, served_models) -> None:
        with self.lock:
            self.tokens += tokens
            self.served.update(served_models)
            if served_models:
                self.primary.add(served_models[0])


class Pass:
    """The state one run shares across its team workers."""

    def __init__(self, *, bus: Bus, repo: Path, backend: Backend, model: str, faults: dict[str, str], risk_of: dict[str, str]) -> None:
        self.bus, self.repo, self.backend, self.model = bus, repo, backend, model
        self.crash_id = next(message_id for message_id, risk_id in risk_of.items() if risk_id == faults["consumer-crash"])
        self.duplicate_id = next(message_id for message_id, risk_id in risk_of.items() if risk_id == faults["duplicate"])
        self.crash_done = False
        self.duplicated = False
        self.spend = Spend()
        self.service_locks = {service: threading.Lock() for service in SERVICES}
        self.no_service_lock = threading.Lock()  # a message with no known service has no files to guard
        self.flag_lock = threading.Lock()

    def take_crash(self, message_id: str) -> bool:
        """True once: the first delivery of the consumer-crash target fails; its retry does not."""
        with self.flag_lock:
            if message_id == self.crash_id and not self.crash_done:
                self.crash_done = True
                return True
        return False

    def take_duplicate(self, job: Job) -> bool:
        """True once, after the first processing of the duplicate target: the transport delivers it again."""
        with self.flag_lock:
            if job.message.message_id == self.duplicate_id and not job.duplicate and not self.duplicated:
                self.duplicated = True
                return True
        return False


def write_files(repo: Path, files: list[dict]) -> dict[str, str]:
    """Write the files whose text differs from what is in the repo; return those."""
    changed = {}
    for item in files:
        path = repo / item["path"]
        if path.read_text(encoding="utf-8") != item["content"]:
            path.write_text(item["content"], encoding="utf-8")
            changed[item["path"]] = item["content"]
    return changed


def consume(state: Pass, team: str, message: Message, delivery: Delivery) -> None:
    """One consumer call on `message`: fill in the delivery with its outcome, tokens and files."""
    service = message.body.get("service", "")
    started = time.monotonic()
    lock = state.service_locks.get(service, state.no_service_lock)
    with lock:
        paths = service_paths(state.repo, service) if service in SERVICES else []
        system = consumer_system(team, state.bus.services_of(team))
        try:
            outcome = call_and_validate(
                state.backend, model=state.model, system=system, prompt=consumer_prompt(state.repo, message), schema=consumer_schema(paths)
            )
        except Exception as error:  # a backend failure takes this delivery out, not the run
            delivery.outcome, delivery.reason = "error", f"{type(error).__name__}: {str(error)[:300]}"
        else:
            delivery.tokens, delivery.served_models = outcome.tokens, list(outcome.served_models)
            state.spend.add(outcome.tokens, outcome.served_models)
            if isinstance(outcome, InvalidAfterRetry):
                delivery.outcome, delivery.reason = "invalid", outcome.error
            else:
                delivery.outcome, delivery.reason = outcome.payload["outcome"], outcome.payload["reason"]
                delivery.files = write_files(state.repo, outcome.payload["files"])
    delivery.wall_seconds = round(time.monotonic() - started, 1)


def handle(state: Pass, team: str, job: Job, pass_no: int) -> Delivery:
    """Deliver one job to a team under the arm's delivery rules."""
    bus, message = state.bus, job.message
    delivery = Delivery(message.message_id, bus.risk_of.get(message.message_id, ""), team, message.body.get("service", ""), pass_no, job.attempt, job.duplicate)
    if bus.policy.idempotent_receiver and bus.is_acked(team, message.message_id):
        delivery.outcome, delivery.reason = "skipped-duplicate", "this team already acknowledged the message id"
        bus.record("discarded", message, team=team, reason=delivery.reason)
        return delivery
    bus.log_received(team, message)
    bus.record("delivered", message, team=team)
    if state.take_crash(message.message_id):
        delivery.injected_failure, delivery.outcome, delivery.reason = True, "injected-failure", "the harness crashed the consumer before it replied"
    else:
        consume(state, team, message, delivery)
    if delivery.outcome in ("injected-failure", "invalid", "error"):
        bus.record("no_reply", message, team=team, reason=delivery.reason)
        if bus.policy.acks and job.attempt == 1:
            bus.enqueue(team, bus.copy_for_retry(message, "retry"), attempt=2)
        elif bus.policy.dead_letter:
            bus.dead_letter(message, f"{team} failed twice: {delivery.reason}"[:200])
    else:
        bus.log_outcome(team, message, delivery.outcome, delivery.reason)
        bus.record("outcome", message, team=team, outcome=delivery.outcome, reason=delivery.reason)
        if bus.policy.acks:
            bus.ack(team, message.message_id)
        if bus.policy.dead_letter and delivery.outcome in DECLINED:
            bus.dead_letter(message, f"{team} replied {delivery.outcome}: {delivery.reason}"[:200])
    if state.take_duplicate(job):
        bus.enqueue(team, message, duplicate=True)
    return delivery


def run_pass(state: Pass, pass_no: int) -> list[Delivery]:
    """Teams work in parallel, each through its own queue in order; the result lists deliveries team by team."""

    def work(team: str) -> list[Delivery]:
        deliveries = []
        while (job := state.bus.next_job(team)) is not None:
            deliveries.append(handle(state, team, job, pass_no))
        return deliveries

    with ThreadPoolExecutor(max_workers=len(state.bus.queues)) as pool:
        futures = [pool.submit(work, team) for team in state.bus.queues]
        return [delivery for future in futures for delivery in future.result()]


def score_record(result: CheckResult) -> dict:
    return {
        "risks_fixed": len(result.fixed),
        "risks_total": len(result.risks),
        "regressions": len(result.regressed),
        "outside_contract": len(result.outside_contract),
        "fixed": result.fixed,
        "regressed": result.regressed,
        "outside_contract_ids": result.outside_contract,
        "risks": {risk_id: asdict(item) for risk_id, item in result.risks.items()},
        "needs": {need_id: asdict(item) for need_id, item in result.needs.items()},
    }


def code_commit() -> str:
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 else ""
    except OSError:
        return ""


def investigate(backend: Backend, model: str, published: list[dict], records: str, truth: dict[str, dict], spend: Spend) -> dict:
    """One investigator call on `records`, scored against `truth`; a failed call scores as an empty answer."""
    answer, error, tokens = None, "", 0
    try:
        outcome = call_and_validate(
            backend, model=model, system=INVESTIGATOR_SYSTEM, prompt=investigator_prompt(published, SUBSCRIPTIONS, records), schema=INVESTIGATOR_SCHEMA
        )
    except Exception as problem:
        error = f"{type(problem).__name__}: {str(problem)[:300]}"
    else:
        tokens = outcome.tokens
        spend.add(outcome.tokens, outcome.served_models)
        if isinstance(outcome, InvalidAfterRetry):
            error = outcome.error
        else:
            answer = outcome.payload
    return {"answer": answer, "error": error, "tokens": tokens, "score": score_investigator(answer, truth), "records": records}


def duplicate_record(deliveries: list[Delivery], message_id: str, fixed_at_end: bool) -> dict:
    calls = [item for item in deliveries if item.message_id == message_id and item.outcome in CALLED]
    rewrote = any(item.duplicate and item.files for item in calls)
    return {"calls": len(calls), "rewrote": rewrote, "harm": rewrote and not fixed_at_end}


def wrong_service_record(deliveries: list[Delivery], message_id: str) -> dict:
    first = next((item for item in deliveries if item.message_id == message_id and item.outcome in CALLED), None)
    if first is None:
        return {"declined": None, "bogus_edit": None}
    return {"declined": first.outcome in DECLINED, "bogus_edit": bool(first.files)}


def run_bus(
    *,
    arm: str,
    run_index: int,
    backend: Backend,
    consumer_model: str = CONSUMER_MODEL,
    owner_model: str = OWNER_MODEL,
    workdir: Path | None = None,
) -> dict:
    """Run one arm on the fault schedule of 0-based `run_index` and return everything needed to re-read and re-score it."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}: use one of {ARMS}")
    started = time.monotonic()
    faults = faults_for_run(run_index)
    scanned, risk_of = scanner_messages(run_index)
    bus = Bus(POLICIES[arm], SUBSCRIPTIONS, risk_of)
    owner_spend, investigator_spend = Spend(), Spend()
    with ExitStack() as stack:
        base = workdir or Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="bus-")))
        repo = base / "repo"
        # CHECKER.md is the answer key: it must never be in a repo an agent can read.
        shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("CHECKER.md", "__pycache__", ".git"))
        state = Pass(bus=bus, repo=repo, backend=backend, model=consumer_model, faults=faults, risk_of=risk_of)

        published = []
        for item in scanned:
            body = item["body"]
            if item["risk_id"] == faults["malformed"]:
                body = malform(body)
            if item["risk_id"] == faults["wrong-service"]:
                body = misroute(body)
            route = bus.publish(Message(item["message_id"], body))
            published.append({"message_id": item["message_id"], "risk_id": item["risk_id"], "body": body, "route": route})
        deliveries = run_pass(state, 1)

        owner = ask_owner(arm, backend=backend, model=owner_model, bus=bus)
        owner_spend.add(owner.tokens, owner.served_models)
        visible_ids = {entry["message_id"] for entry in owner_view(arm, bus)}
        action_log = execute(owner.actions, bus=bus, visible_ids=visible_ids)
        deliveries += run_pass(state, 2)
        result = check(repo)

    truth = ground_truth(faults=faults, deliveries=deliveries, check_result=result)
    outcomes = fault_outcomes(truth, faults)
    ledger = [{"message_id": item["message_id"], "risk_id": item["risk_id"], "service": RISKS_BY_ID[item["risk_id"]].service} for item in scanned]
    investigator = investigate(backend, owner_model, ledger, records_for_investigator(arm, bus), truth, investigator_spend)
    no_trace_spend = Spend()
    investigator_no_trace = None
    if arm == "platform-owned":
        investigator_no_trace = investigate(backend, owner_model, ledger, records_for_investigator(arm, bus, strip_trace=True), truth, no_trace_spend)

    fleet_ids = [risk_id for risk_id in truth if risk_id.startswith("fleet-telemetry.")]
    clean_ids = [risk_id for risk_id in ROUTABLE if risk_id not in faults.values()]
    assert len(clean_ids) == CLEAN_TOTAL
    id_of = {item["risk_id"]: item["message_id"] for item in scanned}  # risk_of also holds corrective copies
    owner_primary = sorted(owner_spend.primary | investigator_spend.primary | no_trace_spend.primary)
    consumer_tokens = state.spend.tokens
    return {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "arm": arm,
        "backend": "",  # set by run_row
        "consumer_model": consumer_model,
        "owner_model": owner_model,
        "code_commit": code_commit(),
        "faults": faults,
        "wrong_destination": next(item["body"]["service"] for item in published if item["risk_id"] == faults["wrong-service"]),
        "published": published,
        "deliveries": [asdict(item) for item in deliveries],
        "owner": {
            "called": owner.called, "actions": owner.actions, "action_log": action_log, "tokens": owner.tokens,
            "error": owner.error, "prompt_chars": owner.prompt_chars,
        },
        "invalid": bus.invalid,
        "dead_letters": bus.dead_letters,
        "store_size": len(bus.store),
        "store": bus.store,
        "team_logs": bus.team_logs,
        "score": score_record(result),
        "truth": truth,
        "fault_outcomes": outcomes,
        "fleet_fixed": sum(1 for risk_id in fleet_ids if truth[risk_id]["fixed"]),
        "clean_fixed": sum(1 for risk_id in clean_ids if truth[risk_id]["fixed"]),
        "clean_total": CLEAN_TOTAL,
        "duplicate": duplicate_record(deliveries, id_of[faults["duplicate"]], truth[faults["duplicate"]]["fixed"]),
        "wrong_service": wrong_service_record(deliveries, id_of[faults["wrong-service"]]),
        "investigator": investigator,
        "investigator_no_trace": investigator_no_trace,
        "tokens_by_role": {
            "consumers": consumer_tokens, "owner": owner.tokens, "investigator": investigator["tokens"],
            "investigator_no_trace": investigator_no_trace["tokens"] if investigator_no_trace else 0,
        },
        "total_tokens": consumer_tokens + owner.tokens + investigator["tokens"],
        "consumer_calls": sum(1 for item in deliveries if item.outcome in CALLED),
        "consumer_served_models": sorted(state.spend.served),
        "consumer_served_primary": sorted(state.spend.primary),
        "owner_served_primary": owner_primary,
        "wall_seconds": round(time.monotonic() - started, 1),
    }


def run_row(*, run: int, backend_name: str, run_index: int, **kwargs) -> dict:
    """One JSONL row: run number and backend name around the record of `run_bus`."""
    record = run_bus(run_index=run_index, **kwargs)
    return {**record, "run": run, "run_index": run_index, "backend": backend_name}
