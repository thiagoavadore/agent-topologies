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
from topologies.harbour import FIXTURE, NEEDS, REGRESSION_NEEDS, RISKS

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
        "dependencies.pinning: none",
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
    replace(repo, "bookings-api/bookings_api.py", BOOKINGS_CALL, BOOKINGS_CALL[:-1] + ", timeout=(3.05, PAYMENT_TIMEOUT_S))")


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
]


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
    "hub file broken": ("payments-gateway.inbound_rate_per_caller", lambda r: write(r, "platform.yaml", "http.default_rate_limit: [unclosed\n")),
}


# Why each fake must be rejected: a fake rejected for any other reason is a checker or test bug.
REJECTION_REASONS = {
    'timeout=None': 'charge() still has no timeout',
    'undefined timeout variable': '`PAYMENTS_TIMEOUT` is not defined',
    'timeout constant reassigned to None': '`TIMEOUT` is bound 2 times in the module',
    'timeout from an environment variable': "cannot resolve `float(os.environ.get('PAY_TIMEOUT', 15))` to a number",
    'timeout below the need': '5 s is below bookings-api.payment_call_timeout minimum 12s',
    'timeout far above the need': '1e+06 s is above bookings-api.payment_call_timeout maximum 25s',
    'endless retry loop around the call': 'the call sits in a loop in charge(), so the total wait has no single b',
    'timeout as a string': "timeout is not a number: '15'",
    'timeout=True': 'timeout is not a number: True',
    'read timeout left open in a tuple': 'charge() still has no timeout',
    "hub helper given another service's name": "`http_timeout('customer-profiles')` must name this service, 'bookings-",
    'hub helper with the hub still at 300ms': '0.3 s is below bookings-api.payment_call_timeout minimum 12s',
    'local function shadowing the hub helper': "cannot resolve `http_timeout('bookings-api')` to a number",
    'hub helper library edited to ignore the hub': 'the code says 0.3 s but the run passed 15.0 s',
    'decoy timeout in a branch, real call without one': 'the code says 15.0 s but the run passed None s',
    'constant rewritten through globals() at import': 'the code says 15.0 s but the run passed None s',
    'timeout passed through **kwargs': 'the call passes *args or **kwargs, so its timeout cannot be read',
    'unitless timeout override': 'http.default_timeout from bookings-api override: 15 is not a duration ',
    'override with words for a value': "http.default_timeout from bookings-api override: '15 seconds' is not a",
    'charge() renamed': 'expected one module-level charge(), found 0',
    'SMTP timeout=None': 'send_email() still has no timeout',
    'SMTP timeout at the 300ms hub value': '0.3 s is below notifications.smtp_timeout minimum 2s',
    'SMTP timeout below the need': '1.5 s is below notifications.smtp_timeout minimum 2s',
    'SMTP timeout above the task limit': '60 s is above notifications.smtp_timeout maximum 20s',
    'SMTP timeout reset to blocking after connecting': '`server.sock.settimeout(None)` changes socket timeouts outside the cal',
    'SMTP timeout attribute cleared after connecting': '`server.timeout = None` changes a timeout after the call is built',
    'SMTP timeout variable never defined': '`SMTP_TIMEOUT` is not defined',
    'rate limit override in words': "'ten per second' is not a rate (e.g. 20/s, 600/min, none)",
    'rate limit override without a unit': '10 is not a rate (e.g. 20/s, 600/min, none)',
    'rate limit of zero': "'0/s' must be above zero",
    'rate limit too high to protect': '1000/s is above bookings-api.public_rate_limit_per_client maximum 50/s',
    'rate limit that throttles real customers': '0.5/s is below bookings-api.public_rate_limit_per_client minimum 2/s',
    'rate limit override set to none': 'public endpoints still have no rate limit (bookings-api override says ',
    'secret moved to a .env file': 'a card processor key is still in payments-gateway/.env',
    "secret moved to another service's file": 'a card processor key is still in libs/processor_secret.py',
    'secret split across a string join': 'a card processor key is still in payments-gateway/main.go',
    'secret base64-encoded': 'a card processor key is still in payments-gateway/key.b64',
    'secret replaced by another hardcoded key': 'a card processor key is still in payments-gateway/config.yaml',
    'placeholder with a broken brace': "processor.api_key is '${PROCESSOR_API_KEY', not an environment placeho",
    'api_key emptied, nothing reads the environment': "processor.api_key is '', not an environment placeholder like ${NAME}",
    'api_key removed, nothing reads the environment': 'api_key was removed but main.go reads no environment variable for it',
    'placeholder kept but main.go stops expanding it': 'main.go no longer expands environment placeholders in config.yaml',
    'os.ExpandEnv only left in a comment': 'main.go no longer expands environment placeholders in config.yaml',
    'instances as a string': "fleet-telemetry instances is '2', not a whole number of at least 1",
    'instances as true': 'fleet-telemetry instances is True, not a whole number of at least 1',
    'fractional instances': 'fleet-telemetry instances is 2.5, not a whole number of at least 1',
    'availability floor put in overrides': 'maintenance-scheduler still runs 1 instance (overrides are not read fo',
    'owner TBD': "owner team 'TBD' is not a team in teams.yaml",
    'owner as a bare string': "owner is 'Team Fleet', not a mapping with team and contact",
    'owner team that does not exist': "owner team 'Team Telemetry' is not a team in teams.yaml",
    'owner team registered by the fixer': "owner team 'Team Telemetry' is not a team in teams.yaml",
    'owner contact from another team': "owner contact 'oncall@harbourbikes.example' is not Team Fleet's contac",
    'weekly backup kept one day': "'weekly/1d' keeps backups for less than one weekly period",
    'backup schedule without retention': "'daily' is not a backup policy (e.g. daily/30d, none)",
    'backup schedule in words': "'nightly pg_dump' is not a backup policy (e.g. daily/30d, none)",
    'backup policy put in overrides': 'maintenance-scheduler data still has no backup (platform backup.policy',
    'service opts out while the hub backs up': 'maintenance-scheduler data still has no backup (maintenance-scheduler ',
    'caret range': "express is '^4.21.1', not an exact version",
    'tilde range': "express is '~4.21.1', not an exact version",
    'x-range': "express is '4.x', not an exact version",
    'latest behind an npm alias': "express is 'npm:express@latest', not an exact version",
    'dist-tag': "express is 'next', not an exact version",
    'git URL': "express is 'git+https://example.com/express.git', not an exact version",
    'unpinned dependency deleted': 'express was removed instead of pinned',
    'pin below the original major': 'demand-model-client@1.9.0 is outside the original range ^2.0.0',
    'unpinned dependency moved to devDependencies': "nodemon is '*', not an exact version",
    'package.json that does not parse': "package.json does not parse: Expecting ',' delimiter: line 10 column 2",
    'pip lower bound': 'celery is not pinned to one version: celery>=5.4',
    'pip wildcard pin': 'celery pin is not an exact version: ==5.*',
    'pip compatible release': 'celery is not pinned to one version: celery~=5.4.0',
    'pip version behind an environment variable': 'line 1 does not parse: Expected semicolon (after name with no version ',
    'unpinned packages moved to an included file': 'line 1 is a pip option (-r); pins must be in this file',
    'unpinned package deleted': 'celery was removed instead of pinned',
    'pin outside the original range': 'firebase-admin==5.4.0 is outside the original range >=6',
    'direct URL instead of a version': 'celery is not pinned to one version: celery @ https://example.com/cele',
    'world split into two halves': 'aws_security_group_rule.admin_console_ingress still admits 0.0.0.0/1, ',
    'IPv6 world left open': 'aws_security_group_rule.admin_console_ingress still admits ::/0',
    'variable that still means the world': 'aws_security_group_rule.admin_console_ingress still admits 0.0.0.0/0',
    'narrow public allowlist': 'aws_security_group_rule.admin_console_ingress still admits 203.0.113.7',
    'CIDR with host bits set': "'10.20.0.1/16' is not a valid CIDR block",
    'unresolvable expression': "cannot resolve Terraform expression '${cidrsubnet(var.staff_vpn_cidr, ",
    'world opened in a second file': 'aws_vpc_security_group_ingress_rule.open still admits 0.0.0.0/0',
    'world opened through an inline security group block': 'aws_security_group.extra.ingress[0] still admits 0.0.0.0/0',
    'CIDR list given as a bare string': "cidr_blocks is '10.20.0.0/16', not a list",
    'world opened in an HCL file': 'admin-console has HCL .tf files, which this check does not parse',
    'ingress removed so staff cannot reach it': 'no ingress rule lets staff reach the console on 443 any more',
    'hub timeout raised for bookings': 'from platform.yaml: 15 s is above pricing-engine.demand_model_timeout ',
    'pricing given a slower override': 'from pricing-engine override: 0.5 s is above pricing-engine.demand_mod',
    'hub timeout unparseable': "from platform.yaml: '300 milliseconds' is not a duration with a unit (",
    'pricing need edited in the repo to excuse a raised hub': 'from platform.yaml: 15 s is above pricing-engine.demand_model_timeout ',
    "global rate limit below the gateway's need": 'from platform.yaml: 10/s is below payments-gateway.inbound_rate_per_ca',
    'global rate limit per minute below the need': 'from platform.yaml: 10/s is below payments-gateway.inbound_rate_per_ca',
    'gateway override unparseable': "from payments-gateway override: 'fast' is not a rate (e.g. 20/s, 600/m",
    'hub file broken': 'http.default_rate_limit for payments-gateway cannot be read: platform.',
}


@pytest.mark.parametrize("name", sorted(FAKE_FIXES))
def test_fake_fix_is_rejected(repo, name):
    target, fake = FAKE_FIXES[name]
    reference_fix(repo)
    fake(repo)
    result = check(repo)
    assert not result.risks[target].fixed, result.risks[target].reason
    assert REJECTION_REASONS[name] in result.risks[target].reason
    assert_readable(result)


@pytest.mark.parametrize("name", sorted(FAKE_REGRESSIONS))
def test_hub_change_that_breaks_a_need_is_a_regression(repo, name):
    target, fake = FAKE_REGRESSIONS[name]
    reference_fix(repo)
    fake(repo)
    result = check(repo)
    assert target in result.regressed
    assert REJECTION_REASONS[name] in result.needs[target].reason


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
    assert "0.4 s is below" in check(repo).risks["notifications.missing_timeout"].reason
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
