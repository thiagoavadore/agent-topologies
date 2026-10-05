import fanout_helpers  # noqa: F401  (import-safe path setup)

from fanout_mandate import mandate_violation
from topologies.harbour import FIXTURE

BOOKINGS = "bookings-api/bookings_api.py"
NOTIFICATIONS = "notifications/notifications.py"
PLAIN = 'requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment())'
HUB = 'requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment(), timeout=http_timeout("bookings-api"))'


def bookings(call: str, header: str = "import requests\nfrom platform_config import http_timeout\n") -> str:
    text = (FIXTURE / BOOKINGS).read_text()
    return text.replace("import requests\n", header, 1).replace(PLAIN, call)


def test_the_hub_call_keeps_the_mandate():
    assert mandate_violation(BOOKINGS, bookings(HUB)) is None


def test_literals_constants_and_a_missing_timeout_break_it():
    assert "is not http_timeout" in mandate_violation(BOOKINGS, bookings(PLAIN.replace("to_payment())", "to_payment(), timeout=15)")))
    assert "no timeout" in mandate_violation(BOOKINGS, bookings(PLAIN))
    constant = bookings(PLAIN.replace("to_payment())", "to_payment(), timeout=T)"), "import requests\nT = 15\n")
    assert "is not http_timeout" in mandate_violation(BOOKINGS, constant)


def test_the_call_must_name_its_own_service_and_import_the_function():
    assert "must name this service" in mandate_violation(BOOKINGS, bookings(HUB.replace('"bookings-api"', '"notifications"')))
    assert "needs `from platform_config import http_timeout`" in mandate_violation(BOOKINGS, bookings(HUB, "import requests\n"))


def test_unmandated_files_and_unparseable_text():
    assert mandate_violation("bookings-api/service.yaml", "anything") is None
    assert "does not parse" in mandate_violation(BOOKINGS, "def broken(:")
