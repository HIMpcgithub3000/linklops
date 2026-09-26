"""Click events: what the redirect puts on the queue, and how it is anonymised.

Everything a click event carries is decided here rather than in the router, so
the question "what personal data does a redirect record?" has one answer in one
file instead of an answer assembled from a handler and a worker.

The queue payload changed shape in Module 07 and the reason is delivery
semantics. It used to be a bare link id, which is only sufficient while the
queue is at-most-once: pop the job, apply it, and a crash in between simply
loses a click. Adding retries and a dead-letter path makes delivery at-least-
once, and at that point the same job can arrive twice -- so the payload has to
carry the two fields that make a replay recognisable:

  event_id     minted here, at enqueue time. Not derived from (link_id, ip,
               minute) or any other natural key: a derived key collapses two
               genuine clicks by the same visitor in the same window into one,
               which trades a visible double-count for an invisible undercount.
               A minted id says "this click", not "a click like this".

  clicked_at   also minted here, not stamped by the worker. Two reasons and
               both matter. click_events is RANGE partitioned on clicked_at
               with primary key (id, clicked_at), so a worker stamping its own
               timestamp would send a retry into a different partition, where
               the ON CONFLICT that provides idempotency would never fire. And
               a job that sits in the queue during a backlog would be counted in
               the hour it was *processed* rather than the hour it happened,
               which silently smears traffic across buckets exactly when the
               system is under load and the numbers matter most.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import Request

from app.config import settings

# Bounded before storage. Both are attacker-controlled headers on the highest
# traffic endpoint in the product, so an unbounded one is a row-size amplifier
# the caller controls. The limits are generous against real values -- real user
# agents run to a few hundred characters -- and the truncation is silent because
# a rejected redirect would be a worse outcome than a clipped analytics field.
MAX_USER_AGENT = 512
MAX_REFERRER = 1024


def hash_ip(ip: str | None) -> str | None:
    """Salted sha256 of the client address, or None when there is no address.

    The salt is not decoration. An unsalted digest of an IPv4 address is a
    reversible encoding, not anonymisation: the whole space is 2^32 values, so
    hashing all of them and building a reverse table is minutes of work. With a
    secret salt the table has to be rebuilt per deployment by someone who
    already holds the salt, which is the property that makes the stored value
    genuinely not an address.

    What this deliberately still allows is the thing the column exists for --
    counting distinct visitors, and recognising the same visitor across clicks --
    because equal addresses still produce equal hashes. That is the trade: the
    value is linkable but not reversible. Pretending otherwise would be worse
    than being clear about it.
    """
    if not ip:
        return None
    return hashlib.sha256(f"{settings.CLICK_IP_SALT}:{ip}".encode()).hexdigest()


def build_event(link_id: uuid.UUID, request: Request, client_ip: str | None) -> str:
    """The JSON payload for one click, ready to push.

    Serialised here rather than in the router so the queue's contract and the
    anonymisation live together -- a second call site that built its own dict
    would be the obvious way for a raw IP to reach the queue.
    """
    user_agent = request.headers.get("user-agent")
    referrer = request.headers.get("referer")  # The header is misspelled in HTTP.
    return json.dumps(
        {
            "event_id": str(uuid.uuid4()),
            "link_id": str(link_id),
            # Explicitly UTC and explicitly marked. The worker parses this back
            # into a timestamptz and buckets from it, so an implied zone here
            # would become an off-by-hours analytics bug at the far end.
            "clicked_at": datetime.now(UTC).isoformat(),
            "user_agent": user_agent[:MAX_USER_AGENT] if user_agent else None,
            "referrer": referrer[:MAX_REFERRER] if referrer else None,
            # Hashed before it is serialised, so a raw address never exists in
            # the queue, in a Redis snapshot, or in anything that dumps the
            # queue for debugging.
            "ip_hash": hash_ip(client_ip),
            "attempts": 0,
        }
    )
