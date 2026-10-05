"""The platform mandate, checked like CI: an outbound call's timeout must be `http_timeout("<service>")`."""

import ast

from topologies.fixcheck import BOOKINGS_CALL, NOTIFICATIONS_CALL
from topologies.harbour import OutsideContract
from topologies.pytimeouts import Resolver, Unresolved, call_name

MANDATED_FILES = {"bookings-api/bookings_api.py": BOOKINGS_CALL, "notifications/notifications.py": NOTIFICATIONS_CALL}


def mandate_violation(path: str, text: str) -> str | None:
    """Why `text` breaks the mandate for the Python file at `path`, or None when it keeps it (or is not mandated)."""
    site = MANDATED_FILES.get(path)
    if site is None:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return f"{path} does not parse: {error}"
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == site.function]
    if len(functions) != 1:
        return f"expected one module-level {site.function}(), found {len(functions)}"
    calls = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Call) and call_name(node) in site.call_names]
    if len(calls) != 1:
        return f"expected one {'/'.join(site.call_names)} call in {site.function}(), found {len(calls)}"
    call = calls[0]
    expression = next((keyword.value for keyword in call.keywords if keyword.arg == "timeout"), None)
    if expression is None and site.timeout_position is not None and len(call.args) > site.timeout_position:
        expression = call.args[site.timeout_position]
    if expression is None:
        return f"the call in {site.function}() has no timeout"
    try:
        is_hub_call = isinstance(expression, ast.Call) and Resolver(tree, functions[0], site.service, lambda: 0.0).is_hub_call(expression, 0)
    except (OutsideContract, Unresolved) as error:
        return str(error)
    return None if is_hub_call else f"`{ast.unparse(expression)}` is not http_timeout({site.service!r})"
