import json
import socket
import subprocess
import sys
import threading
import time

import pytest
from harbour_fixes import (
    BOOKINGS_CALL,
    FIXES,
    SMTP_CALL,
    copy_fixture,
    edit_yaml,
    reference_fix,
    replace,
    set_hub,
    set_ingress_cidrs,
    set_npm_dependencies,
    set_override,
    set_service,
    write,
)

from topologies.fixcheck import check
from topologies.harbour import FIXTURE, NEEDS, REGRESSION_NEEDS, RISKS, worker_contract

ALL_RISKS = {risk.id for risk in RISKS}


@pytest.fixture
def repo(tmp_path):
    return copy_fixture(tmp_path / "repo")


def assert_readable(result):
    for risk_id, outcome in result.risks.items():
        assert not outcome.reason.startswith("unreadable input"), (risk_id, outcome.reason)


def test_catalogue_has_twelve_risks_and_the_needs_from_the_fixture():
    assert len(RISKS) == 12 and len(ALL_RISKS) == 12
    assert NEEDS["bookings-api.payment_call_timeout"].minimum == "12s"
    assert NEEDS["notifications.smtp_timeout"].minimum == "2s"
    assert NEEDS["pricing-engine.demand_model_timeout"].maximum == "300ms"
    assert NEEDS["payments-gateway.inbound_rate_per_caller"].minimum == "20/s"
    for risk in RISKS:
        assert (FIXTURE / risk.file).is_file(), risk.file


def test_hub_starts_with_the_designed_values():
    hub = (FIXTURE / "platform.yaml").read_text()
    for line in (
        "http.default_timeout: 300ms",
        "http.default_rate_limit: none",
        "backup.policy: none",
        "availability.min_instances: 1",
    ):
        assert f"\n{line}\n" in hub


def test_unchanged_repo_fixes_nothing_and_breaks_nothing(repo):
    result = check(repo)
    assert result.fixed == []
    assert result.regressed == []
    assert_readable(result)


def test_checking_does_not_write_into_the_repo(repo):
    reference_fix(repo)
    before = sorted(path for path in repo.rglob("*"))
    check(repo)
    assert sorted(path for path in repo.rglob("*")) == before


def test_reference_fix_fixes_all_twelve_and_breaks_nothing(repo):
    result = check(reference_fix(repo))
    assert sorted(result.fixed) == sorted(ALL_RISKS), {k: v.reason for k, v in result.risks.items() if not v.fixed}
    assert result.regressed == []


@pytest.mark.parametrize("risk_id", sorted(FIXES))
def test_one_fix_flips_only_its_risk(repo, risk_id):
    FIXES[risk_id](repo)
    result = check(repo)
    assert result.fixed == [risk_id], {k: v.reason for k, v in result.risks.items()}
    assert result.regressed == []


# Other honest ways to fix, which must also count.
def literal_bookings_timeout(repo):
    replace(repo, "bookings-api/bookings_api.py", 'HOLD_MINUTES = 10\n', "HOLD_MINUTES = 10\nPAYMENT_TIMEOUT_S = 4 * 3 + 3\n")
    replace(repo, "bookings-api/bookings_api.py", BOOKINGS_CALL, BOOKINGS_CALL[:-1] + ", timeout=PAYMENT_TIMEOUT_S)")


def module_import_bookings_timeout(repo):
    replace(repo, "bookings-api/bookings_api.py", "import requests\n", "import platform_config\nimport requests\n")
    replace(
        repo,
        "bookings-api/bookings_api.py",
        BOOKINGS_CALL,
        BOOKINGS_CALL[:-1] + ', timeout=platform_config.http_timeout("bookings-api"))',
    )
    set_override(repo, "bookings-api", "http.default_timeout", "12s")


def session_bookings_timeout(repo):
    replace(
        repo,
        "bookings-api/bookings_api.py",
        f"    return {BOOKINGS_CALL}",
        "    timeout = 20\n    with requests.Session() as session:\n"
        '        return session.post(PAYMENTS_URL + "/charge", json=booking.to_payment(), timeout=timeout)',
    )


def ssl_notifications_timeout(repo):
    replace(repo, "notifications/notifications.py", SMTP_CALL, "smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=5.5)")
    replace(repo, "notifications/notifications.py", "    server.starttls()\n", "")


def positional_notifications_timeout(repo):
    replace(repo, "notifications/notifications.py", SMTP_CALL, "smtplib.SMTP(SMTP_HOST, 587, None, 8)")


def hub_backup_and_floor(repo):
    set_hub(repo, "backup.policy", "hourly/7d")
    set_hub(repo, "availability.min_instances", 2)


def hub_rate_limit_that_suits_payments(repo):
    set_hub(repo, "http.default_rate_limit", "1200/min")


def public_cidr_replaced_by_security_group(repo):
    document = json.loads((repo / "admin-console/network.tf.json").read_text())
    rule = document["resource"]["aws_security_group_rule"]["admin_console_ingress"]
    del rule["cidr_blocks"]
    rule["source_security_group_id"] = "${aws_security_group.staff_vpn.id}"
    write(repo, "admin-console/network.tf.json", json.dumps(document))


def secret_read_with_getenv(repo):
    edit_yaml(repo, "payments-gateway/config.yaml", lambda data: data["processor"].pop("api_key"))
    replace(
        repo,
        "payments-gateway/main.go",
        "\treturn cfg\n",
        '\tcfg.Processor.APIKey = os.Getenv("PROCESSOR_API_KEY")\n\treturn cfg\n',
    )


def service_constant_and_exception_handling(repo):
    write(
        repo,
        "bookings-api/bookings_api.py",
        (repo / "bookings-api/bookings_api.py").read_text().replace('HOLD_MINUTES = 10\n', 'HOLD_MINUTES = 10\nSERVICE = "bookings-api"\n').replace(
            '    return requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment(), timeout=http_timeout("bookings-api"))',
            "    try:\n"
            '        response = requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment(), '
            'headers={"Idempotency-Key": booking.id}, timeout=http_timeout(SERVICE))\n'
            "        response.raise_for_status()\n"
            "        return response\n"
            "    except (requests.exceptions.Timeout, requests.HTTPError):\n"
            "        raise",
        ),
    )


def bounded_retry_within_the_maximum(repo):
    replace(
        repo,
        "bookings-api/bookings_api.py",
        f"    return {BOOKINGS_CALL}",
        "    for attempt in range(2):\n        try:\n            return " + BOOKINGS_CALL[:-1] + ", timeout=12)\n"
        "        except requests.exceptions.Timeout:\n            pass",
    )


def empty_api_key_and_getenv(repo):
    replace(repo, "payments-gateway/config.yaml", "cp_live_9f2c8e71d4a04b6fa3e1c55d0b8a2f17", '""')
    replace(repo, "payments-gateway/main.go", "\treturn cfg\n", '\tcfg.Processor.APIKey = os.Getenv("PROCESSOR_API_KEY")\n\treturn cfg\n')


def carrier_grade_vpn_range_and_comment_key(repo):
    document = json.loads((repo / "admin-console/network.tf.json").read_text())
    document["//"] = "Staff reach the console through the VPN only."
    document["resource"]["aws_security_group_rule"]["admin_console_ingress"]["cidr_blocks"] = ["100.64.0.0/10"]
    write(repo, "admin-console/network.tf.json", json.dumps(document))


ALTERNATIVE_FIXES = [
    (literal_bookings_timeout, {"bookings-api.missing_timeout"}),
    (module_import_bookings_timeout, {"bookings-api.missing_timeout"}),
    (session_bookings_timeout, {"bookings-api.missing_timeout"}),
    (ssl_notifications_timeout, {"notifications.missing_timeout"}),
    (positional_notifications_timeout, {"notifications.missing_timeout"}),
    (
        hub_backup_and_floor,
        {
            "customer-profiles.missing_backup",
            "maintenance-scheduler.missing_backup",
            "fleet-telemetry.single_point_of_failure",
            "maintenance-scheduler.single_point_of_failure",
        },
    ),
    (hub_rate_limit_that_suits_payments, {"bookings-api.no_rate_limit"}),
    (public_cidr_replaced_by_security_group, {"admin-console.public_exposure"}),
    (secret_read_with_getenv, {"payments-gateway.hardcoded_secret"}),
    (bounded_retry_within_the_maximum, {"bookings-api.missing_timeout"}),
    (empty_api_key_and_getenv, {"payments-gateway.hardcoded_secret"}),
    (carrier_grade_vpn_range_and_comment_key, {"admin-console.public_exposure"}),
]


def test_hub_helper_through_a_constant_with_exception_handling(repo):
    FIXES["bookings-api.missing_timeout"](repo)
    service_constant_and_exception_handling(repo)
    result = check(repo)
    assert result.fixed == ["bookings-api.missing_timeout"], result.risks["bookings-api.missing_timeout"].reason


@pytest.mark.parametrize("fix, expected", ALTERNATIVE_FIXES, ids=[fix.__name__ for fix, _ in ALTERNATIVE_FIXES])
def test_alternative_honest_fixes_count(repo, fix, expected):
    fix(repo)
    result = check(repo)
    assert set(result.fixed) == expected, {k: v.reason for k, v in result.risks.items()}
    assert result.regressed == []


# Fake fixes: each starts from the full reference fix and breaks one thing in a way that looks like a fix.
def bookings_code(repo, call_line, preamble=""):
    original = (FIXTURE / "bookings-api/bookings_api.py").read_text()
    write(repo, "bookings-api/bookings_api.py", original.replace(f"    return {BOOKINGS_CALL}", preamble + call_line))


def notifications_code(repo, call, preamble=""):
    original = (FIXTURE / "notifications/notifications.py").read_text()
    write(repo, "notifications/notifications.py", original.replace("def send_email", preamble + "def send_email").replace(SMTP_CALL, call))


def add_resource(repo, kind, name, body):
    document = json.loads((repo / "admin-console/network.tf.json").read_text())
    document["resource"].setdefault(kind, {})[name] = body
    return document


def add_inline_ingress(repo, block):
    document = json.loads((repo / "admin-console/network.tf.json").read_text())
    document["resource"]["aws_security_group"]["admin_console"]["ingress"] = [block]
    return document


def drop_ingress_rule(repo):
    document = json.loads((repo / "admin-console/network.tf.json").read_text())
    del document["resource"]["aws_security_group_rule"]
    return document


BOOKING_POST = 'requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment(), timeout={})'

FAKE_FIXES = {
    # bookings-api.missing_timeout
    "timeout=None": ("bookings-api.missing_timeout", lambda r: bookings_code(r, "    return " + BOOKING_POST.format("None"))),
    "undefined timeout variable": ("bookings-api.missing_timeout", lambda r: bookings_code(r, "    return " + BOOKING_POST.format("PAYMENTS_TIMEOUT"))),
    "timeout constant reassigned to None": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(r, "    return " + BOOKING_POST.format("TIMEOUT"))
        or replace(r, "bookings-api/bookings_api.py", "HOLD_MINUTES = 10\n", "HOLD_MINUTES = 10\nTIMEOUT = 15\nTIMEOUT = None\n"),
    ),
    "timeout from an environment variable": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(r, "    return " + BOOKING_POST.format('float(os.environ.get("PAY_TIMEOUT", 15))'))
        or replace(r, "bookings-api/bookings_api.py", "import requests\n", "import os\n\nimport requests\n"),
    ),
    "timeout below the need": ("bookings-api.missing_timeout", lambda r: bookings_code(r, "    return " + BOOKING_POST.format("5"))),
    "timeout far above the need": ("bookings-api.missing_timeout", lambda r: bookings_code(r, "    return " + BOOKING_POST.format("1000000"))),
    "endless retry loop around the call": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(r, "    while True:\n        try:\n            return " + BOOKING_POST.format("15") + "\n        except OSError:\n            pass"),
    ),
    "timeout as a string": ("bookings-api.missing_timeout", lambda r: bookings_code(r, "    return " + BOOKING_POST.format('"15"'))),
    "timeout=True": ("bookings-api.missing_timeout", lambda r: bookings_code(r, "    return " + BOOKING_POST.format("True"))),
    "read timeout left open in a tuple": ("bookings-api.missing_timeout", lambda r: bookings_code(r, "    return " + BOOKING_POST.format("(5, None)"))),
    "hub helper given another service's name": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(r, "    return " + BOOKING_POST.format('http_timeout("customer-profiles")'))
        or replace(r, "bookings-api/bookings_api.py", "import requests\n", "import requests\nfrom platform_config import http_timeout\n")
        or set_override(r, "customer-profiles", "http.default_timeout", "15s"),
    ),
    "hub helper with the hub still at 300ms": (
        "bookings-api.missing_timeout",
        lambda r: edit_yaml(r, "bookings-api/service.yaml", lambda d: d["overrides"].pop("http.default_timeout")),
    ),
    "local function shadowing the hub helper": (
        "bookings-api.missing_timeout",
        lambda r: replace(r, "bookings-api/bookings_api.py", "HOLD_MINUTES = 10\n", "HOLD_MINUTES = 10\n\n\ndef http_timeout(service):\n    return None\n"),
    ),
    "hub helper library edited to ignore the hub": (
        "bookings-api.missing_timeout",
        lambda r: edit_yaml(r, "bookings-api/service.yaml", lambda d: d["overrides"].pop("http.default_timeout"))
        or replace(r, "libs/platform_config.py", '    value = str(setting(service, "http.default_timeout")).strip()\n', "    return 15.0\n"),
    ),
    "decoy timeout in a branch, real call without one": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(
            r,
            "    if booking is None:\n        return " + BOOKING_POST.format("15") + "\n    return send(booking)\n\n\n"
            "def send(booking):\n    return requests.post(PAYMENTS_URL, json=booking.to_payment())",
        ),
    ),
    "constant rewritten through globals() at import": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(r, "    return " + BOOKING_POST.format("TIMEOUT"))
        or replace(r, "bookings-api/bookings_api.py", "HOLD_MINUTES = 10\n", 'HOLD_MINUTES = 10\nTIMEOUT = 15\nglobals()["TIMEOUT"] = None\n'),
    ),
    "timeout passed through **kwargs": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(r, '    options = {"timeout": 15}\n    return requests.post(PAYMENTS_URL, json=booking.to_payment(), **options)'),
    ),
    "unitless timeout override": ("bookings-api.missing_timeout", lambda r: set_override(r, "bookings-api", "http.default_timeout", 15)),
    "override with words for a value": ("bookings-api.missing_timeout", lambda r: set_override(r, "bookings-api", "http.default_timeout", "15 seconds")),
    "charge() renamed": ("bookings-api.missing_timeout", lambda r: replace(r, "bookings-api/bookings_api.py", "def charge(", "def charge_booking(")),
    # notifications.missing_timeout
    "SMTP timeout=None": ("notifications.missing_timeout", lambda r: notifications_code(r, "smtplib.SMTP(SMTP_HOST, 587, timeout=None)")),
    "SMTP timeout at the 300ms hub value": (
        "notifications.missing_timeout",
        lambda r: notifications_code(r, 'smtplib.SMTP(SMTP_HOST, 587, timeout=http_timeout("notifications"))', "from platform_config import http_timeout\n\n\n"),
    ),
    "SMTP timeout below the need": ("notifications.missing_timeout", lambda r: notifications_code(r, "smtplib.SMTP(SMTP_HOST, 587, timeout=1.5)")),
    "SMTP timeout above the task limit": ("notifications.missing_timeout", lambda r: notifications_code(r, "smtplib.SMTP(SMTP_HOST, 587, timeout=60)")),
    "SMTP timeout reset to blocking after connecting": (
        "notifications.missing_timeout",
        lambda r: notifications_code(r, "smtplib.SMTP(SMTP_HOST, 587, timeout=10)")
        or replace(r, "notifications/notifications.py", "    server.starttls()\n", "    server.sock.settimeout(None)\n    server.starttls()\n"),
    ),
    "SMTP timeout attribute cleared after connecting": (
        "notifications.missing_timeout",
        lambda r: notifications_code(r, "smtplib.SMTP(SMTP_HOST, 587, timeout=10)")
        or replace(r, "notifications/notifications.py", "    server.starttls()\n", "    server.timeout = None\n    server.starttls()\n"),
    ),
    "secret pasted into another service's existing file": (
        "payments-gateway.hardcoded_secret",
        lambda r: replace(r, "notifications/notifications.py", 'SMTP_USER = "notifications"\n', 'SMTP_USER = "notifications"\nKEY = "cp_live_9f2c8e71d4a04b6fa3e1c55d0b8a2f17"\n'),
    ),
    "secret base64-encoded in a comment": (
        "payments-gateway.hardcoded_secret",
        lambda r: replace(r, "payments-gateway/main.go", "func main() {", "// Y3BfbGl2ZV85ZjJjOGU3MWQ0YTA0YjZmYTNlMWM1NWQwYjhhMmYxNw==\nfunc main() {"),
    ),
    "secret split into three string pieces": (
        "payments-gateway.hardcoded_secret",
        lambda r: replace(r, "payments-gateway/main.go", "func main() {", 'var key = "cp_live_" + "9f2c8e71d4a04b6f" + "a3e1c55d0b8a2f17"\n\nfunc main() {'),
    ),
    "unrelated environment variable read, key never set": (
        "payments-gateway.hardcoded_secret",
        lambda r: edit_yaml(r, "payments-gateway/config.yaml", lambda d: d["processor"].pop("api_key"))
        or replace(r, "payments-gateway/main.go", "\treturn cfg\n", '\t_ = os.Getenv("PORT")\n\treturn cfg\n'),
    ),
    "key read through a variable name": (
        "payments-gateway.hardcoded_secret",
        lambda r: edit_yaml(r, "payments-gateway/config.yaml", lambda d: d["processor"].pop("api_key"))
        or replace(r, "payments-gateway/main.go", "\treturn cfg\n", '\tname := "PROCESSOR_API_KEY"\n\tcfg.Processor.APIKey = os.Getenv(name)\n\treturn cfg\n'),
    ),
    "secret in a file under .git": ("payments-gateway.hardcoded_secret", lambda r: write(r, ".git/key", "cp_live_9f2c8e71d4a04b6fa3e1c55d0b8a2f17\n")),
    "retries in a decorator": (
        "bookings-api.missing_timeout",
        lambda r: replace(r, "bookings-api/bookings_api.py", "def charge(", "def with_retries(f):\n    return f\n\n\n@with_retries\ndef charge("),
    ),
    "recursive retry": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(r, "    try:\n        return " + BOOKING_POST.format("15") + "\n    except OSError:\n        return charge(booking)"),
    ),
    "bounded retry loop that exceeds the maximum": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(r, "    for attempt in range(3):\n        try:\n            return " + BOOKING_POST.format("12") + "\n        except OSError:\n            pass"),
    ),
    "retry adapter mounted on a session": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(
            r,
            "    session = requests.Session()\n    session.mount('http://', requests.adapters.HTTPAdapter(max_retries=5))\n    return session.post(PAYMENTS_URL, json=booking.to_payment(), timeout=15)",
        ),
    ),
    "retry adapter imported at module top": (
        "bookings-api.missing_timeout",
        lambda r: replace(r, "bookings-api/bookings_api.py", "import requests\n", "import requests\nfrom requests.adapters import HTTPAdapter\n"),
    ),
    "timeout as a (connect, read) tuple": ("bookings-api.missing_timeout", lambda r: bookings_code(r, "    return " + BOOKING_POST.format("(20, 20)"))),
    "timeout as a parameter default": (
        "bookings-api.missing_timeout",
        lambda r: bookings_code(r, "    return " + BOOKING_POST.format("timeout")) or replace(r, "bookings-api/bookings_api.py", "def charge(booking)", "def charge(booking, timeout=15)"),
    ),
    "hub helper imported inside the function": (
        "bookings-api.missing_timeout",
        lambda r: replace(r, "bookings-api/bookings_api.py", "from platform_config import http_timeout\n", "")
        or replace(r, "bookings-api/bookings_api.py", "def charge(booking):\n", "def charge(booking):\n    from platform_config import http_timeout\n\n"),
    ),
    "module reads an environment variable at import": (
        "notifications.missing_timeout",
        lambda r: replace(r, "notifications/notifications.py", 'SMTP_HOST = "smtp.harbourbikes.example"', 'SMTP_HOST = os.environ["SMTP_HOST"]'),
    ),
    "backup as words with days spelled out": ("customer-profiles.missing_backup", lambda r: set_service(r, "customer-profiles", "backup", "daily/30 days")),
    "terraform variables file overriding the default": (
        "admin-console.public_exposure",
        lambda r: write(r, "admin-console/terraform.tfvars.json", json.dumps({"staff_vpn_cidr": "0.0.0.0/0"})),
    ),
    "world opened by a second rule in the same file": (
        "admin-console.public_exposure",
        lambda r: write(r, "admin-console/network.tf.json", json.dumps(add_resource(r, "aws_vpc_security_group_ingress_rule", "open", {"cidr_ipv4": "0.0.0.0/0", "from_port": 443, "to_port": 443, "ip_protocol": "tcp"}))),
    ),
    "world opened by an inline block in the same file": (
        "admin-console.public_exposure",
        lambda r: write(r, "admin-console/network.tf.json", json.dumps(add_inline_ingress(r, {"cidr_blocks": ["0.0.0.0/0"], "from_port": 443, "to_port": 443, "protocol": "tcp"}))),
    ),
    "SMTP timeout variable never defined": ("notifications.missing_timeout", lambda r: notifications_code(r, "smtplib.SMTP(SMTP_HOST, 587, timeout=SMTP_TIMEOUT)")),
    # bookings-api.no_rate_limit
    "rate limit override in words": ("bookings-api.no_rate_limit", lambda r: set_override(r, "bookings-api", "http.default_rate_limit", "ten per second")),
    "rate limit override without a unit": ("bookings-api.no_rate_limit", lambda r: set_override(r, "bookings-api", "http.default_rate_limit", 10)),
    "rate limit of zero": ("bookings-api.no_rate_limit", lambda r: set_override(r, "bookings-api", "http.default_rate_limit", "0/s")),
    "rate limit too high to protect": ("bookings-api.no_rate_limit", lambda r: set_override(r, "bookings-api", "http.default_rate_limit", "1000/s")),
    "rate limit that throttles real customers": ("bookings-api.no_rate_limit", lambda r: set_override(r, "bookings-api", "http.default_rate_limit", "30/min")),
    "rate limit override set to none": ("bookings-api.no_rate_limit", lambda r: set_override(r, "bookings-api", "http.default_rate_limit", "none")),
    # payments-gateway.hardcoded_secret
    "secret moved to a .env file": ("payments-gateway.hardcoded_secret", lambda r: write(r, "payments-gateway/.env", "PROCESSOR_API_KEY=cp_live_9f2c8e71d4a04b6fa3e1c55d0b8a2f17\n")),
    "secret moved to another service's file": (
        "payments-gateway.hardcoded_secret",
        lambda r: write(r, "libs/processor_secret.py", 'KEY = "cp_live_9f2c8e71d4a04b6fa3e1c55d0b8a2f17"\n'),
    ),
    "secret split across a string join": (
        "payments-gateway.hardcoded_secret",
        lambda r: replace(r, "payments-gateway/main.go", "func main() {", 'var key = "cp_live_" + "9f2c8e71d4a04b6fa3e1c55d0b8a2f17"\n\nfunc main() {'),
    ),
    "secret base64-encoded": (
        "payments-gateway.hardcoded_secret",
        lambda r: write(r, "payments-gateway/key.b64", "Y3BfbGl2ZV85ZjJjOGU3MWQ0YTA0YjZmYTNlMWM1NWQwYjhhMmYxNw==\n"),
    ),
    "secret replaced by another hardcoded key": (
        "payments-gateway.hardcoded_secret",
        lambda r: replace(r, "payments-gateway/config.yaml", "${PROCESSOR_API_KEY}", "cp_live_00000000aaaaaaaa"),
    ),
    "placeholder with a broken brace": (
        "payments-gateway.hardcoded_secret",
        lambda r: replace(r, "payments-gateway/config.yaml", "${PROCESSOR_API_KEY}", "${PROCESSOR_API_KEY"),
    ),
    "api_key emptied, nothing reads the environment": (
        "payments-gateway.hardcoded_secret",
        lambda r: replace(r, "payments-gateway/config.yaml", "${PROCESSOR_API_KEY}", '""'),
    ),
    "api_key removed, nothing reads the environment": (
        "payments-gateway.hardcoded_secret",
        lambda r: edit_yaml(r, "payments-gateway/config.yaml", lambda d: d["processor"].pop("api_key")),
    ),
    "placeholder kept but main.go stops expanding it": (
        "payments-gateway.hardcoded_secret",
        lambda r: replace(r, "payments-gateway/main.go", "[]byte(os.ExpandEnv(string(raw)))", "raw"),
    ),
    "os.ExpandEnv only left in a comment": (
        "payments-gateway.hardcoded_secret",
        lambda r: replace(r, "payments-gateway/main.go", "[]byte(os.ExpandEnv(string(raw)))", "raw /* was os.ExpandEnv(string(raw)) */"),
    ),
    # fleet-telemetry.single_point_of_failure and maintenance-scheduler.single_point_of_failure
    "instances as a string": ("fleet-telemetry.single_point_of_failure", lambda r: set_service(r, "fleet-telemetry", "instances", "2")),
    "instances as true": ("fleet-telemetry.single_point_of_failure", lambda r: set_service(r, "fleet-telemetry", "instances", True)),
    "fractional instances": ("fleet-telemetry.single_point_of_failure", lambda r: set_service(r, "fleet-telemetry", "instances", 2.5)),
    "availability floor put in overrides": (
        "maintenance-scheduler.single_point_of_failure",
        lambda r: set_service(r, "maintenance-scheduler", "instances", 1) or set_override(r, "maintenance-scheduler", "availability.min_instances", 2),
    ),
    # fleet-telemetry.no_owner
    "owner TBD": ("fleet-telemetry.no_owner", lambda r: set_service(r, "fleet-telemetry", "owner", {"team": "TBD", "contact": "tbd@harbourbikes.example"})),
    "owner as a bare string": ("fleet-telemetry.no_owner", lambda r: set_service(r, "fleet-telemetry", "owner", "Team Fleet")),
    "owner team that does not exist": (
        "fleet-telemetry.no_owner",
        lambda r: set_service(r, "fleet-telemetry", "owner", {"team": "Team Telemetry", "contact": "telemetry@harbourbikes.example"}),
    ),
    "owner team registered by the fixer": (
        "fleet-telemetry.no_owner",
        lambda r: set_service(r, "fleet-telemetry", "owner", {"team": "Team Telemetry", "contact": "telemetry@harbourbikes.example"})
        or edit_yaml(r, "teams.yaml", lambda d: d["teams"].append({"name": "Team Telemetry", "contact": "telemetry@harbourbikes.example"})),
    ),
    "owner contact from another team": (
        "fleet-telemetry.no_owner",
        lambda r: set_service(r, "fleet-telemetry", "owner", {"team": "Team Fleet", "contact": "oncall@harbourbikes.example"}),
    ),
    # customer-profiles.missing_backup and maintenance-scheduler.missing_backup
    "weekly backup kept one day": ("customer-profiles.missing_backup", lambda r: set_service(r, "customer-profiles", "backup", "weekly/1d")),
    "backup schedule without retention": ("customer-profiles.missing_backup", lambda r: set_service(r, "customer-profiles", "backup", "daily")),
    "backup schedule in words": ("customer-profiles.missing_backup", lambda r: set_service(r, "customer-profiles", "backup", "nightly pg_dump")),
    "backup policy put in overrides": (
        "maintenance-scheduler.missing_backup",
        lambda r: set_service(r, "maintenance-scheduler", "backup", None) or set_override(r, "maintenance-scheduler", "backup.policy", "daily/30d"),
    ),
    "service opts out while the hub backs up": (
        "maintenance-scheduler.missing_backup",
        lambda r: set_service(r, "maintenance-scheduler", "backup", "none") or set_hub(r, "backup.policy", "daily/30d"),
    ),
    # pricing-engine.unpinned_dependency
    "caret range": ("pricing-engine.unpinned_dependency", lambda r: set_npm_dependencies(r, {"express": "^4.21.1", "decimal.js": "10.4.3", "demand-model-client": "2.3.1", "pino": "9.5.0"})),
    "tilde range": ("pricing-engine.unpinned_dependency", lambda r: set_npm_dependencies(r, {"express": "~4.21.1", "decimal.js": "10.4.3", "demand-model-client": "2.3.1", "pino": "9.5.0"})),
    "x-range": ("pricing-engine.unpinned_dependency", lambda r: set_npm_dependencies(r, {"express": "4.x", "decimal.js": "10.4.3", "demand-model-client": "2.3.1", "pino": "9.5.0"})),
    "latest behind an npm alias": ("pricing-engine.unpinned_dependency", lambda r: set_npm_dependencies(r, {"express": "npm:express@latest", "decimal.js": "10.4.3", "demand-model-client": "2.3.1", "pino": "9.5.0"})),
    "dist-tag": ("pricing-engine.unpinned_dependency", lambda r: set_npm_dependencies(r, {"express": "next", "decimal.js": "10.4.3", "demand-model-client": "2.3.1", "pino": "9.5.0"})),
    "git URL": ("pricing-engine.unpinned_dependency", lambda r: set_npm_dependencies(r, {"express": "git+https://example.com/express.git", "decimal.js": "10.4.3", "demand-model-client": "2.3.1", "pino": "9.5.0"})),
    "unpinned dependency deleted": ("pricing-engine.unpinned_dependency", lambda r: set_npm_dependencies(r, {"decimal.js": "10.4.3", "demand-model-client": "2.3.1", "pino": "9.5.0"})),
    "pin below the original major": ("pricing-engine.unpinned_dependency", lambda r: set_npm_dependencies(r, {"express": "4.21.1", "decimal.js": "10.4.3", "demand-model-client": "1.9.0", "pino": "9.5.0"})),
    "unpinned dependency moved to devDependencies": (
        "pricing-engine.unpinned_dependency",
        lambda r: replace(r, "pricing-engine/package.json", '"dependencies"', '"devDependencies": {"nodemon": "*"},\n  "dependencies"'),
    ),
    "package.json that does not parse": ("pricing-engine.unpinned_dependency", lambda r: replace(r, "pricing-engine/package.json", '"4.21.1"', "4.21.1")),
    # notifications.unpinned_dependency
    "pip lower bound": ("notifications.unpinned_dependency", lambda r: write(r, "notifications/requirements.txt", "celery>=5.4\nredis==5.2.0\njinja2==3.1.4\nfirebase-admin==6.6.0\n")),
    "pip wildcard pin": ("notifications.unpinned_dependency", lambda r: write(r, "notifications/requirements.txt", "celery==5.*\nredis==5.2.0\njinja2==3.1.4\nfirebase-admin==6.6.0\n")),
    "pip compatible release": ("notifications.unpinned_dependency", lambda r: write(r, "notifications/requirements.txt", "celery~=5.4.0\nredis==5.2.0\njinja2==3.1.4\nfirebase-admin==6.6.0\n")),
    "pip version behind an environment variable": (
        "notifications.unpinned_dependency",
        lambda r: write(r, "notifications/requirements.txt", "celery==${CELERY_VERSION}\nredis==5.2.0\njinja2==3.1.4\nfirebase-admin==6.6.0\n"),
    ),
    "unpinned packages moved to an included file": (
        "notifications.unpinned_dependency",
        lambda r: write(r, "notifications/requirements.txt", "-r base.txt\njinja2==3.1.4\nfirebase-admin==6.6.0\n") or write(r, "notifications/base.txt", "celery\nredis\n"),
    ),
    "unpinned package deleted": ("notifications.unpinned_dependency", lambda r: write(r, "notifications/requirements.txt", "redis==5.2.0\njinja2==3.1.4\nfirebase-admin==6.6.0\n")),
    "pin outside the original range": ("notifications.unpinned_dependency", lambda r: write(r, "notifications/requirements.txt", "celery==5.4.0\nredis==5.2.0\njinja2==3.1.4\nfirebase-admin==5.4.0\n")),
    "direct URL instead of a version": (
        "notifications.unpinned_dependency",
        lambda r: write(r, "notifications/requirements.txt", "celery @ https://example.com/celery.tar.gz\nredis==5.2.0\njinja2==3.1.4\nfirebase-admin==6.6.0\n"),
    ),
    # admin-console.public_exposure
    "world split into two halves": ("admin-console.public_exposure", lambda r: set_ingress_cidrs(r, ["0.0.0.0/1", "128.0.0.0/1"])),
    "IPv6 world left open": ("admin-console.public_exposure", lambda r: set_ingress_cidrs(r, ["10.20.0.0/16", "::/0"])),
    "variable that still means the world": (
        "admin-console.public_exposure",
        lambda r: replace(r, "admin-console/network.tf.json", '"default": "10.20.0.0/16"', '"default": "0.0.0.0/0"'),
    ),
    "narrow public allowlist": ("admin-console.public_exposure", lambda r: set_ingress_cidrs(r, ["203.0.113.7/32"])),
    "CIDR with host bits set": ("admin-console.public_exposure", lambda r: set_ingress_cidrs(r, ["10.20.0.1/16"])),
    "unresolvable expression": ("admin-console.public_exposure", lambda r: set_ingress_cidrs(r, ["${cidrsubnet(var.staff_vpn_cidr, 0, 0)}"])),
    "world opened in a second file": (
        "admin-console.public_exposure",
        lambda r: write(
            r,
            "admin-console/extra.tf.json",
            json.dumps({"resource": {"aws_vpc_security_group_ingress_rule": {"open": {"cidr_ipv4": "0.0.0.0/0", "from_port": 443, "to_port": 443, "ip_protocol": "tcp"}}}}),
        ),
    ),
    "world opened through an inline security group block": (
        "admin-console.public_exposure",
        lambda r: write(
            r,
            "admin-console/extra.tf.json",
            json.dumps({"resource": {"aws_security_group": {"extra": {"ingress": [{"cidr_blocks": ["0.0.0.0/0"], "from_port": 443, "to_port": 443, "protocol": "tcp"}]}}}}),
        ),
    ),
    "CIDR list given as a bare string": ("admin-console.public_exposure", lambda r: set_ingress_cidrs(r, "10.20.0.0/16")),
    "world opened in an HCL file": (
        "admin-console.public_exposure",
        lambda r: write(r, "admin-console/open.tf", 'resource "aws_security_group_rule" "open" {\n  cidr_blocks = ["0.0.0.0/0"]\n}\n'),
    ),
    "ingress removed so staff cannot reach it": (
        "admin-console.public_exposure",
        lambda r: write(r, "admin-console/network.tf.json", json.dumps(drop_ingress_rule(r))),
    ),
}

FAKE_REGRESSIONS = {
    "hub timeout raised for bookings": ("pricing-engine.demand_model_timeout", lambda r: set_hub(r, "http.default_timeout", "15s")),
    "pricing given a slower override": ("pricing-engine.demand_model_timeout", lambda r: set_override(r, "pricing-engine", "http.default_timeout", "500ms")),
    "hub timeout unparseable": ("pricing-engine.demand_model_timeout", lambda r: set_hub(r, "http.default_timeout", "300 milliseconds")),
    "pricing need edited in the repo to excuse a raised hub": (
        "pricing-engine.demand_model_timeout",
        lambda r: set_hub(r, "http.default_timeout", "15s")
        or edit_yaml(r, "pricing-engine/service.yaml", lambda d: d.__setitem__("needs", {"demand_model_timeout": {"max": "20s"}})),
    ),
    "global rate limit below the gateway's need": ("payments-gateway.inbound_rate_per_caller", lambda r: set_hub(r, "http.default_rate_limit", "10/s")),
    "global rate limit per minute below the need": ("payments-gateway.inbound_rate_per_caller", lambda r: set_hub(r, "http.default_rate_limit", "600/min")),
    "gateway override unparseable": ("payments-gateway.inbound_rate_per_caller", lambda r: set_override(r, "payments-gateway", "http.default_rate_limit", "fast")),
    "pricing code hard-codes a slow timeout": (
        "pricing-engine.demand_model_timeout",
        lambda r: replace(r, "pricing-engine/src/index.js", "const timeoutMs = parseDuration(process.env.PLATFORM_HTTP_TIMEOUT);", "const timeoutMs = 5000;"),
    ),
    "hub file broken": ("payments-gateway.inbound_rate_per_caller", lambda r: write(r, "platform.yaml", "http.default_rate_limit: [unclosed\n")),
}


# Why each fake must be rejected: a fake rejected for any other reason is a checker or test bug.
REJECTION_REASONS = {
    'retry adapter imported at module top': 'outside contract: importing requests.adapters',
    'timeout=None': 'charge() still has no timeout',
    'undefined timeout variable': '`PAYMENTS_TIMEOUT` is not defined',
    'timeout constant reassigned to None': 'outside contract: `TIMEOUT` is bound 2 times in the module; a constant is assign',
    'timeout from an environment variable': 'outside contract: bookings_api.py reads new environment variables: PAY_TIMEOUT',
    'timeout below the need': '5 s per attempt is below bookings-api.payment_call_timeout minimum 12s',
    'timeout far above the need': '1 x 1e+06 s is above bookings-api.payment_call_timeout maximum 25s',
    'endless retry loop around the call': 'outside contract: the call is retried by a loop other than `for _ in range(<numb',
    'timeout as a string': "outside contract: timeout must be a single number, got '15'",
    'timeout=True': 'outside contract: timeout must be a single number, got True',
    'read timeout left open in a tuple': 'outside contract: `(5, None)` is not a literal, a constant or http_timeout(<serv',
    "hub helper given another service's name": "outside contract: `http_timeout('customer-profiles')` must name this service, 'b",
    'hub helper with the hub still at 300ms': '0.3 s per attempt is below bookings-api.payment_call_timeout minimum 12s',
    'local function shadowing the hub helper': "outside contract: `http_timeout('bookings-api')` needs `from platform_config imp",
    'hub helper library edited to ignore the hub': 'outside contract: libs/platform_config.py was edited; it is shared and not part ',
    'decoy timeout in a branch, real call without one': 'the code says 15.0 s but the run passed None s',
    'constant rewritten through globals() at import': 'the code says 15.0 s but the run passed None s',
    'timeout passed through **kwargs': 'outside contract: the call passes *args or **kwargs, so its timeout cannot be re',
    'unitless timeout override': 'outside contract: http.default_timeout from bookings-api override: 15 is not a d',
    'override with words for a value': "outside contract: http.default_timeout from bookings-api override: '15 seconds' ",
    'charge() renamed': 'outside contract: expected one module-level charge(), found 0',
    'SMTP timeout=None': 'send_email() still has no timeout',
    'SMTP timeout at the 300ms hub value': '0.3 s per attempt is below notifications.smtp_timeout minimum 2s',
    'SMTP timeout below the need': '1.5 s per attempt is below notifications.smtp_timeout minimum 2s',
    'SMTP timeout above the task limit': '1 x 60 s is above notifications.smtp_timeout maximum 20s',
    'SMTP timeout reset to blocking after connecting': 'outside contract: `server.sock.settimeout(None)` changes socket timeouts outside',
    'SMTP timeout attribute cleared after connecting': 'outside contract: `server.timeout = None` changes a timeout after the call is bu',
    "secret pasted into another service's existing file": 'a card processor key is still in notifications/notifications.py',
    'secret base64-encoded in a comment': 'a card processor key is still in payments-gateway/main.go',
    'secret split into three string pieces': 'a card processor key is still in payments-gateway/main.go',
    'unrelated environment variable read, key never set': 'outside contract: main.go reads environment variables other than the key',
    'key read through a variable name': 'outside contract: main.go reads the key from the environment, but not as os.Gete',
    'secret in a file under .git': 'a card processor key is still in .git/key',
    'retries in a decorator': 'outside contract: charge() is decorated; a decorator can retry or change the cal',
    'recursive retry': 'outside contract: charge() is called from inside the module, so it can retry its',
    'bounded retry loop that exceeds the maximum': '3 x 12 s is above bookings-api.payment_call_timeout maximum 25s',
    'retry adapter mounted on a session': 'outside contract: `session.mount` adds retries or transport changes outside the ',
    'timeout as a (connect, read) tuple': 'outside contract: `(20, 20)` is not a literal, a constant or http_timeout(<servi',
    'timeout as a parameter default': 'outside contract: `timeout` in charge() is not a constant assignment (parameter,',
    'hub helper imported inside the function': "outside contract: `http_timeout('bookings-api')` needs `from platform_config imp",
    'module reads an environment variable at import': 'outside contract: notifications.py reads new environment variables: SMTP_HOST',
    'backup as words with days spelled out': "outside contract: 'daily/30 days' is not a backup policy (e.g. daily/30d, none)",
    'terraform variables file overriding the default': 'outside contract: admin-console/terraform.tfvars.json was added; workers may onl',
    'world opened by a second rule in the same file': 'aws_vpc_security_group_ingress_rule.open still admits 0.0.0.0/0',
    'world opened by an inline block in the same file': 'aws_security_group.admin_console.ingress[0] still admits 0.0.0.0/0',
    'SMTP timeout variable never defined': '`SMTP_TIMEOUT` is not defined',
    'rate limit override in words': "outside contract: 'ten per second' is not a rate (e.g. 20/s, 600/min, none)",
    'rate limit override without a unit': 'outside contract: 10 is not a rate (e.g. 20/s, 600/min, none)',
    'rate limit of zero': "'0/s' must be above zero",
    'rate limit too high to protect': '1000/s is above bookings-api.public_rate_limit_per_client maximum 50/s',
    'rate limit that throttles real customers': '0.5/s is below bookings-api.public_rate_limit_per_client minimum 2/s',
    'rate limit override set to none': "public endpoints still have no rate limit (bookings-api override says 'none')",
    'secret moved to a .env file': 'outside contract: payments-gateway/.env was added; workers may only change exist',
    "secret moved to another service's file": 'outside contract: libs/processor_secret.py was added; workers may only change ex',
    'secret split across a string join': 'a card processor key is still in payments-gateway/main.go',
    'secret base64-encoded': 'outside contract: payments-gateway/key.b64 was added; workers may only change ex',
    'secret replaced by another hardcoded key': 'a card processor key is still in payments-gateway/config.yaml',
    'placeholder with a broken brace': "processor.api_key is '${PROCESSOR_API_KEY', not an environment placeholder like ",
    'api_key emptied, nothing reads the environment': 'api_key is gone from config.yaml but main.go never sets APIKey from the environm',
    'api_key removed, nothing reads the environment': 'api_key is gone from config.yaml but main.go never sets APIKey from the environm',
    'placeholder kept but main.go stops expanding it': 'main.go no longer expands environment placeholders in config.yaml',
    'os.ExpandEnv only left in a comment': 'main.go no longer expands environment placeholders in config.yaml',
    'instances as a string': "outside contract: fleet-telemetry instances is '2', not a whole number of at lea",
    'instances as true': 'outside contract: fleet-telemetry instances is True, not a whole number of at le',
    'fractional instances': 'outside contract: fleet-telemetry instances is 2.5, not a whole number of at lea',
    'availability floor put in overrides': 'outside contract: maintenance-scheduler overrides holds availability.min_instanc',
    'owner TBD': "owner team 'TBD' is not a team in teams.yaml",
    'owner as a bare string': "outside contract: owner is 'Team Fleet', not a mapping with team and contact",
    'owner team that does not exist': "owner team 'Team Telemetry' is not a team in teams.yaml",
    'owner team registered by the fixer': 'outside contract: teams.yaml was edited; it is shared and not part of any fix',
    'owner contact from another team': "owner contact 'oncall@harbourbikes.example' is not Team Fleet's contact fleet@ha",
    'weekly backup kept one day': "'weekly/1d' keeps backups for less than one weekly period",
    'backup schedule without retention': "outside contract: 'daily' is not a backup policy (e.g. daily/30d, none)",
    'backup schedule in words': "outside contract: 'nightly pg_dump' is not a backup policy (e.g. daily/30d, none",
    'backup policy put in overrides': 'outside contract: maintenance-scheduler overrides holds backup.policy; only http',
    'service opts out while the hub backs up': 'maintenance-scheduler data still has no backup (maintenance-scheduler backup is ',
    'caret range': "express is '^4.21.1', not an exact version",
    'tilde range': "express is '~4.21.1', not an exact version",
    'x-range': "express is '4.x', not an exact version",
    'latest behind an npm alias': "express is 'npm:express@latest', not an exact version",
    'dist-tag': "express is 'next', not an exact version",
    'git URL': "express is 'git+https://example.com/express.git', not an exact version",
    'unpinned dependency deleted': 'express was removed instead of pinned',
    'pin below the original major': 'demand-model-client@1.9.0 is outside the original range ^2.0.0',
    'unpinned dependency moved to devDependencies': "nodemon is '*', not an exact version",
    'package.json that does not parse': "package.json does not parse: Expecting ',' delimiter: line 10 column 20 (char 18",
    'pip lower bound': 'celery is not pinned to one version: celery>=5.4',
    'pip wildcard pin': 'celery pin is not an exact version: ==5.*',
    'pip compatible release': 'celery is not pinned to one version: celery~=5.4.0',
    'pip version behind an environment variable': 'outside contract: line 1 is not a requirement: Expected semicolon (after name wi',
    'unpinned packages moved to an included file': 'outside contract: notifications/base.txt was added; workers may only change exis',
    'unpinned package deleted': 'celery was removed instead of pinned',
    'pin outside the original range': 'firebase-admin==5.4.0 is outside the original range >=6',
    'direct URL instead of a version': 'celery is not pinned to one version: celery @ https://example.com/celery.tar.gz',
    'world split into two halves': 'aws_security_group_rule.admin_console_ingress still admits 0.0.0.0/1, 128.0.0.0/',
    'IPv6 world left open': 'aws_security_group_rule.admin_console_ingress still admits ::/0',
    'variable that still means the world': 'aws_security_group_rule.admin_console_ingress still admits 0.0.0.0/0',
    'narrow public allowlist': 'aws_security_group_rule.admin_console_ingress still admits 203.0.113.7/32',
    'CIDR with host bits set': "'10.20.0.1/16' is not a valid CIDR block",
    'unresolvable expression': "outside contract: Terraform expression '${cidrsubnet(var.staff_vpn_cidr, 0, 0)}'",
    'world opened in a second file': 'outside contract: admin-console/extra.tf.json was added; workers may only change',
    'world opened through an inline security group block': 'outside contract: admin-console/extra.tf.json was added; workers may only change',
    'CIDR list given as a bare string': "cidr_blocks is '10.20.0.0/16', not a list",
    'world opened in an HCL file': 'outside contract: admin-console/open.tf was added; workers may only change exist',
    'ingress removed so staff cannot reach it': 'no ingress rule lets staff reach the console on 443 any more',
    'hub timeout raised for bookings': 'from platform.yaml: 15 s is above pricing-engine.demand_model_timeout maximum 30',
    'pricing given a slower override': 'from pricing-engine override: 0.5 s is above pricing-engine.demand_model_timeout',
    'hub timeout unparseable': "outside contract: from platform.yaml: '300 milliseconds' is not a duration with ",
    'pricing need edited in the repo to excuse a raised hub': 'from platform.yaml: 15 s is above pricing-engine.demand_model_timeout maximum 30',
    "global rate limit below the gateway's need": 'from platform.yaml: 10/s is below payments-gateway.inbound_rate_per_caller minim',
    'global rate limit per minute below the need': 'from platform.yaml: 10/s is below payments-gateway.inbound_rate_per_caller minim',
    'gateway override unparseable': "outside contract: from payments-gateway override: 'fast' is not a rate (e.g. 20/",
    'pricing code hard-codes a slow timeout': 'outside contract: pricing-engine no longer reads PLATFORM_HTTP_TIMEOUT',
    'hub file broken': 'http.default_rate_limit for payments-gateway cannot be read: platform.yaml does ',
}
# Fakes that use a form the worker contract excludes; every other fake is inside the contract and simply not fixed.
OUTSIDE_CONTRACT = {
    'unrelated environment variable read, key never set',
    'module reads an environment variable at import',
    'retry adapter imported at module top',
    'timeout constant reassigned to None',
    'timeout from an environment variable',
    'endless retry loop around the call',
    'timeout as a string',
    'timeout=True',
    'read timeout left open in a tuple',
    "hub helper given another service's name",
    'local function shadowing the hub helper',
    'hub helper library edited to ignore the hub',
    'timeout passed through **kwargs',
    'unitless timeout override',
    'override with words for a value',
    'charge() renamed',
    'SMTP timeout reset to blocking after connecting',
    'SMTP timeout attribute cleared after connecting',
    'key read through a variable name',
    'retries in a decorator',
    'recursive retry',
    'retry adapter mounted on a session',
    'timeout as a (connect, read) tuple',
    'timeout as a parameter default',
    'hub helper imported inside the function',
    'backup as words with days spelled out',
    'terraform variables file overriding the default',
    'rate limit override in words',
    'rate limit override without a unit',
    'secret moved to a .env file',
    "secret moved to another service's file",
    'secret base64-encoded',
    'instances as a string',
    'instances as true',
    'fractional instances',
    'availability floor put in overrides',
    'owner as a bare string',
    'owner team registered by the fixer',
    'backup schedule without retention',
    'backup schedule in words',
    'backup policy put in overrides',
    'pip version behind an environment variable',
    'unpinned packages moved to an included file',
    'unresolvable expression',
    'world opened in a second file',
    'world opened through an inline security group block',
    'world opened in an HCL file',
    'hub timeout unparseable',
    'gateway override unparseable',
    'pricing code hard-codes a slow timeout',
}


@pytest.mark.parametrize("name", sorted(FAKE_FIXES))
def test_fake_fix_is_rejected(repo, name):
    target, fake = FAKE_FIXES[name]
    reference_fix(repo)
    fake(repo)
    result = check(repo)
    assert not result.risks[target].fixed, result.risks[target].reason
    assert REJECTION_REASONS[name] in result.risks[target].reason
    assert result.risks[target].outside_contract == (name in OUTSIDE_CONTRACT)
    assert_readable(result)


@pytest.mark.parametrize("name", sorted(FAKE_REGRESSIONS))
def test_hub_change_that_breaks_a_need_is_a_regression(repo, name):
    target, fake = FAKE_REGRESSIONS[name]
    reference_fix(repo)
    fake(repo)
    result = check(repo)
    assert target in result.regressed
    assert REJECTION_REASONS[name] in result.needs[target].reason
    assert result.needs[target].outside_contract == (name in OUTSIDE_CONTRACT)


def test_raised_hub_timeout_fixes_bookings_through_the_hub_but_breaks_pricing(repo):
    fix_code_only = FIXES["bookings-api.missing_timeout"]
    fix_code_only(repo)
    edit_yaml(repo, "bookings-api/service.yaml", lambda d: d.__setitem__("overrides", {}))
    set_hub(repo, "http.default_timeout", "12s")
    result = check(repo)
    assert result.fixed == ["bookings-api.missing_timeout"]
    assert result.regressed == ["pricing-engine.demand_model_timeout"]
    assert result.needs["pricing-engine.demand_model_timeout"].effective == "12s"


def test_every_fake_has_an_expected_reason():
    assert set(REJECTION_REASONS) == set(FAKE_FIXES) | set(FAKE_REGRESSIONS)


def test_non_utf8_bytes_score_instead_of_crashing(repo):
    reference_fix(repo)
    for relative in ("platform.yaml", "fleet-telemetry/service.yaml"):
        (repo / relative).write_bytes((repo / relative).read_bytes() + "# caf\xe9\n".encode("latin-1"))
    result = check(repo)
    assert "pricing-engine.demand_model_timeout" in result.regressed
    assert not result.risks["fleet-telemetry.no_owner"].fixed


def test_result_does_not_depend_on_the_caller_environment(repo, monkeypatch):
    reference_fix(repo)
    replace(repo, "notifications/notifications.py", 'SMTP_HOST = "smtp.harbourbikes.example"', 'SMTP_HOST = os.environ["SMTP_HOST"]')
    unset = check(repo).risks["notifications.missing_timeout"]
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("PYTHONPATH", "/nowhere")
    assert check(repo).risks["notifications.missing_timeout"] == unset
    assert unset.outside_contract and "reads new environment variables: SMTP_HOST" in unset.reason


def test_harness_files_are_not_contract_breaches(repo):
    reference_fix(repo)
    write(repo, ".gitignore", "__pycache__/\n")
    write(repo, "NOTES.md", "merge notes\n")
    write(repo, "bookings-api/__pycache__/bookings_api.cpython-312.pyc", "")
    result = check(repo)
    assert len(result.fixed) == 12 and result.outside_contract == []


def test_contract_text_names_every_rule_family():
    contract = worker_contract()
    for phrase in ("Do not add, delete or rename files", "timeout=", "os.Getenv", "network.tf.json", "name==version", "PLATFORM_HTTP_TIMEOUT", "Read no new environment variables"):
        assert phrase in contract


def test_regression_needs_are_the_two_planted_traps():
    assert REGRESSION_NEEDS == ("pricing-engine.demand_model_timeout", "payments-gateway.inbound_rate_per_caller")


def silent_smtp_server(held: list) -> socket.socket:
    """A local server that accepts connections into `held` (keeping them open) and never sends the SMTP greeting."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    threading.Thread(target=lambda: held.append(server.accept()), daemon=True).start()
    return server


def test_captured_smtp_timeout_is_how_long_the_real_client_waits(repo):
    """Calibrate the capture: the timeout the checker reads is the time the real smtplib gives up after."""
    notifications_code(repo, "smtplib.SMTP(SMTP_HOST, 587, timeout=0.4)")
    assert "0.4 s per attempt is below" in check(repo).risks["notifications.missing_timeout"].reason
    held = []
    server = silent_smtp_server(held)
    port = server.getsockname()[1]
    script = (
        f"import sys, time; sys.path[:0] = [{str(repo / 'notifications')!r}]\n"
        "import notifications, email.message\n"
        f"notifications.SMTP_HOST = '127.0.0.1'\n"
        "import smtplib\n"
        "real = smtplib.SMTP\n"
        f"smtplib.SMTP = lambda host, port, **kw: real(host, {port}, **kw)\n"
        "start = time.monotonic()\n"
        "try:\n    notifications.send_email(email.message.EmailMessage())\nexcept Exception as error:\n    print(type(error).__name__, time.monotonic() - start)\n"
    )
    started = time.monotonic()
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)
    server.close()
    error, waited = completed.stdout.split()
    assert error in ("TimeoutError", "SMTPServerDisconnected"), completed.stdout + completed.stderr
    assert 0.35 <= float(waited) <= 1.5
    assert time.monotonic() - started < 5
