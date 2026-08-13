"""Seed the LinkOps lab with 25 links so pagination spans 3+ pages.

Two adaptations from the reference seed, both forced by the schema this app
actually has rather than the one the reference sketches:

  columns   the reference writes links(short_code, target_url). Here the columns
            are code and long_url, and the table also requires tenant_id and
            created_by, because every row in this system belongs to a tenant --
            that is the point of the design, not an inconvenience.

  codes     the reference seeds "test1".."test25". Those are 5-6 characters and
            the table rejects them: ck_links_code_entropy requires length >= 7,
            because possession of the code is the only authorization on the
            public redirect. Seeded as test001..test025 instead, which keeps the
            ordering obvious while satisfying the constraint.

Runs as the migration role: creating a tenant is a provisioning operation, and
the app role deliberately holds only SELECT on tenants.
"""

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.models import new_id  # noqa: E402

SEED_TENANT_NAME = "linkops-lab"
COUNT = 25


def main() -> int:
    engine = create_engine(str(settings.MIGRATION_DATABASE_URL))
    with engine.begin() as conn:
        tenant_id = conn.execute(
            text("SELECT id FROM tenants WHERE name = :n"), {"n": SEED_TENANT_NAME}
        ).scalar()
        if tenant_id is None:
            tenant_id = new_id()
            conn.execute(
                text("INSERT INTO tenants (id, name, status) VALUES (:i, :n, 'active')"),
                {"i": tenant_id, "n": SEED_TENANT_NAME},
            )

        actor = new_id()
        inserted = 0
        for i in range(1, COUNT + 1):
            code = f"test{i:03d}"
            existing = conn.execute(
                text("SELECT 1 FROM links WHERE code = :c"), {"c": code}
            ).scalar()
            if existing:
                continue
            conn.execute(
                text(
                    "INSERT INTO links (id, tenant_id, created_by, code, long_url) "
                    "VALUES (:id, :t, :a, :c, :u)"
                ),
                {
                    "id": new_id(),
                    "t": tenant_id,
                    "a": actor,
                    "c": code,
                    "u": f"https://example.com/{i}",
                },
            )
            inserted += 1

        total = conn.execute(
            text("SELECT count(*) FROM links WHERE tenant_id = :t"), {"t": tenant_id}
        ).scalar()

    print(f"seeded {inserted} link(s); tenant {SEED_TENANT_NAME} now holds {total}")
    print(f"tenant_id: {tenant_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
