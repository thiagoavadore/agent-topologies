import pytest
import hier_helpers  # noqa: F401  (imported first: puts this folder at the front of sys.path)

from hier_config import CUTS, group_of, owner_team
from hier_harm import classify, coupling_ladder, harms, readable
from hier_hub import scratch_copy
from topologies.harbour import REGRESSION_NEEDS, SERVICES

PRICING, PAYMENTS = REGRESSION_NEEDS


def test_cuts_cover_every_service_once():
    for groups in CUTS.values():
        assigned = [service for services in groups.values() for service in services]
        assert sorted(assigned) == sorted(SERVICES)
        assert sorted(len(services) for services in groups.values()) == [2, 3, 3]


def test_by_team_splits_no_owner_team():
    teams = {}
    for service in SERVICES:
        team = owner_team(service)
        if team:
            teams.setdefault(team, set()).add(group_of("by-team", service))
    assert all(len(groups) == 1 for groups in teams.values()), teams
    assert owner_team("fleet-telemetry") is None


def test_by_runtime_splits_rentals_fleet_and_bookings_calls():
    group = lambda service: group_of("by-runtime", service)
    assert group("bookings-api") not in {group("pricing-engine"), group("payments-gateway")}
    assert group("maintenance-scheduler") != group("fleet-telemetry")
    assert owner_team("bookings-api") == owner_team("pricing-engine") and group("bookings-api") != group("pricing-engine")


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("http.default_timeout", "15s", [PRICING]),
        ("http.default_timeout", "300ms", []),
        ("http.default_rate_limit", "10/s", [PAYMENTS]),
        ("http.default_rate_limit", "30/s", []),
        ("http.default_rate_limit", "none", []),
        ("http.default_timeout", "fifteen seconds", []),
        ("backup.policy", "daily/30d", []),
    ],
)
def test_harms(key, value, expected):
    assert harms(key, value) == expected


def test_unreadable_value_is_flagged_not_harmful():
    assert not readable("http.default_timeout", "fifteen seconds") and readable("http.default_timeout", "15s")
    assert classify("http.default_timeout", "fifteen seconds", ["bookings-api"]) == {"harms": [], "unreadable": True}


def test_classify_inside_outside():
    def inside(cut_services):
        [harm] = classify("http.default_timeout", "15s", cut_services)["harms"]
        return harm["inside"]

    assert inside(["bookings-api"]) is False  # flat: the requester is its own subtree
    assert inside(CUTS["by-runtime"]["python"]) is False
    assert inside(CUTS["by-team"]["rentals-and-money"]) is True


def harmful(need):
    return {"harms": [{"need": need, "inside": False}]}


HARMLESS = {"harms": []}


def test_ladder_outcomes():
    cases = {
        "not-threatened": dict(worker_requests=[HARMLESS], requests_up=[HARMLESS], regressed=[], tiered=True),
        "absorbed-at-sub-lead": dict(worker_requests=[harmful(PRICING)], requests_up=[HARMLESS], regressed=[], tiered=True),
        "caught-at-top": dict(worker_requests=[harmful(PRICING)], requests_up=[harmful(PRICING)], regressed=[], tiered=True),
        "reached-hub": dict(worker_requests=[harmful(PRICING)], requests_up=[harmful(PRICING)], regressed=[PRICING], tiered=True),
        "introduced-by-top": dict(worker_requests=[HARMLESS], requests_up=[HARMLESS], regressed=[PRICING], tiered=True),
    }
    for outcome, case in cases.items():
        assert coupling_ladder(**case)[PRICING] == outcome
        assert coupling_ladder(**case)[PAYMENTS] == "not-threatened"
    flat = dict(worker_requests=[harmful(PRICING)], requests_up=[HARMLESS], regressed=[], tiered=False)
    assert coupling_ladder(**flat)[PRICING] == "not-threatened"


def test_checker_answer_key_not_in_scratch(tmp_path):
    repo = scratch_copy(tmp_path / "repo")
    assert not list(repo.rglob("CHECKER.md"))
    assert (repo / "CONTRACT.md").exists() and (repo / "platform.yaml").exists()
