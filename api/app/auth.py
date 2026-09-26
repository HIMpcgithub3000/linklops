"""API-key authentication for the management plane.

Replaces the X-Dev-Tenant-Id header from Module 03, which was a client-supplied
tenant identity -- a trivially forged authorization bypass. The AUTH_BACKEND
guard in app/db.py was written to remove itself when this landed.

The header carries two parts: `X-API-Key: <key_id>.<secret>`. The key_id is a
non-secret prefix used to look the row up; the secret is compared against a hash.
Splitting them is not cosmetic -- without a lookup handle, verification means
scanning every row and hashing against each, which is O(n) per request and makes
constant-time comparison meaningless because the scan leaks timing anyway.

What this module does NOT do: decide what the caller may reach. Authentication
answers who is calling; row-level security answers what they can see. Keeping
those separate is why a handler that forgets to filter returns zero rows instead
of everyone's.
"""

import hashlib
import hmac
import secrets
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import ratelimit
from app.db import SessionLocal, bind_tenant, engine

KEY_HEADER = "X-API-Key"


def hash_secret(secret: str) -> str:
    """sha256, not bcrypt.

    This credential is 256 bits of machine-generated randomness, so it is not
    brute-forceable from its hash the way a human password is. A slow KDF here
    would add latency to every admin request to defend against an attack the
    entropy already prevents.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


def mint() -> tuple[str, str, str]:
    """Return (key_id, secret, secret_hash). The secret is shown once, ever."""
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return key_id, secret, hash_secret(secret)


# Through the SECURITY DEFINER function, not the table. The app role has no
# SELECT on api_keys at all -- it can ask about one key_id and learn nothing else,
# never the set of valid ids and never another tenant's hash.
_LOOKUP = text("SELECT tenant_id, secret_hash, revoked_at FROM verify_api_key(:kid)")
_TOUCH = text("SELECT touch_api_key(:kid)")


def require_principal(x_api_key: str = Header(default="", alias=KEY_HEADER)) -> str:
    """Resolve the calling tenant, or 401. Returns the tenant id as a string.

    Every failure path returns the identical 401 with the identical body. Absent
    header, malformed header, unknown key_id, wrong secret and revoked key are
    five distinguishable conditions internally and one answer externally --
    because telling a caller that a key_id exists but the secret is wrong
    confirms half a credential, and telling them a key is revoked confirms it
    once existed. Same reasoning as the redirect returning one 404 for unknown,
    expired and disabled.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing API key",
        headers={"WWW-Authenticate": KEY_HEADER},
    )

    key_id, _, secret = x_api_key.partition(".")
    if not key_id or not secret:
        raise unauthorized

    with engine.connect() as conn:
        row = conn.execute(_LOOKUP, {"kid": key_id}).first()

    # compare_digest even when the row is missing, against a dummy of the same
    # length, so a request for an unknown key_id costs the same time as one for a
    # known key with a wrong secret. Otherwise the 401 is instant for unknown ids
    # and slower for real ones, which is an oracle for enumerating valid key_ids.
    expected = row.secret_hash if row else "0" * 64
    matches = hmac.compare_digest(hash_secret(secret), expected)

    if row is None or not matches or row.revoked_at is not None:
        # Counted before raising, so brute force is limited by attempt rather
        # than by success. Keyed by key_id: an IP key lets a distributed attacker
        # walk one credential, and a tenant key does not exist yet at this point.
        ratelimit.check("auth_fail", key_id)
        raise unauthorized

    with engine.begin() as conn:
        conn.execute(_TOUCH, {"kid": key_id})

    return str(row.tenant_id)


def get_tenant_session(
    principal: str = Depends(require_principal),
) -> Iterator[Session]:
    """A session bound to the AUTHENTICATED tenant.

    This dependency exists so the ordering is a fact rather than a hope. The
    previous version resolved the tenant from a header inside get_session, and
    replacing that with request.state would have depended on FastAPI resolving
    two sibling dependencies in declaration order -- which is not a guarantee I
    want an authorization boundary resting on. Here the session cannot be
    constructed until require_principal has returned, because it is an argument.

    Same body as db.get_session otherwise: bind the tenant for the transaction so
    row-level security scopes every statement, and let a handler that forgets to
    filter return zero rows rather than everyone's.
    """
    with SessionLocal() as session:
        with session.begin():
            bind_tenant(session, principal)
            yield session
