import json

from hier_helpers import PRICING_REASON, Script, asking_workers, changed_files, forward, payload, protective_top, stable_commit  # noqa: F401

from hier import rotated, run_pair, run_worker
from hier_config import ARMS
from hier_hub import parse_hub, scratch_copy
from hier_prompts import needs_block
from topologies.harbour import FIXTURE, SERVICES
from topologies.model import ScriptedBackend

PRICING, PAYMENTS = "pricing-engine.demand_model_timeout", "payments-gateway.inbound_rate_per_caller"


def pair(script, arms=ARMS, run=1):
    return run_pair(run=run, backend=ScriptedBackend(script), backend_name="scripted", worker_model="w", lead_model="l", arms=arms)


def by_arm(rows):
    return {row["arm"]: row for row in rows}


def cut_script() -> Script:
    return Script(
        workers=asking_workers(),
        sub_leads={
            "rentals-and-money": forward(("http.default_timeout", "300ms", PRICING_REASON), ("http.default_rate_limit", "20/s", "public clients need a limit")),
            "customer-and-back-office": forward(("http.default_timeout", "5s", "the SMTP relay is slow")),
            "python": forward(("http.default_timeout", "15s", "charges take up to 12 s"), ("http.default_rate_limit", "20/s", "public clients need a limit")),
        },
        top=protective_top,
    )


def test_worker_mandate_and_override_strip(tmp_path):
    base = scratch_copy(tmp_path / "base")

    def bookings_edit(repo):
        text = (repo / "bookings-api/bookings_api.py").read_text()
        (repo / "bookings-api/bookings_api.py").write_text(text.replace('json=booking.to_payment())', 'json=booking.to_payment(), timeout=15)'))
        service_yaml = repo / "bookings-api/service.yaml"
        service_yaml.write_text(service_yaml.read_text() + "overrides:\n  http.default_timeout: 15s\n")

    files = changed_files("bookings-api", bookings_edit)
    files["bookings-api/service.yaml"] = files["bookings-api/service.yaml"].replace("description:", "description: edited", 1)
    reply = payload(files, [("http.default_timeout", 15, "charges take up to 12 s")])
    result = run_worker("bookings-api", backend=ScriptedBackend(Script(workers={"bookings-api": reply})), model="w", base=base)
    assert [item["path"] for item in result.mandate_rejections] == ["bookings-api/bookings_api.py"]
    assert "bookings-api/bookings_api.py" not in result.files
    assert result.override_edits == [{"service": "bookings-api", "key": "http.default_timeout", "value": "15s"}] and result.overrides_stripped
    assert "overrides: {}" in result.files["bookings-api/service.yaml"] and "description: edited" in result.files["bookings-api/service.yaml"]
    assert result.hub_requests == [{"key": "http.default_timeout", "value": "15", "reason": "charges take up to 12 s"}]
    assert result.status == "ok"


def test_workers_run_once_per_pair():
    script = cut_script()
    rows = pair(script)
    assert len(script.calls("worker")) == len(SERVICES)
    assert len({json.dumps(row["workers"], sort_keys=True) for row in rows}) == 1
    assert sorted(row["arm"] for row in rows) == sorted(ARMS)


def test_registry_reuses_by_runtime_sub_leads():
    script = cut_script()
    rows = by_arm(pair(script))
    assert len(script.calls("sub-lead")) == 6
    assert rows["by-runtime-registry"]["sub_leads"] == rows["by-runtime"]["sub_leads"]
    assert rows["by-runtime-registry"]["sub_leads_shared_with"] == "by-runtime" and rows["by-runtime"]["sub_leads_shared_with"] == ""
    only_registry = cut_script()
    pair(only_registry, arms=["by-runtime-registry"])
    assert len(only_registry.calls("sub-lead")) == 3


def test_sub_lead_sees_only_its_group():
    script = cut_script()
    pair(script, arms=["by-runtime"])
    prompt = next(prompt for key, _, prompt in script.calls("sub-lead") if key == "python")
    assert "=== bookings-api/service.yaml ===" in prompt and "=== notifications/service.yaml ===" in prompt
    assert "=== pricing-engine/service.yaml ===" not in prompt
    assert "charges take up to 12 s" in prompt and "the SMTP relay is slow" in prompt


def test_top_needs_only_in_flat_and_registry():
    seen = {}
    for arm in ARMS:
        script = cut_script()
        pair(script, arms=[arm])
        [(_, _, prompt)] = script.calls("top")
        seen[arm] = needs_block() in prompt
    assert seen == {"flat": True, "by-team": False, "by-runtime": False, "by-runtime-registry": True}


def test_top_not_called_without_requests():
    script = Script(top=lambda prompt: (_ for _ in ()).throw(AssertionError("the platform lead must not be called")))
    rows = pair(script)
    assert script.calls("top") == []
    base_hub = parse_hub((FIXTURE / "platform.yaml").read_text())
    for row in rows:
        assert row["top"]["called"] is False and row["escalations"] == 0 and row["final_hub"] == base_hub and row["top_prompt_chars"] == 0


def test_sub_lead_invalid_forwards_unchanged():
    script = cut_script()
    script.sub_leads["python"] = "not json at all"
    row = by_arm(pair(script, arms=["by-runtime"]))["by-runtime"]
    python = next(lead for lead in row["sub_leads"] if lead["group"] == "python")
    assert python["fallback"] == "forwarded-unchanged" and python["error"]
    assert [(r["key"], r["value"]) for r in python["requests"]] == [("http.default_timeout", "15s"), ("http.default_rate_limit", "20/s"), ("http.default_timeout", "5s")]
    assert [item["from"] for item in row["requests_up"]] == ["python"] * 3


def test_end_to_end_cut_decides_regression():
    rows = by_arm(pair(cut_script()))
    for arm in ("flat", "by-team", "by-runtime-registry"):
        assert rows[arm]["score"]["regressed"] == [], arm
        assert rows[arm]["final_hub"]["http.default_timeout"] == "300ms"
        assert rows[arm]["effective_timeouts"]["bookings-api"] == 15 and rows[arm]["effective_timeouts"]["notifications"] == 5
        assert rows[arm]["score"]["risks"]["bookings-api.missing_timeout"]["fixed"] and rows[arm]["score"]["risks"]["notifications.missing_timeout"]["fixed"]
        assert len(rows[arm]["overrides_granted"]) == 2
    broken = rows["by-runtime"]
    assert broken["score"]["regressed"] == [PRICING]
    assert broken["final_hub"]["http.default_timeout"] == "15s" and broken["overrides_granted"] == []
    assert broken["ladder"][PRICING] == "reached-hub"
    assert rows["by-team"]["ladder"][PRICING] == "caught-at-top"
    assert rows["flat"]["ladder"][PRICING] == "caught-at-top" and rows["by-runtime-registry"]["ladder"][PRICING] == "caught-at-top"
    assert all(row["ladder"][PAYMENTS] == "not-threatened" for row in rows.values())
    assert rows["flat"]["escalations"] == 3 and rows["by-runtime"]["escalations"] == 2
    assert rows["by-runtime"]["harmful_up_outside"] == 1 and rows["by-runtime"]["harmful_up_inside"] == 0
    assert rows["by-team"]["harmful_up_outside"] == 1 and rows["by-team"]["harmful_worker_requests"] == 2
    assert rows["by-team"]["management_tokens"] > rows["flat"]["management_tokens"] > 0


def test_arm_order_rotates():
    arms = list(ARMS)
    assert [rotated(arms, i)[0] for i in range(5)] == [*arms, arms[0]]
    assert sorted(rotated(arms, 3)) == sorted(arms)
    assert [row["arm"] for row in pair(cut_script(), run=2)] == rotated(arms, 1)
