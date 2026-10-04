"""The timeout a Python outbound call really uses: resolved from the AST, then confirmed by running the function."""

import ast
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from topologies.harbour import OutsideContract

HUB_MODULE = "platform_config"
HUB_FUNCTION = "http_timeout"
MAX_DEPTH = 20
TIMEOUT_CHANGERS = ("settimeout", "setdefaulttimeout", "setblocking")
RETRY_MACHINERY = ("HTTPAdapter", "Retry", "mount")


class Unresolved(Exception):
    """The timeout is inside the contract but broken: undefined, contradicted by the run, or the run failed."""


@dataclass(frozen=True)
class CallSite:
    service: str
    module: str  # file name inside the service directory, without .py
    function: str
    call_names: tuple[str, ...]  # attribute or function names of the outbound call
    timeout_position: int | None  # positional index of the timeout argument, if it has one
    kind: str  # "requests" or "smtp": decides how the call is intercepted at run time


@dataclass(frozen=True)
class Timeout:
    seconds: float | None  # per attempt; None means the call has no timeout
    attempts: int  # from `for _ in range(N)` loops around the call; 1 without one

    @property
    def worst_case(self) -> float | None:
        return None if self.seconds is None else self.seconds * self.attempts


def as_seconds(value) -> float | None:
    """A timeout value as seconds; None means no timeout. Tuples and non-numbers are outside the contract."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OutsideContract(f"timeout must be a single number, got {value!r}")
    if not math.isfinite(value):
        raise OutsideContract(f"timeout is not finite: {value!r}")
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
            raise OutsideContract("timeout expression nests too deep")
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = self.number(node.operand, depth)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = self.number(node.left, depth), self.number(node.right, depth)
            if isinstance(node.op, ast.Div) and right == 0:
                raise Unresolved("division by zero in the timeout")
            return {ast.Add: lambda: left + right, ast.Sub: lambda: left - right,
                    ast.Mult: lambda: left * right, ast.Div: lambda: left / right}[type(node.op)]()
        if isinstance(node, ast.Name):
            return self.value(self.assigned_value(node.id), depth + 1)
        if isinstance(node, ast.Call) and self.is_hub_call(node, depth):
            return self.hub_timeout()
        raise OutsideContract(f"`{ast.unparse(node)}` is not a literal, a constant or http_timeout(<service>)")

    def number(self, node: ast.expr, depth: int) -> float:
        value = self.value(node, depth + 1)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise OutsideContract(f"`{ast.unparse(node)}` is not a number")
        return value

    def assigned_value(self, name: str) -> ast.expr:
        """The one expression assigned to `name`, in the function if bound there, else at module level."""
        local = bindings(self.function, name, enter_functions=False)
        if local:
            return self.single_assignment(local, name, f"{self.function.name}()")
        module_level = bindings(self.tree, name, enter_functions=False)
        if any(isinstance(node, ast.Global) for node in bindings(self.tree, name, enter_functions=True)):
            raise OutsideContract(f"`{name}` is rebound through a global statement")
        if not module_level:
            raise Unresolved(f"`{name}` is not defined")
        return self.single_assignment(module_level, name, "the module")

    def single_assignment(self, found: list[ast.AST], name: str, where: str) -> ast.expr:
        if len(found) != 1:
            raise OutsideContract(f"`{name}` is bound {len(found)} times in {where}; a constant is assigned once")
        target = found[0]
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and node.targets == [target]:
                return node.value
            if isinstance(node, ast.AnnAssign) and node.target is target and node.value is not None:
                return node.value
        raise OutsideContract(f"`{name}` in {where} is not a constant assignment (parameter, import or loop variable)")

    def module_import(self, name: str, imported: str, module: str | None) -> bool:
        """Whether `name` is bound once, by a top-level import of `imported` (from `module` when given)."""
        found = bindings(self.tree, name, enter_functions=True)
        if len(found) != 1 or not isinstance(found[0], ast.alias) or found[0].name != imported:
            return False
        for node in self.tree.body:
            if module is not None and isinstance(node, ast.ImportFrom) and node.module == module and found[0] in node.names:
                return True
            if module is None and isinstance(node, ast.Import) and found[0] in node.names:
                return True
        return False

    def is_hub_call(self, call: ast.Call, depth: int) -> bool:
        """True for `http_timeout(<this service>)` imported at module top from platform_config, and nothing else."""
        func = call.func
        if isinstance(func, ast.Name):
            called = func.id
            imported = self.module_import(func.id, HUB_FUNCTION, HUB_MODULE)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.attr == HUB_FUNCTION:
            called = HUB_FUNCTION
            imported = self.module_import(func.value.id, HUB_MODULE, None)
        else:
            return False
        if called != HUB_FUNCTION and not imported:
            return False
        if not imported:
            raise OutsideContract(f"`{ast.unparse(call)}` needs `from {HUB_MODULE} import {HUB_FUNCTION}` at module top")
        if call.keywords or len(call.args) != 1:
            raise OutsideContract(f"`{ast.unparse(call)}` must pass only the service name")
        if self.value(call.args[0], depth + 1) != self.service:
            raise OutsideContract(f"`{ast.unparse(call)}` must name this service, {self.service!r}")
        return True


def call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def attempts_around(function: ast.FunctionDef, call: ast.Call) -> int:
    """Multiply the counts of `for _ in range(N)` loops around the call; any other loop is outside the contract."""
    attempts = 1
    for node in ast.walk(function):
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.While)) or not any(inner is call for inner in ast.walk(node)):
            continue
        count = None
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Call) and call_name(node.iter) == "range":
            arguments = node.iter.args
            if len(arguments) == 1 and not node.iter.keywords and isinstance(arguments[0], ast.Constant):
                count = arguments[0].value
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise OutsideContract(f"the call is retried by a loop other than `for _ in range(<number>)` in {function.name}()")
        attempts *= count
    return attempts


def static_timeout(source: str, site: CallSite, hub_timeout: Callable[[], float]) -> Timeout:
    """Resolve the timeout of the one outbound call in `site.function` without running anything."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise Unresolved(f"{site.module}.py does not parse: {error}") from None
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == site.function
    ]
    if len(functions) != 1:
        raise OutsideContract(f"expected one module-level {site.function}(), found {len(functions)}")
    function = functions[0]
    if function.decorator_list:
        raise OutsideContract(f"{site.function}() is decorated; a decorator can retry or change the call")
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call) and call_name(node) in site.call_names]
    if len(calls) != 1:
        raise OutsideContract(f"expected one {'/'.join(site.call_names)} call directly in {site.function}(), found {len(calls)}")
    call = calls[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == site.function:
            raise OutsideContract(f"{site.function}() is called from inside the module, so it can retry itself")
        if isinstance(node, ast.Call) and call_name(node) in TIMEOUT_CHANGERS:
            raise OutsideContract(f"`{ast.unparse(node)}` changes socket timeouts outside the call")
        if isinstance(node, (ast.Name, ast.Attribute)) and (getattr(node, "id", None) or getattr(node, "attr", None)) in RETRY_MACHINERY:
            raise OutsideContract(f"`{ast.unparse(node)}` adds retries or transport changes outside the call")
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else []
        if any(isinstance(target, ast.Attribute) and target.attr == "timeout" for target in targets):
            raise OutsideContract(f"`{ast.unparse(node)}` changes a timeout after the call is built")
    if any(keyword.arg is None for keyword in call.keywords) or any(isinstance(arg, ast.Starred) for arg in call.args):
        raise OutsideContract("the call passes *args or **kwargs, so its timeout cannot be read")
    attempts = attempts_around(function, call)
    expression = next((keyword.value for keyword in call.keywords if keyword.arg == "timeout"), None)
    if expression is None and site.timeout_position is not None and len(call.args) > site.timeout_position:
        expression = call.args[site.timeout_position]
    if expression is None:
        return Timeout(None, attempts)
    return Timeout(as_seconds(Resolver(tree, function, site.service, hub_timeout).value(expression)), attempts)


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

# A stand-in `requests` with the names payment code commonly touches; every request records its timeout.
requests = types.ModuleType("requests")
requests.__path__ = []
exceptions = types.ModuleType("requests.exceptions")
exceptions.RequestException = type("RequestException", (OSError,), {})
for name in ("HTTPError", "ConnectionError", "Timeout"):
    setattr(exceptions, name, type(name, (exceptions.RequestException,), {}))
exceptions.ConnectTimeout = type("ConnectTimeout", (exceptions.ConnectionError, exceptions.Timeout), {})
exceptions.ReadTimeout = type("ReadTimeout", (exceptions.Timeout,), {})
for name in dir(exceptions):
    if not name.startswith("_"):
        setattr(requests, name, getattr(exceptions, name))
requests.exceptions = exceptions


class Response:
    status_code = 200

    def json(self):
        return {}

    def raise_for_status(self):
        return None


class Session:
    def __init__(self):
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        return None


for verb in ("get", "post", "put", "patch", "delete", "head", "request"):
    setattr(requests, verb, capture_request)
    setattr(Session, verb, lambda self, *args, **kwargs: capture_request(*args, **kwargs))
requests.Response, requests.Session, requests.session = Response, Session, Session
sys.modules["requests"], sys.modules["requests.exceptions"] = requests, exceptions


class Booking:
    def to_payment(self):
        return {"booking_id": "b-1", "amount_cents": 1200}

    def __getattr__(self, name):
        return f"stub-{name}"


message = email.message.EmailMessage()
message["To"] = "rider@harbourbikes.example"
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

# The child sees only this environment, so a result never depends on who runs the check.
CLEAN_ENV = {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONHASHSEED": "0", "SMTP_PASSWORD": "check-only"}


def runtime_timeout(repo: Path, site: CallSite) -> float | None:
    """Run the function with its outbound call intercepted and return the timeout it passed."""
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", DRIVER, str(repo), site.service, site.module, site.function, site.kind],
            capture_output=True,
            text=True,
            timeout=20,
            env=CLEAN_ENV,
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


def effective_timeout(repo: Path, site: CallSite, hub_timeout: Callable[[], float]) -> Timeout:
    """The call's timeout when static resolution and the run agree."""
    path = repo / site.service / f"{site.module}.py"
    if not path.is_file():
        raise OutsideContract(f"{site.service}/{site.module}.py is missing")
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise Unresolved(f"{site.service}/{site.module}.py is not UTF-8") from None
    static = static_timeout(source, site, hub_timeout)
    runtime = runtime_timeout(repo, site)
    if static.seconds is None and runtime is None:
        return static
    if static.seconds is None or runtime is None or not math.isclose(static.seconds, runtime, rel_tol=1e-9, abs_tol=1e-9):
        raise Unresolved(f"the code says {static.seconds} s but the run passed {runtime} s")
    return static
