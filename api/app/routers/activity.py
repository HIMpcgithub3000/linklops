"""Real-time team activity feed.

ITERATION 2. Iteration 1 is recorded in the iteration log and was restarted, not
refined: it delivered events with a PostgreSQL LISTEN/NOTIFY trigger on the
activity_events table. That is structural -- a trigger fires when the row is
written, which is before any notion of WHO MAY SEE IT exists, so the layer
producing the event cannot filter it. No follow-up prompt reaches that.

Three decisions this file makes, all at the service boundary rather than the
database boundary:

  1. Events are PUBLISHED by the service layer, with a typed payload. The
     database schema is not the wire contract -- renaming a column must not
     break every connected client.
  2. Authorisation happens once, at SUBSCRIBE, against current membership -- and
     is re-checked on a revocation event. A socket held open is a standing grant
     unless something ends it, so "authority is a property of now" needs a
     mechanism on a long-lived connection, not just on a request.
  3. Delivery is per-team channel. A connection joins the teams it is a member
     of, so cross-team leakage is impossible by construction rather than by a
     filter that could be forgotten.
"""

import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

log = logging.getLogger(__name__)
router = APIRouter(tags=["activity"])

# Bounded per connection. An unbounded queue turns one slow client into a memory
# leak that looks like a leak in the broadcaster.
QUEUE_MAX = 100


# eq=False is load-bearing, not style. A plain @dataclass generates __eq__,
# which sets __hash__ = None, which makes Subscriber unhashable, which makes
# every `set[Subscriber]` in Hub raise TypeError on the first join(). Found by
# running it; the code reads perfectly.
@dataclass(eq=False)
class Subscriber:
    ws: WebSocket
    user_id: uuid.UUID
    teams: set[uuid.UUID]
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX))


class Hub:
    """In-process fan-out. Single-process only, and that is stated rather than
    implied -- see the iteration log. Two uvicorn workers means a client
    connected to worker A never sees an event published on worker B, silently.
    The fix is a Redis pub/sub backplane; the interface below does not change
    when it arrives, which is why the Hub exists as a seam at all."""

    def __init__(self) -> None:
        self._by_team: dict[uuid.UUID, set[Subscriber]] = defaultdict(set)

    def join(self, sub: Subscriber) -> None:
        for team in sub.teams:
            self._by_team[team].add(sub)

    def leave(self, sub: Subscriber) -> None:
        for team in sub.teams:
            self._by_team[team].discard(sub)
            if not self._by_team[team]:
                del self._by_team[team]

    def revoke(self, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Membership ended. Drop the team from every live socket for that user.

        This is the mechanism behind decision 2: without it, a socket opened
        while the user was a member keeps receiving that team's events after
        they are removed, forever, because nothing on a long-lived connection
        re-asks the authorisation question.
        """
        for sub in list(self._by_team.get(team_id, ())):
            if sub.user_id == user_id:
                self._by_team[team_id].discard(sub)
                sub.teams.discard(team_id)

    def publish(self, team_id: uuid.UUID, event: dict) -> int:
        """Non-blocking. A full queue drops the event for THAT subscriber only.

        Deliberate: back-pressure from one slow client must not stall the
        publisher, and this feed is not a durable log -- a client that falls
        behind refetches. Dropping is a decision, so it is counted and logged
        rather than silent.
        """
        delivered = 0
        for sub in self._by_team.get(team_id, ()):
            try:
                sub.queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                log.warning(
                    "activity feed drop", extra={"user_id": str(sub.user_id),
                                                 "team_id": str(team_id)}
                )
        return delivered


hub = Hub()


def publish_event(team_id: uuid.UUID, kind: str, actor_id: uuid.UUID, **payload) -> None:
    """The only way an event enters the feed. Called from the service layer --
    teams_service.accept_invitation, revoke_invitation and so on -- never from a
    database trigger, and never with a raw row.

    The wire contract is this dict, not the table.
    """
    hub.publish(team_id, {
        "kind": kind,
        "team_id": str(team_id),
        "actor_id": str(actor_id),
        "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **payload,
    })


async def _pump(sub: Subscriber) -> None:
    """One writer task per connection. Reading and writing on one socket from
    two places is where WebSocket implementations corrupt frames."""
    while True:
        event = await sub.queue.get()
        await sub.ws.send_json(event)


@router.websocket("/ws/activity")
async def activity_feed(websocket: WebSocket, api_key: str = "") -> None:
    principal = await _authenticate(websocket, api_key)
    if principal is None:
        # 1008 policy violation, closed BEFORE accept where possible. One code
        # for every auth failure -- absent, unknown, revoked -- because a
        # distinguishable close code is the same oracle as a distinguishable 401.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    teams = await _teams_for(principal)
    if not teams:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    sub = Subscriber(ws=websocket, user_id=principal, teams=teams)
    hub.join(sub)
    pump = asyncio.create_task(_pump(sub))
    try:
        while True:
            # The read loop exists to observe disconnects, not to accept
            # commands. This socket is write-only by design: a client that can
            # send "subscribe to team X" is a client that can subscribe to a
            # team it does not belong to, and then the authorisation lives in a
            # message handler instead of at connect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        hub.leave(sub)


async def _authenticate(websocket: WebSocket, api_key: str) -> uuid.UUID | None:
    raise NotImplementedError("wired to app.auth once a user identity exists")


async def _teams_for(principal: uuid.UUID) -> set[uuid.UUID]:
    raise NotImplementedError("SELECT team_id FROM team_memberships WHERE user_id = :u")
