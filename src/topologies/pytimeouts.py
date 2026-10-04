"""The timeout a Python outbound call really uses: resolved from the AST, then confirmed by running the function."""

import ast
import json
import math
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HUB_MODULE = "platform_config"
HUB_FUNCTION = "http_timeout"
MAX_DEPTH = 20
TIMEOUT_CHANGERS = ("settimeout", "setdefaulttimeout", "setblocking")


class Unresolved(Exception):
    """The timeout cannot be pinned to one number; the reason is the message."""


@dataclass(frozen=True)
class CallSite:
    service: str
    module: str  # file name inside the service directory, without .py
    function: str
    call_names: tuple[str, ...]  # attribute or function names of the outbound call
    timeout_position: int | None  # positional index of the timeout argument, if it has one
    kind: str  # "requests" or "smtp": decides how the call is intercepted at run time


def as_seconds(value) -> float | None:
    """Turn a timeout value into the seconds that bound the wait; None means no timeout."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise Unresolved(f"a (connect, read) timeout needs two values, got {value!r}")
        connect, read = (as_seconds(part) for part in value)
        if connect is None or read is None:
            return None
        if connect <= 0:
            raise Unresolved(f"connect timeout must be positive, got {connect}")
        return read
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Unresolved(f"timeout is not a number: {value!r}")
    if not math.isfinite(value):
        raise Unresolved(f"timeout is not finite: {value!r}")
    return float(value)


def bindings(scope: ast.AST, name: str, *, enter_functions: bool) -> list[ast.AST]:
    """Every node in `scope` that binds `name`; nested function and class bodies only if `enter_functions`."""
    found = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        nested = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            found.append(node)
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
            found.append(node)
        elif isinstance(node, ast.arg) and node.arg == name:
            found.append(node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found += [alias for alias in node.names if (alias.asname or alias.name.split(".")[0]) == name]
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            found.append(node)
        if not nested or enter_functions:
            stack.extend(ast.iter_child_nodes(node))
    return found


class Resolver:
    def __init__(self, tree: ast.Module, function: ast.FunctionDef, service: str, hub_timeout: Callable[[], float]):
        self.tree = tree
        self.function = function
        self.service = service
        self.hub_timeout = hub_timeout

    def value(self, node: ast.expr, depth: int = 0):
        if depth > MAX_DEPTH:
            raise Unresolved("timeout expression nests too deep")
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Tuple):
            return tuple(self.value(element, depth + 1) for element in node.elts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = self.number(node.operand, depth)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = self.number(node.left, depth), self.number(node.right, depth)
            try:
                return {ast.Add: left + right, ast.Sub: left - right, ast.Mult: left * right}[type(node.op)]
            except KeyError:
                if right == 0:
                    raise Unresolved("division by zero in the timeout") from None
                return left / right
        if isinstance(node, ast.Name):
            return self.value(self.assigned_value(node.id), depth + 1)
        if isinstance(node, ast.Call) and self.is_hub_call(node):
            return self.hub_timeout()
        raise Unresolved(f"cannot resolve `{ast.unparse(node)}` to a number")

    def number(self, node: ast.expr, depth: int) -> float:
        value = self.value(node, depth + 1)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Unresolved(f"`{ast.unparse(node)}` is not a number")
        return value

    def assigned_value(self, name: str) -> ast.expr:
        """The one expression assigned to `name`, in the function if bound there, else at module level."""
        local = bindings(self.function, name, enter_functions=False)
        if local:
            return self.single_assignment(local, name, f"{self.function.name}()")
        module_level = bindings(self.tree, name, enter_functions=False)
        declared_global = [node for node in bindings(self.tree, name, enter_functions=True) if isinstance(node, ast.Global)]
        if declared_global:
            raise Unresolved(f"`{name}` is rebound through a global statement")
        if not module_level:
            raise Unresolved(f"`{name}` is not defined")
        return self.single_assignment(module_level, name, "the module")

    def single_assignment(self, found: list[ast.AST], name: str, where: str) -> ast.expr:
        if len(found) != 1:
            raise Unresolved(f"`{name}` is bound {len(found)} times in {where}")
        target = found[0]
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and node.targets == [target]:
                return node.value
            if isinstance(node, ast.AnnAssign) and node.target is target and node.value is not None:
                return node.value
        raise Unresolved(f"`{name}` is not a plain assignment in {where}")

    def is_hub_call(self, call: ast.Call) -> bool:
        """True for `http_timeout("<this service>")` imported from platform_config, and nothing else."""
        func = call.func
        if isinstance(func, ast.Name):
            found = bindings(self.tree, func.id, enter_functions=True)
            imported = (
                len(found) == 1
                and isinstance(found[0], ast.alias)
                and found[0].name == HUB_FUNCTION
                and any(
                    isinstance(node, ast.ImportFrom) and node.module == HUB_MODULE and found[0] in node.names
                    for node in self.tree.body
                )
            )
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.attr == HUB_FUNCTION:
            found = bindings(self.tree, func.value.id, enter_functions=True)
            imported = (
                len(found) == 1
                and isinstance(found[0], ast.alias)
                and found[0].name == HUB_MODULE
                and any(isinstance(node, ast.Import) and found[0] in node.names for node in self.tree.body)
            )
        else:
            return False
        if not imported:
            return False
        if call.keywords or len(call.args) != 1:
            raise Unresolved(f"`{ast.unparse(call)}` must pass only the service name")
        argument = call.args[0]
        if not (isinstance(argument, ast.Constant) and argument.value == self.service):
            raise Unresolved(f"`{ast.unparse(call)}` must name this service, {self.service!r}")
        return True


def call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def static_timeout(source: str, site: CallSite, hub_timeout: Callable[[], float]) -> float | None:
    """Resolve the timeout of the one outbound call in `site.function` without running anything."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise Unresolved(f"{site.module}.py does not parse: {error}") from None
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == site.function
    ]
    if len(functions) != 1:
        raise Unresolved(f"expected one module-level {site.function}(), found {len(functions)}")
    function = functions[0]
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call) and call_name(node) in site.call_names]
    if len(calls) != 1:
        raise Unresolved(f"expected one {'/'.join(site.call_names)} call in {site.function}(), found {len(calls)}")
    call = calls[0]
    if any(isinstance(node, (ast.For, ast.AsyncFor, ast.While)) and call in list(ast.walk(node)) for node in ast.walk(function)):
        raise Unresolved(f"the call sits in a loop in {site.function}(), so the total wait has no single bound")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) in TIMEOUT_CHANGERS:
            raise Unresolved(f"`{ast.unparse(node)}` changes socket timeouts outside the call")
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else []
        if any(isinstance(target, ast.Attribute) and target.attr == "timeout" for target in targets):
            raise Unresolved(f"`{ast.unparse(node)}` changes a timeout after the call is built")
    if any(keyword.arg is None for keyword in call.keywords) or any(isinstance(arg, ast.Starred) for arg in call.args):
        raise Unresolved("the call passes *args or **kwargs, so its timeout cannot be read")
    expression = next((keyword.value for keyword in call.keywords if keyword.arg == "timeout"), None)
    if expression is None and site.timeout_position is not None and len(call.args) > site.timeout_position:
        expression = call.args[site.timeout_position]
    if expression is None:
        return None
    return as_seconds(Resolver(tree, function, site.service, hub_timeout).value(expression))


# Runs in a child interpreter: blocks the network, intercepts the outbound call, prints the timeout it was given.
DRIVER = r"""
import email.message, importlib, json, os, socket, sys, types

repo, service, module_name, function_name, kind = sys.argv[1:6]
seen = []


class Captured(BaseException):
    pass


def blocked(*args, **kwargs):
    raise OSError("network access is blocked during the check")


def capture_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *args, **kwargs):
    seen.append(socket.getdefaulttimeout() if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout)
    raise Captured()


def capture_request(*args, **kwargs):
    seen.append(kwargs.get("timeout"))
    raise Captured()


socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = capture_connection
verbs = ("get", "post", "put", "patch", "delete", "head", "request")
fake_requests = types.ModuleType("requests")
fake_requests.RequestException = fake_requests.Timeout = fake_requests.ConnectionError = OSError


class Session:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


for verb in verbs:
    setattr(fake_requests, verb, capture_request)
    setattr(Session, verb, lambda self, *args, **kwargs: capture_request(*args, **kwargs))
fake_requests.Session = fake_requests.session = Session
sys.modules["requests"] = fake_requests


class Booking:
    def to_payment(self):
        return {"booking_id": "b-1", "amount_cents": 1200}


message = email.message.EmailMessage()
message["To"] = "rider@harbourbikes.example"
os.environ.setdefault("SMTP_PASSWORD", "check-only")
os.chdir(repo)
sys.path[:0] = [os.path.join(repo, service), os.path.join(repo, "libs")]
try:
    function = getattr(importlib.import_module(module_name), function_name)
    function(Booking() if kind == "requests" else message)
except Captured:
    pass
except Exception as error:
    print(json.dumps({"error": f"{type(error).__name__}: {error}"}))
    sys.exit(0)
if len(seen) != 1:
    print(json.dumps({"error": f"expected one outbound call, saw {len(seen)}"}))
else:
    print(json.dumps({"timeout": seen[0]}))
"""


def runtime_timeout(repo: Path, site: CallSite) -> float | None:
    """Run the function with its outbound call intercepted and return the timeout it passed."""
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", DRIVER, str(repo), site.service, site.module, site.function, site.kind],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise Unresolved(f"running {site.function}() did not finish in 20 s") from None
    try:
        outcome = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        raise Unresolved(f"running {site.function}() failed: {completed.stderr.strip()[-300:]}") from None
    if "error" in outcome:
        raise Unresolved(f"running {site.function}() failed: {outcome['error']}")
    return as_seconds(outcome["timeout"])


def effective_timeout(repo: Path, site: CallSite, hub_timeout: Callable[[], float]) -> float | None:
    """The call's timeout in seconds when static resolution and the run agree; None when it has none."""
    path = repo / site.service / f"{site.module}.py"
    if not path.is_file():
        raise Unresolved(f"{site.service}/{site.module}.py is missing")
    static = static_timeout(path.read_text(), site, hub_timeout)
    runtime = runtime_timeout(repo, site)
    if static is None and runtime is None:
        return None
    if static is None or runtime is None or not math.isclose(static, runtime, rel_tol=1e-9, abs_tol=1e-9):
        raise Unresolved(f"the code says {static} s but the run passed {runtime} s")
    return static
