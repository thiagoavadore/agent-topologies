from types import SimpleNamespace

import pytest
from bus_helpers import smart_actions  # noqa: F401  (imports first: puts this folder on sys.path)

from bus import Delivery
from bus_channel import Bus, Message
from bus_config import FAULT_KINDS, OWNER, POLICIES, ROUTABLE, SUBSCRIPTIONS, TEAMS
from bus_faults import faults_for_run, malform, misroute, scanner_messages, valid_copy, wrong_destination
from bus_owner import execute, owner_view
from bus_trace import fault_outcomes, ground_truth, records_for_investigator, score_investigator
from topologies.harbour import RISKS, SERVICES

# Targets per 0-based run index, from the schedule table in docs/specs/spec-issue-15-04-05/experiment-design.md.
SCHEDULE = [
    ("bookings-api.missing_timeout", "payments-gateway.hardcoded_secret", "pricing-engine.unpinned_dependency", "notifications.missing_timeout"),
    ("bookings-api.no_rate_limit", "customer-profiles.missing_backup", "admin-console.public_exposure", "notifications.unpinned_dependency"),
    ("payments-gateway.hardcoded_secret", "pricing-engine.unpinned_dependency", "notifications.missing_timeout", "maintenance-scheduler.missing_backup"),
    ("customer-profiles.missing_backup", "admin-console.public_exposure", "notifications.unpinned_dependency", "bookings-api.missing_timeout"),
    ("pricing-engine.unpinned_dependency", "notifications.missing_timeout", "maintenance-scheduler.missing_backup", "bookings-api.no_rate_limit"),
    ("admin-console.public_exposure", "notifications.unpinned_dependency", "bookings-api.missing_timeout", "payments-gateway.hardcoded_secret"),
    ("notifications.missing_timeout", "maintenance-scheduler.missing_backup", "bookings-api.no_rate_limit", "customer-profiles.missing_backup"),
    ("notifications.unpinned_dependency", "bookings-api.missing_timeout", "payments-gateway.hardcoded_secret", "pricing-engine.unpinned_dependency"),
    ("maintenance-scheduler.missing_backup", "bookings-api.no_rate_limit", "customer-profiles.missing_backup", "admin-console.public_exposure"),
    ("bookings-api.missing_timeout", "payments-gateway.hardcoded_secret", "pricing-engine.unpinned_dependency", "notifications.missing_timeout"),
]
MOVED_TO = ["pricing-engine", "maintenance-scheduler", "admin-console", "bookings-api", "payments-gateway", "payments-gateway", "notifications", "customer-profiles", "customer-profiles", "pricing-engine"]


def test_subscriptions_from_pristine_owners():
    assert len(SUBSCRIPTIONS) == 6 and all(SUBSCRIPTIONS.values())
    assert SUBSCRIPTIONS["Team Rentals"] == ["bookings-api", "pricing-engine"]
    assert all("fleet-telemetry" not in services for services in SUBSCRIPTIONS.values())
    assert OWNER["fleet-telemetry"] is None and len(ROUTABLE) == 9
    assert not any(risk_id.startswith("fleet-telemetry") for risk_id in ROUTABLE)
    assert set(TEAMS) == set(SUBSCRIPTIONS)


def test_fault_schedule():
    for index in range(9):
        faults = faults_for_run(index)
        assert len(set(faults.values())) == 4
        assert set(faults) == set(FAULT_KINDS)
    for kind in FAULT_KINDS:
        assert {faults_for_run(index)[kind] for index in range(9)} == set(ROUTABLE)
    assert faults_for_run(9) == faults_for_run(0)
    for index, targets in enumerate(SCHEDULE):
        faults = faults_for_run(index)
        assert tuple(faults[kind] for kind in FAULT_KINDS) == targets
        assert wrong_destination(faults["wrong-service"].split(".")[0]) == MOVED_TO[index]


def test_wrong_destination():
    for service in SERVICES:
        if service == "fleet-telemetry":
            continue
        moved = wrong_destination(service)
        assert moved != "fleet-telemetry" and OWNER[moved] != OWNER[service]
        assert wrong_destination(service) == moved


def test_scanner_is_deterministic_and_numbered_after_the_shuffle():
    first, risk_of = scanner_messages(3)
    assert scanner_messages(3)[0] == first and scanner_messages(4)[0] != first
    assert [item["message_id"] for item in first] == [f"m{n:02d}" for n in range(1, 12)]
    assert sorted(risk_of.values()) == sorted(risk.id for risk in RISKS)


def test_schema():
    bus = Bus(POLICIES["platform-owned"], SUBSCRIPTIONS)
    messages, _ = scanner_messages(0)
    for item in messages:
        assert bus.schema_problem(Message(item["message_id"], item["body"])) == ""
    body = messages[0]["body"]
    assert bus.schema_problem(Message("m01", malform(body)))
    assert bus.schema_problem(Message("m01", misroute(body))) == ""  # wrong-service passes by design


def published_bus(arm):
    messages, risk_of = scanner_messages(0)
    bus = Bus(POLICIES[arm], SUBSCRIPTIONS, risk_of)
    routes = {item["message_id"]: bus.publish(Message(item["message_id"], item["body"])) for item in messages}
    return bus, messages, routes


def test_publish_routes_by_arm():
    for arm, unroutable in (("no-owner", "discarded:no-subscriber"), ("steward-agent", "discarded:no-subscriber"), ("platform-owned", "dead-letter:no-subscriber")):
        bus, messages, routes = published_bus(arm)
        fleet = [item["message_id"] for item in messages if item["body"]["service"] == "fleet-telemetry"]
        assert [routes[message_id] for message_id in fleet] == [unroutable] * 2
        assert bus.undelivered == fleet
        assert bool(bus.store) == POLICIES[arm].keep_store
    bus = Bus(POLICIES["platform-owned"], SUBSCRIPTIONS)
    assert bus.publish(Message("x1", malform(scanner_messages(0)[0][0]["body"]))) == "invalid"
    assert len(bus.invalid) == 1 and bus.dead_letters == []


def test_owner_views():
    bus, messages, _ = published_bus("steward-agent")
    assert len(owner_view("steward-agent", bus)) == len(bus.store) == 11 + 2  # 11 published, 2 discarded
    assert owner_view("no-owner", published_bus("no-owner")[0]) == []
    platform, _, _ = published_bus("platform-owned")
    view = owner_view("platform-owned", platform)
    assert {item["channel"] for item in view} == {"dead_letter"} and len(view) == 2
    assert "event" not in view[0] and view[0]["reason"] == "no subscriber for the service"


def test_actions():
    for arm in ("steward-agent", "platform-owned"):
        bus, messages, routes = published_bus(arm)
        fleet = [item["message_id"] for item in messages if item["body"]["service"] == "fleet-telemetry"]
        bookings = next(item for item in messages if item["risk_id"] == "bookings-api.missing_timeout")
        visible = {item["message_id"] for item in owner_view(arm, bus)} | {bookings["message_id"]}
        before = len(bus.queues["Team Fleet"])
        log = execute(
            [
                {"action": "assign_owner", "service": "fleet-telemetry", "team": "Team Fleet", "reason": "unowned"},
                {"action": "assign_owner", "service": "bookings-api", "team": "Team Fleet", "reason": "already owned"},
                {"action": "route", "message_id": bookings["message_id"], "service": "payments-gateway", "reason": "test"},
                {"action": "return_to_producer", "message_id": bookings["message_id"], "reason": "test"},
                {"action": "redeliver", "message_id": "m99", "reason": "no such id"},
            ],
            bus=bus, visible_ids=visible,
        )
        assert log[0]["result"] == "assigned Team Fleet; queued 2 message(s)"
        assert len(bus.queues["Team Fleet"]) == before + 2
        assert [job.message.body["service"] for job in bus.queues["Team Fleet"]].count("fleet-telemetry") == 2
        assert log[1]["result"].startswith("ignored") and log[4]["result"].startswith("ignored")
        routed = bus.queues["Team Money"][-1].message
        assert routed.body["service"] == "payments-gateway" and routed.body["file"] == bookings["body"]["file"]
        returned = bus.queues["Team Rentals"][-1].message
        assert returned.body == valid_copy("bookings-api.missing_timeout")
        copies = [job.message for job in bus.queues["Team Fleet"] if job.message.body["service"] == "fleet-telemetry"] + [routed, returned]
        if arm == "platform-owned":
            assert routed.message_id == bookings["message_id"] and routed.correlation_id == bookings["message_id"] and routed.history == ["route:payments-gateway"]
            assert returned.history == ["return_to_producer"] and all(copy.correlation_id for copy in copies)
        else:
            assert all(copy.message_id.startswith("c") and not copy.correlation_id and not copy.history for copy in copies)
            assert bus.risk_of[routed.message_id] == "bookings-api.missing_timeout"
        assert bus.assignments == {"fleet-telemetry": "Team Fleet"} and fleet


def delivery(message_id, risk_id, outcome, *, duplicate=False):
    return Delivery(message_id, risk_id, "Team X", risk_id.split(".")[0], 1, 1, duplicate, outcome=outcome)


FAULTS = {"malformed": ROUTABLE[0], "wrong-service": ROUTABLE[2], "consumer-crash": ROUTABLE[4], "duplicate": ROUTABLE[6]}


def truth_for(fixed, deliveries):
    return ground_truth(faults=FAULTS, deliveries=deliveries, check_result=SimpleNamespace(fixed=fixed))


def test_ground_truth_and_fault_outcomes():
    everything = [risk.id for risk in RISKS]
    none_fixed = truth_for([], [])
    assert none_fixed["fleet-telemetry.no_owner"]["cause"] == "no_subscriber"
    assert none_fixed[FAULTS["malformed"]]["cause"] == "missing_fields"
    assert none_fixed[FAULTS["wrong-service"]]["cause"] == "wrong_team"
    assert none_fixed[FAULTS["consumer-crash"]]["cause"] == "consumer_failed"
    assert all(none_fixed[risk_id]["channel_caused"] for risk_id in ("fleet-telemetry.no_owner", FAULTS["malformed"]))
    assert none_fixed[ROUTABLE[1]]["cause"] == "unknown" and not none_fixed[ROUTABLE[1]]["channel_caused"]
    declined = truth_for([], [delivery("m1", ROUTABLE[1], "not_mine"), delivery("m1", ROUTABLE[1], "cannot_fix")])
    assert declined[ROUTABLE[1]]["cause"] == "consumer_declined"
    held = truth_for([], [delivery("m1", ROUTABLE[1], "fixed")])
    assert held[ROUTABLE[1]]["cause"] == "fix_did_not_hold"
    assigned = truth_for([], [delivery("m2", "fleet-telemetry.no_owner", "cannot_fix")])
    assert assigned["fleet-telemetry.no_owner"]["cause"] == "consumer_declined"
    # a loss target fixed through a sibling's message is absorbed; one processed fixed is recovered
    sibling = truth_for(everything, [delivery("m3", FAULTS["malformed"].replace("missing_timeout", "no_rate_limit"), "fixed")])
    assert sibling[FAULTS["malformed"]]["cause"] == "none"
    assert fault_outcomes(sibling, FAULTS) == {"malformed": "absorbed", "wrong-service": "absorbed", "consumer-crash": "absorbed"}
    recovered = truth_for(everything, [delivery("m4", FAULTS["consumer-crash"], "fixed")])
    assert fault_outcomes(recovered, FAULTS)["consumer-crash"] == "recovered"
    assert fault_outcomes(none_fixed, FAULTS) == {"malformed": "lost", "wrong-service": "lost", "consumer-crash": "lost"}


def test_investigator_scoring():
    truth = truth_for([], [delivery("m1", ROUTABLE[1], "fixed")])  # 5 channel losses (fleet x2 + 3 faults), one fix_did_not_hold
    losses = [risk_id for risk_id, item in truth.items() if item["channel_caused"]]
    assert len(losses) == 5
    perfect = {"risks": [{"risk_id": risk_id, "status": "lost", "cause": item["cause"]} for risk_id, item in truth.items()]}
    assert score_investigator(perfect, truth)["accuracy"] == 1.0
    wrong = {"risks": [{**item, "cause": "unknown"} if item["risk_id"] == losses[0] else item for item in perfect["risks"]]}
    assert score_investigator(wrong, truth)["correct"] == 4
    assured = {"risks": [{**item, "status": "handled", "cause": "none"} if item["risk_id"] in (losses[1], ROUTABLE[1]) else item for item in perfect["risks"]]}
    score = score_investigator(assured, truth)
    assert score["false_assurance_channel"] == 1 and score["false_assurance_fix"] == 1 and score["fix_did_not_hold"] == 1
    missing = {"risks": perfect["risks"][1:]}
    score = score_investigator(missing, truth)
    assert len(score["missing_risks"]) == 1
    assert score_investigator(None, truth)["correct"] == 0
    assert score_investigator(None, truth_for(list(truth), []))["accuracy"] is None


def test_strip_trace_removes_trace_context_and_relabels_copies_consistently():
    bus, messages, _ = published_bus("platform-owned")
    original = bus.messages[messages[0]["message_id"]]
    first, second = bus.copy_for_retry(original, "retry"), bus.copy_for_retry(original, "redeliver")
    for copy in (first, second):
        bus.record("delivered", copy, team="Team X")
    bus.dead_letter(first, "declined")
    assert records_for_investigator("platform-owned", bus).count("correlation_id") >= 3
    stripped = records_for_investigator("platform-owned", bus, strip_trace=True)
    assert "correlation_id" not in stripped and "history" not in stripped
    store_text, dead_text = stripped.split("Dead Letter Channel:\n")
    assert '"message_id": "c01"' in store_text and '"message_id": "c02"' in store_text
    assert '"message_id": "c01"' in dead_text  # the dead letter of the first copy keeps the id the store gave it
    assert store_text.count(f'"message_id": "{original.message_id}"') >= 1  # the original keeps its own id


def test_no_owner_records_are_the_team_logs():
    bus, messages, _ = published_bus("no-owner")
    text = records_for_investigator("no-owner", bus)
    assert text.count("Local log of ") == 6 and "(nothing received)" in text
    assert bus.store == []
