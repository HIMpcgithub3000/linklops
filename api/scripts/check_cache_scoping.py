"""Regression control: a cache read must not be a way around row-level security.

The Module 06 constraints twist (b2b_multi_tenant: org scoping, end to end) was
taken as implement-now rather than defer, and this is what "implemented" means
in practice. The property it defends:

    RLS is enforced by Postgres. A cache hit never reaches Postgres. So the
    key is the only thing scoping a cached read to a tenant, and a key built
    without a tenant is a cross-tenant read that check_rls.py cannot see --
    because no query ran for it to inspect.

That failure is silent from every angle: the response is well-formed, the logs
look normal, and the database is innocent. A control is the only thing that
notices, so it has to notice at construction time rather than at read time.

Two halves, and the second is the one that actually holds the line:

  the rule       every tenant-scoped namespace refuses a key without a tenant,
                 every global namespace refuses a key *with* one, unregistered
                 namespaces are refused outright, and two tenants never collide.

  the reach      the rule is worthless if code can skip it, so the source is
                 scanned for direct Redis calls that build their own keys. An
                 allowlist covers the two places that legitimately do.

Exit 0 = the discipline holds and nothing bypasses it. Exit 1 = it does not.

No database and no Redis: it drives key() directly and reads source text, so it
belongs in CI on every commit.
"""

import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.cache import NAMESPACES, CacheKeyError, key  # noqa: E402

# Modules allowed to touch the Redis client directly.
#
#   app/cache.py        is the discipline; it necessarily calls Redis.
#   app/redis_client.py owns the connection and the analytics queue name, which
#                       is a fixed constant carrying link ids, not a key built
#                       from tenant data.
#   app/routers/redirect.py pushes to that same fixed queue in enqueue_click.
#
# Anything else building its own key is the exact bypass this control exists to
# catch, so the list is deliberately short and each entry is justified above.
ALLOWED_DIRECT_REDIS = {
    "app/cache.py",
    "app/redis_client.py",
    "app/routers/redirect.py",
}

DIRECT_REDIS_CALL = re.compile(r"redis_client\.client\.(get|set|delete|mget|hset|hget)\b")


def check_rules() -> list[str]:
    failures: list[str] = []
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    if not NAMESPACES:
        return ["  no namespaces registered, so this control proves nothing"]

    for name, ns in NAMESPACES.items():
        if not ns.why.strip():
            # A namespace whose scoping decision has no stated reason cannot be
            # reviewed. That is true of the exceptions especially: an
            # unexplained global namespace is indistinguishable from an
            # oversight.
            failures.append(f"  namespace {name!r} has no stated reason for its scoping")

        if ns.tenant_scoped:
            try:
                built = key(name, "x")
            except CacheKeyError:
                pass
            else:
                failures.append(
                    f"  tenant-scoped namespace {name!r} built a key with no tenant: {built!r}"
                )

            key_a = key(name, "x", tenant_id=tenant_a)
            key_b = key(name, "x", tenant_id=tenant_b)
            if key_a == key_b:
                failures.append(f"  namespace {name!r} gives two tenants the same key")
            if str(tenant_a) not in key_a:
                failures.append(f"  namespace {name!r} omits the tenant from its key: {key_a!r}")
        else:
            try:
                built = key(name, "x", tenant_id=tenant_a)
            except CacheKeyError:
                pass
            else:
                failures.append(
                    f"  global namespace {name!r} accepted a tenant, fragmenting it: {built!r}"
                )

    try:
        key("not_a_registered_namespace", "x")
    except CacheKeyError:
        pass
    else:
        failures.append("  an unregistered namespace produced a key")

    return failures


def check_reach() -> list[str]:
    failures: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in ALLOWED_DIRECT_REDIS:
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if DIRECT_REDIS_CALL.search(line):
                failures.append(
                    f"  {relative}:{number} calls Redis directly, bypassing key(): {line.strip()}"
                )
    return failures


def main() -> int:
    failures = check_rules() + check_reach()
    if failures:
        print("check_cache_scoping: FAIL")
        for failure in failures:
            print(failure)
        return 1

    scoped = sum(1 for ns in NAMESPACES.values() if ns.tenant_scoped)
    print(
        f"cache scoping ok: {len(NAMESPACES)} namespaces ({scoped} tenant-scoped), "
        "no key escapes the discipline"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
