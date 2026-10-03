import pytest
from fanout_helpers import BASE_CARDS, BASE_HUB, FIXES, all_fixes_applied, edited

from fixcheck import NEED_RULES, RISK_RULES, check, load_planted, parse_duration, parse_rate

PRICING_NEED = "pricing-engine:demand-model-300ms"
PAYMENTS_NEED = "payments-gateway:20-per-second"


def test_planted_file_and_rules_agree():
    assert load_planted() == set(RISK_RULES)
    assert len(RISK_RULES) == 12


def test_baseline_fixes_nothing_and_regresses_nothing():
    result = check(BASE_CARDS, BASE_HUB)
    assert result["risks_fixed"] == 0
    assert result["regressed"] == []


@pytest.mark.parametrize("card,category,old,new", FIXES, ids=[f"{card}:{category}" for card, category, *_ in FIXES])
def test_each_edit_fixes_exactly_its_risk(card, category, old, new):
    result = check(edited(BASE_CARDS, card, old, new), BASE_HUB)
    assert result["fixed"] == [f"{card}:{category}"]
    assert result["regressed"] == []


def test_all_edits_fix_all_twelve():
    result = check(all_fixes_applied(), BASE_HUB)
    assert result["risks_fixed"] == 12
    assert result["unfixed"] == []


def test_longer_global_timeout_breaks_pricing():
    result = check(all_fixes_applied(), {**BASE_HUB, "http.default_timeout": "5s"})
    assert result["regressed"] == [PRICING_NEED]


def test_pricing_override_saves_it_from_a_longer_global_timeout():
    cards = edited(all_fixes_applied(), "pricing-engine.md", "## Calls\n", "## Calls\nOverride: http.default_timeout = 250ms\n")
    assert check(cards, {**BASE_HUB, "http.default_timeout": "5s"})["regressed"] == []


def test_unparseable_timeout_counts_as_broken():
    assert check(all_fixes_applied(), {**BASE_HUB, "http.default_timeout": "soon"})["regressed"] == [PRICING_NEED]


def test_global_rate_below_20_breaks_payments():
    assert check(all_fixes_applied(), {**BASE_HUB, "http.default_rate_limit": "10/s"})["regressed"] == [PAYMENTS_NEED]
    assert check(all_fixes_applied(), {**BASE_HUB, "http.default_rate_limit": "600/min"})["regressed"] == [PAYMENTS_NEED]
    assert check(all_fixes_applied(), {**BASE_HUB, "http.default_rate_limit": "50/s"})["regressed"] == []


def test_payments_override_saves_it_from_a_low_global_rate():
    cards = edited(all_fixes_applied(), "payments-gateway.md", "## Limits\n", "## Limits\nOverride: http.default_rate_limit = 25/s\n")
    assert check(cards, {**BASE_HUB, "http.default_rate_limit": "10/s"})["regressed"] == []


def test_fix_by_hub_reference_needs_a_usable_hub_value():
    cards = edited(BASE_CARDS, "bookings-api.md", "json=booking.to_payment())", "json=booking.to_payment(), timeout=HUB['http.default_timeout'])")
    assert "bookings-api.md:missing_timeout" in check(cards, {**BASE_HUB, "http.default_timeout": "5s"})["fixed"]
    assert "bookings-api.md:missing_timeout" not in check(cards, {**BASE_HUB, "http.default_timeout": "none"})["fixed"]


@pytest.mark.parametrize("key,value", [("backup.policy", "daily, 30d retention"), ("availability.min_instances", "2"), ("dependencies.pinning", "lockfile")])
def test_hub_reference_fixes_need_the_hub_value(key, value):
    cards = edited(BASE_CARDS, "customer-profiles.md", "## Data\n", "## Data\nSnapshots follow `backup.policy` from the platform hub.\n")
    cards = edited(cards, "fleet-telemetry.md", "## Topology\n", "## Topology\nInstance count follows `availability.min_instances`.\n")
    cards = edited(cards, "pricing-engine.md", "## Calls\n", "## Calls\nVersions follow `dependencies.pinning`.\n")
    before = check(cards, BASE_HUB)["risks_fixed"]
    after = check(cards, {**BASE_HUB, key: value})["risks_fixed"]
    assert before == 0 and after == 1


def test_unchanged_wording_does_not_count_as_a_fix():
    # "no standby", "no second scheduler" and "No snapshot schedule" are the risks, not fixes.
    result = check(BASE_CARDS, BASE_HUB)
    assert "fleet-telemetry.md:single_point_of_failure" in result["unfixed"]
    assert "maintenance-scheduler.md:single_point_of_failure" in result["unfixed"]
    assert "customer-profiles.md:missing_backup" in result["unfixed"]


def test_broken_json_in_pricing_is_not_fixed():
    cards = edited(BASE_CARDS, "pricing-engine.md", '"pino": "9.5.0"', '"pino": "9.5.0",,')
    assert "pricing-engine.md:unpinned_dependency" not in check(cards, BASE_HUB)["fixed"]


def test_placeholder_owner_is_not_an_owner():
    cards = edited(BASE_CARDS, "fleet-telemetry.md", "Owner:\n", "Owner: TBD\n")
    assert "fleet-telemetry.md:no_owner" not in check(cards, BASE_HUB)["fixed"]


def test_renamed_heading_falls_back_to_the_whole_card():
    cards = edited(all_fixes_applied(), "notifications.md", "## Email sending", "## Mail")
    assert "notifications.md:missing_timeout" in check(cards, BASE_HUB)["fixed"]


def test_parsers():
    assert parse_duration("300ms") == pytest.approx(0.3)
    assert parse_duration("5 s") == 5
    assert parse_duration("2min") == 120
    assert parse_duration("none") is None
    assert parse_rate("20/s") == 20
    assert parse_rate("600 per minute") == 10
    assert parse_rate("fast") is None
    assert set(NEED_RULES) == {PRICING_NEED, PAYMENTS_NEED}
