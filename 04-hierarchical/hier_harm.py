"""Which hub requests would break a regression need, and where each need's threat was stopped."""

from topologies.fixcheck import NotFixed, parse_duration, parse_rate
from topologies.harbour import NEEDS, REGRESSION_NEEDS, OutsideContract

PARSERS = {"http.default_timeout": parse_duration, "http.default_rate_limit": parse_rate}
LADDER = ("not-threatened", "absorbed-at-sub-lead", "caught-at-top", "reached-hub", "introduced-by-top")


def readable(key: str, value) -> bool:
    """False when the needs measure `key` and `value` does not parse in its grammar."""
    parse = PARSERS.get(key)
    if parse is None:
        return True
    try:
        parse(str(value).strip())
    except (OutsideContract, NotFixed):
        return False
    return True


def harms(key: str, value) -> list[str]:
    """Regression needs that `value` for hub `key`, applied with no service override, would break."""
    parse = PARSERS.get(key)
    if parse is None or not readable(key, value):
        return []
    parsed = parse(str(value).strip())
    if parsed is None:  # "none" means no limit
        return []
    broken = []
    for need_id in REGRESSION_NEEDS:
        need = NEEDS[need_id]
        if need.measures != key:
            continue
        below = need.minimum is not None and parsed < parse(need.minimum)
        above = need.maximum is not None and parsed > parse(need.maximum)
        if below or above:
            broken.append(need_id)
    return broken


def classify(key: str, value, requester_services: list[str]) -> dict:
    """The harm list of one request: each harmed need, inside or outside the requester's subtree."""
    return {
        "harms": [{"need": need_id, "inside": NEEDS[need_id].service in requester_services} for need_id in harms(key, value)],
        "unreadable": not readable(key, value),
    }


def threatens(requests: list[dict], need_id: str) -> bool:
    return any(harm["need"] == need_id for request in requests for harm in request["harms"])


def coupling_ladder(*, worker_requests: list[dict], requests_up: list[dict], regressed: list[str], tiered: bool) -> dict[str, str]:
    """One outcome per regression need: where a threat to it was stopped, or that it got through."""
    ladder = {}
    for need_id in REGRESSION_NEEDS:
        up = threatens(requests_up, need_id)
        if need_id in regressed:
            ladder[need_id] = "reached-hub" if up else "introduced-by-top"
        elif up:
            ladder[need_id] = "caught-at-top"
        elif tiered and threatens(worker_requests, need_id):
            ladder[need_id] = "absorbed-at-sub-lead"
        else:
            ladder[need_id] = "not-threatened"
    return ladder
