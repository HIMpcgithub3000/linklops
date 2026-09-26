"""Regression control: /ready reflects Postgres, and deliberately not Redis.

This one guards a decision, not a computation, and the decision is easy to
"improve" into a bug: readiness should be *thorough*, so someone adds the cache
to it, and the next Redis blip pulls the whole fleet out of rotation for a
service that could still serve. That was the Module 10 break, and it is the exact
failure the endpoint's own docstring warns against -- a more thorough check that
makes availability worse.

The contract, stated so it can be enforced:

  /health   liveness   never depends on any external service. A database blip
                       must not restart every pod at once.
  /ready    readiness  depends on Postgres (the request path needs it) and NOT
                       on Redis (a cache with a database fallback, whose outage
                       must degrade latency, not availability).

Checked two ways, because either alone has a hole:

  behaviour  drive the real handlers with a broken database and a broken Redis
             client swapped in, and assert /ready fails for the first and
             ignores the second, while /health ignores both.

  source     a Redis call inside the ready() function is the regression itself,
             so the function's source is scanned for one. Behaviour testing can
             miss a redis call that happens to be short-circuited on the day the
             test runs; the source scan cannot.

No server and no network -- the handlers are plain functions and the failures
are injected. Exit 0 = the contract holds. Exit 1 = it does not.
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.responses import Response  # noqa: E402

from app import main  # noqa: E402


class _Boom:
    def __getattr__(self, _name):
        def fail(*_a, **_k):
            raise RuntimeError("dependency down")
        return fail


def _status(monkey_db=None, monkey_redis=None) -> int:
    """Call ready() with optional broken dependencies and return its status."""
    response = Response()
    saved_ping = main.db.ping
    import app.redis_client as rc
    saved_client = rc.client
    try:
        if monkey_db is not None:
            main.db.ping = monkey_db
        if monkey_redis is not None:
            rc.client = monkey_redis
        main.ready(response)
        return response.status_code
    finally:
        main.db.ping = saved_ping
        rc.client = saved_client


def check_behaviour() -> list[str]:
    failures = []

    # All up: ready. (Uses the real db.ping against the configured database; if
    # that is down this reports it, which is correct.)
    try:
        if _status() != 200:
            failures.append("  /ready is not 200 with all dependencies up")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"  /ready raised with dependencies up: {type(exc).__name__}")

    # Postgres down: NOT ready. This is the dependency readiness must reflect.
    if _status(monkey_db=lambda *_: (_ for _ in ()).throw(RuntimeError("pg down"))) != 503:
        failures.append("  /ready did not report 503 when Postgres was down")

    # Redis down: STILL ready. This is the whole point -- a cache outage must
    # not read as unreadiness.
    status = _status(monkey_redis=_Boom())
    if status != 200:
        failures.append(
            f"  /ready returned {status} with only Redis down -- a cache outage is "
            "being reported as unreadiness, which pulls the fleet out of rotation"
        )
    return failures


def _code_without_docstring(func) -> str:
    """The function's source with its docstring removed.

    The docstring is where the *reason* Redis is excluded is written -- 'must NOT
    be added here' -- so scanning raw source flags the documentation as the bug.
    The scan must see code, not prose, so the docstring is stripped by parsing.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    fn = tree.body[0]
    if (
        fn.body
        and isinstance(fn.body[0], ast.Expr)
        and isinstance(fn.body[0].value, ast.Constant)
        and isinstance(fn.body[0].value.value, str)
    ):
        fn.body = fn.body[1:]
    return ast.unparse(fn)


def check_source() -> list[str]:
    """No Redis reference in ready()'s CODE; no dependency call in health()'s."""
    failures = []
    ready_code = _code_without_docstring(main.ready)
    if "redis" in ready_code.lower():
        failures.append("  ready() calls into Redis -- the Module 10 regression")

    health_code = _code_without_docstring(main.health)
    for forbidden in ("redis", "db.ping", "ping("):
        if forbidden in health_code:
            failures.append(
                f"  health() references {forbidden!r} -- liveness must not touch a dependency"
            )
    return failures


def main_() -> int:
    failures = check_behaviour() + check_source()
    if failures:
        print("check_readiness_contract: FAIL")
        for failure in failures:
            print(failure)
        return 1
    print(
        "readiness contract ok: /ready tracks Postgres and ignores Redis, "
        "/health touches no dependency"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
