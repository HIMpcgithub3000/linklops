"""Module 2 evidence: insert a link, read it back by code, prove RLS bites.

Writes progress/evidence/module-02/query-by-code.txt.

Deliberately more than a round trip. A seed script that inserts and selects
proves the connection works; it does not prove the thing this module is
actually about. So it also runs the negative controls -- the queries that must
return *nothing* -- because a security mechanism nobody has watched fail is a
mechanism nobody knows is on.
"""

import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import SessionLocal, bind_tenant  # noqa: E402
from app.models import new_id  # noqa: E402

# Provisioning a tenant is an owner operation, not an application one: the app
# role holds SELECT on tenants and nothing more, because a service that can
# mint organisations can mint itself a tenant to read from. Seeding therefore
# needs the migration credential -- which is the privilege split doing its job,
# not an obstacle to work around by widening the grant.
owner_engine = create_engine(str(settings.MIGRATION_DATABASE_URL))

OUT = (
    Path(__file__).resolve().parent.parent.parent
    / "progress" / "evidence" / "module-02" / "query-by-code.txt"
)

ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_code(n: int = 8) -> str:
    """Random, never sequential.

    The redirect is authorized by possession of the code, so code entropy is
    the authentication strength -- a guessable code means the endpoint is
    'anyone may read any link'. Ambiguous glyphs are excluded because these
    get read aloud and retyped.
    """
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def main() -> int:
    lines: list[str] = []

    def say(s: str = "") -> None:
        print(s)
        lines.append(s)

    tenant_a, tenant_b = new_id(), new_id()
    actor = new_id()
    code = make_code()
    long_url = "https://example.com/a-fairly-long-destination-url?utm_source=module-02"

    say("MODULE 02 -- persistence evidence")
    say(f"generated at   {datetime.now(UTC).isoformat(timespec='seconds')}")
    say("=" * 72)

    # Tenants are owner records; created as owner because upsk_app is granted
    # SELECT only on tenants -- the app has no business minting organisations.
    with owner_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:a, 'Acme'), (:b, 'Globex')"),
            {"a": tenant_a, "b": tenant_b},
        )
        conn.commit()
    say(f"\ntenant A       {tenant_a}")
    say(f"tenant B       {tenant_b}")

    # ---------------------------------------------------------------- INSERT
    with SessionLocal() as s, s.begin():
        bind_tenant(s, str(tenant_a))
        s.execute(
            text(
                "INSERT INTO links (id, tenant_id, created_by, code, long_url) "
                "VALUES (:id, :tenant, :actor, :code, :url)"
            ),
            {"id": new_id(), "tenant": tenant_a, "actor": actor, "code": code, "url": long_url},
        )
    say("\n--- INSERT (tenant A) ---")
    say(f"inserted code  {code}")
    say(f"long_url       {long_url}")

    # ------------------------------------------------- SELECT by code, in-tenant
    with SessionLocal() as s, s.begin():
        bind_tenant(s, str(tenant_a))
        row = s.execute(
            text("SELECT code, long_url, tenant_id FROM links WHERE code = :c"),
            {"c": code},
        ).one()
    say("\n--- SELECT WHERE code = ? (tenant A session) ---")
    say(f"selected code  {row.code}")
    say(f"matched url    {row.long_url}")
    say(f"owner          {row.tenant_id}")
    say(f"MATCH          code {row.code == code} | long_url {row.long_url == long_url}")

    # -------------------------------------------------------- negative controls
    say("\n--- NEGATIVE CONTROLS (each must return nothing) ---")

    # 1. Same query, different tenant. This is the IDOR: an attacker here is a
    #    legitimate, fully authenticated user -- of another organisation.
    with SessionLocal() as s, s.begin():
        bind_tenant(s, str(tenant_b))
        cross = s.execute(
            text("SELECT count(*) FROM links WHERE code = :c"), {"c": code}
        ).scalar_one()
    say(f"tenant B reading tenant A's code by exact match : {cross} rows")

    # 2. The query that FORGETS the tenant filter entirely. Under RLS this is
    #    the whole point -- forgetting returns zero rows, not everyone's rows.
    with SessionLocal() as s, s.begin():
        bind_tenant(s, str(tenant_b))
        unscoped = s.execute(text("SELECT count(*) FROM links")).scalar_one()
    say(f"SELECT * FROM links with no WHERE (tenant B)    : {unscoped} rows")

    # 3. No tenant bound at all -- the public redirect session's raw view.
    with SessionLocal() as s, s.begin():
        unbound = s.execute(text("SELECT count(*) FROM links")).scalar_one()
    say(f"session with no app.tenant_id set               : {unbound} rows")

    # 4. Writing a row attributed to another tenant. USING governs reads;
    #    without WITH CHECK this would succeed and simply be invisible after.
    with SessionLocal() as s:
        try:
            with s.begin():
                bind_tenant(s, str(tenant_b))
                s.execute(
                    text(
                        "INSERT INTO links (id, tenant_id, created_by, code, long_url) "
                        "VALUES (:id, :tenant, :actor, :code, 'https://evil.example')"
                    ),
                    {"id": new_id(), "tenant": tenant_a, "actor": actor, "code": make_code()},
                )
            wrote = "SUCCEEDED -- WITH CHECK is not working"
        except Exception as exc:
            wrote = f"blocked ({type(exc).__name__})"
    say(f"tenant B inserting a row owned by tenant A      : {wrote}")

    # -------------------------------------------- the tenant-free redirect path
    # The public plane has no tenant, so under the policy above it can read
    # nothing from links directly -- correctly. resolve_link() is the single
    # audited SECURITY DEFINER path that crosses tenants, returning three
    # columns for a link that is resolvable right now.
    with SessionLocal() as s, s.begin():
        redirect = s.execute(
            text("SELECT link_id, tenant_id, long_url FROM resolve_link(:c)"), {"c": code}
        ).one()
    say("\n--- PUBLIC REDIRECT PATH (no tenant bound) ---")
    say(f"resolve_link({code}) -> {redirect.long_url}")
    say(f"resolved owner {redirect.tenant_id}  (tenant_id is an OUTPUT here, not an input)")

    # ------------------------------------------------------ partitioned insert
    with SessionLocal() as s, s.begin():
        bind_tenant(s, str(tenant_a))
        s.execute(
            text(
                "INSERT INTO click_events (id, link_id, tenant_id, ip_hash, user_agent) "
                "VALUES (:id, :link, :tenant, :ip_hash, 'curl/8')"
            ),
            {
                "id": new_id(),
                "link": redirect.link_id,
                "tenant": tenant_a,
                # sha256 of the IP, never the IP. Salted in production; the
                # branch constraint is no raw IPs stored, and "we delete them
                # later" is a different promise from "we never wrote them down".
                "ip_hash": uuid.uuid5(uuid.NAMESPACE_DNS, "203.0.113.7").hex,
            },
        )
        part = s.execute(
            text(
                "SELECT tableoid::regclass::text AS part, count(*) "
                "FROM click_events GROUP BY 1"
            )
        ).all()
    say("\n--- click_events (RANGE partitioned by clicked_at) ---")
    for p, n in part:
        say(f"row landed in  {p}  ({n} row)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwritten -> {OUT}")

    ok = (
        row.code == code
        and row.long_url == long_url
        and cross == 0
        and unscoped == 0
        and unbound == 0
        and wrote.startswith("blocked")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
