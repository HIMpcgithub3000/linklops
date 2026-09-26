"""Fixtures the review found missing. Without this file the whole suite errors
at collection, which is how twelve tests existed and had never run once.

The `db` fixture is deliberately a *real* database session, not a mock: nearly
every property this suite checks -- a partial unique index, a CHECK constraint,
ON CONFLICT DO NOTHING, an UPDATE ... WHERE guard, a row-level-security policy --
is a property of PostgreSQL, and a mock would only assert that the mock behaves
as configured. That is the Module 09 decision (integration-heavy) expressed as a
fixture.

Which makes *where it points* a safety question, and that question is this
module's interlude: a suite aimed at the wrong database does not fail, it
mutates. Two things keep that from happening here -- the session is wrapped in a
transaction that is always rolled back (so a run leaves no trace even against a
populated database), and `_guard_target_database` below refuses to run against
anything whose name looks like production unless explicitly allowed. Rollback
bounds the blast radius; the guard bounds the target. They are different
protections and the module names both.
"""

import os
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

# The intended test database. CI points DATABASE_URL at linkops_test; a
# developer running locally against the working database is caught by the guard
# unless they opt in, because the working database is the one with real rows in
# it and therefore the one the interlude is about.
TEST_DB_MARKERS = ("test", "_ci")


def _database_name() -> str:
    return str(settings.DATABASE_URL).rsplit("/", 1)[-1].split("?", 1)[0]


@pytest.fixture(scope="session", autouse=True)
def _guard_target_database():
    """Refuse to run against a database that does not look like a test database.

    Autouse and session-scoped, so it runs once before any test and cannot be
    forgotten. The escape hatch is deliberate and loud: UPSK_ALLOW_NONTEST_DB=1
    is a decision a person makes on purpose, not a default that drifts. Even
    then every test rolls back, so the escape hatch trades a guarantee for a
    strong convention rather than for real danger.
    """
    name = _database_name()
    looks_like_test = any(marker in name.lower() for marker in TEST_DB_MARKERS)
    allowed = os.environ.get("UPSK_ALLOW_NONTEST_DB") == "1"
    if not looks_like_test and not allowed:
        pytest.exit(
            f"refusing to run the suite against database {name!r}: it is not a test "
            f"database (name lacks {TEST_DB_MARKERS}). Point DATABASE_URL at "
            f"linkops_test, or set UPSK_ALLOW_NONTEST_DB=1 to override -- every test "
            f"rolls back, but the target is yours to choose on purpose.",
            returncode=2,
        )
    yield


@pytest.fixture()
def db():
    """A real database session, rolled back after each test."""
    engine = create_engine(str(settings.DATABASE_URL))
    conn = engine.connect()
    tx = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        tx.rollback()
        conn.close()


@pytest.fixture()
def tenant():
    return uuid.uuid4()


@pytest.fixture(scope="session")
def two_tenants():
    """Two tenant ids that exist in the target database, for isolation tests.

    Created here via a superuser connection and cleaned up at session end, so an
    IDOR test does not depend on whatever tenants happen to be present. The
    app-role session used by tests cannot create tenants (no grant, by design),
    which is exactly why this needs the migration role.
    """
    engine = create_engine(str(settings.MIGRATION_DATABASE_URL))
    a, b = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:a, 'idor-a'), (:b, 'idor-b')"),
            {"a": str(a), "b": str(b)},
        )
    try:
        yield a, b
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM tenants WHERE id IN (:a, :b)"), {"a": str(a), "b": str(b)}
            )
