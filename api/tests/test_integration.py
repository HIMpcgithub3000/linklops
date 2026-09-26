"""The six behaviours Module 09 names as highest-risk, tested against real Postgres.

Integration-heavy by decision (module_09.test_strategy = integration_heavy), and
the reason is where the logic lives: tenant isolation is an RLS policy, code
entropy is a CHECK, retention is a partition drop, the destination check is a
stored policy. A mock would assert the mock. So these bind a real tenant on a
real session and let the database enforce what it enforces.

Mapped to the module's required list:

  create a link            test_create_link_persists_with_entropy
  redirect correctness     test_redirect_resolves_only_live_links
  auth returns 401         test_missing_or_bad_key_is_401
  owner scoping (IDOR)     test_tenant_cannot_touch_another_tenants_link
  retention enforcement    test_purge_drops_expired_click_partitions (in test_retention.py)
  URL validation bypass    test_url_policy_rejects_a_known_bypass
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.auth import require_principal
from app.services import links_service as svc
from app.services.url_policy import DestinationRejected, validate_destination


def _bind(db, tenant):
    db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
    # Drop the identity map. Without this, a link one tenant created is returned
    # from cache on the next `session.get` without a query, so RLS is never
    # consulted -- which would make an IDOR test pass for the wrong reason. Two
    # real tenants are two requests with two sessions and empty caches; expiring
    # here is what models that inside one transaction.
    db.expire_all()


def _make_link(db, tenant, url="https://example.com/integ", **kw):
    return svc.create_link(
        db, tenant_id=tenant, actor_id=uuid.uuid4(),
        long_url=url, expires_at=kw.get("expires_at"), tags=kw.get("tags"),
    )


# --- create ----------------------------------------------------------------

def test_create_link_persists_with_entropy(db, two_tenants):
    a, _ = two_tenants
    _bind(db, a)
    link = _make_link(db, a)
    row = db.execute(
        text("SELECT code, long_url, tenant_id FROM links WHERE id = :i"), {"i": str(link.id)}
    ).one()
    assert row.long_url == "https://example.com/integ"
    assert row.tenant_id == a
    # The code is a credential on the public redirect, so its length is a
    # security property, enforced by ck_links_code_entropy at >= 7.
    assert len(row.code) >= 7


# --- redirect correctness --------------------------------------------------

def test_redirect_resolves_only_live_links(db, two_tenants):
    """resolve_code is the redirect's whole decision. It must resolve a live
    link and refuse a disabled or expired one identically -- one 'no'."""
    a, _ = two_tenants
    _bind(db, a)
    link = _make_link(db, a, url="https://example.com/live")

    resolved = svc.resolve_code(db, link.code)
    assert resolved is not None and resolved[1] == "https://example.com/live"

    db.execute(
        text("UPDATE links SET disabled_at = now() WHERE id = :i"), {"i": str(link.id)}
    )
    assert svc.resolve_code(db, link.code) is None, "a disabled link still resolved"

    assert svc.resolve_code(db, "nonexistent-code") is None


# --- auth ------------------------------------------------------------------

def test_missing_or_bad_key_is_401():
    """Every auth failure is the same 401 -- absent, malformed, unknown."""
    for bad in ["", "no-dot-here", "deadbeef.wrongsecret"]:
        with pytest.raises(HTTPException) as exc:
            require_principal(x_api_key=bad)
        assert exc.value.status_code == 401
        # The body must not distinguish the five internal causes.
        assert exc.value.detail == "invalid or missing API key"


# --- owner scoping / IDOR --------------------------------------------------

def test_tenant_cannot_touch_another_tenants_link(db, two_tenants):
    """The single most important test in the suite: B cannot see, update, or
    search A's link. Enforced by the database, not by this code -- which is why
    it is worth a test that actually swaps the bound tenant."""
    a, b = two_tenants

    _bind(db, a)
    link = _make_link(db, a, url="https://example.com/owned-by-a", tags=["a-only"])
    a_id = link.id

    # Same session, now acting as B. RLS keys on app.tenant_id, so rebinding is
    # exactly what a second tenant's request does.
    _bind(db, b)

    assert svc.get_link(db, a_id) is None, "B read A's link by id (IDOR)"

    # Update through the service returns None (not found) rather than mutating.
    assert svc.update_link(db, a_id, long_url="https://evil.example/") is None

    # And it is genuinely untouched: back as A, the URL is unchanged.
    _bind(db, a)
    still = svc.get_link(db, a_id)
    assert still is not None and still.long_url == "https://example.com/owned-by-a"

    # Search as B must not surface it either.
    _bind(db, b)
    _, total = svc.search_links(
        db, q=None, tag="a-only", sort="created", direction="desc", page=1, page_size=10
    )
    assert total == 0, "B found A's link through search (IDOR via search)"


# --- URL validation bypass -------------------------------------------------

def test_url_policy_rejects_a_known_bypass(db):
    """A concrete bypass string, not a generic 'bad url'. javascript: is the
    canonical open-redirect-to-XSS payload the policy exists to refuse."""
    for bypass in ["javascript:alert(1)", "JavaScript:alert(1)", "  javascript:alert(1)"]:
        with pytest.raises(DestinationRejected):
            validate_destination(bypass)

    # And a legitimate URL still passes, so the test is not vacuously strict.
    assert validate_destination("https://example.com/ok").startswith("https://")
