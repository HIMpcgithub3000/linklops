"""SQLAlchemy 2.0 models.

The index set here is the one argued in CONTEXT, with a plane assigned to
each. Two planes, two access patterns, and tenant_id plays opposite roles in
them:

  data plane (redirect)      WHERE code = ?          tenant_id is an OUTPUT.
                             Public, unauthenticated, carries no tenant
                             context, so `code` must be globally unique --
                             the lookup resolves the owner rather than
                             filtering by it.

  management plane (CRUD)    WHERE tenant_id = ?     tenant_id is an INPUT.
                             Supplied by the caller's identity and used to
                             constrain what may be seen.

Same column, opposite direction. That is why they need different indexes
rather than one compromise index that serves neither.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> uuid.UUID:
    """Time-ordered UUIDv7.

    Not uuid4: random keys scatter inserts across the whole B-tree, causing
    page splits and write amplification on every insert -- the churn cost of
    indexing a high-write column. uuid7 sorts by time, so inserts land at the
    right edge and behave like a sequence.

    Not a sequence either: a sequential integer in /links/<id> is an
    enumeration oracle. It leaks total volume and lets an attacker walk the
    ID space, which a 404-on-wrong-tenant response cannot hide.
    """
    return uuid.uuid7()


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """The owner of data. Distinct from the actor who created it."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    # Suspension source of truth. Links carry a denormalised copy (see
    # Link.tenant_active) so the redirect never joins; this column and that
    # copy are updated in one transaction, so there is no window where a
    # suspended tenant's links still resolve.
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("status in ('active','suspended')", name="ck_tenants_status"),
    )


class Link(Base):
    __tablename__ = "links"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)

    # WHO MAY SEE THIS. The scope boundary, the RLS policy target, and the
    # leading column of every management index. NOT NULL is load-bearing: a
    # row with a null tenant_id belongs to nobody, matches no tenant filter,
    # and becomes data that exists but is unreachable -- or worse, reachable
    # by whoever writes the query that forgets to exclude nulls.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )

    # WHO DID THIS. Attribution only -- must never appear in an authorization
    # predicate. Scoping by actor breaks a shared workspace, orphans rows when
    # a person leaves, and follows a contractor from one tenant into the next.
    # No FK yet: the users table arrives with auth in Module 4.
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    # The credential. Globally unique, because /r/<code> is public and has no
    # tenant context to disambiguate with -- a code resolving to two rows
    # means possession no longer identifies one thing.
    code: Mapped[str] = mapped_column(String(32), nullable=False)

    long_url: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Denormalised from tenants.status. The redirect is ~1000x the create rate,
    # so it stays a single-row read; suspension is a rare admin event that pays
    # a bulk update riding the (tenant_id, ...) index. A boolean, not a copy of
    # the status enum -- copying a decision ("may this resolve?") stays stable
    # as the enum grows meanings, and the reason lives on the tenant row.
    tenant_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    __table_args__ = (
        # DATA PLANE. Total, not partial: a partial unique index excluding
        # disabled links would let two disabled rows share a code, and
        # re-enabling one is a corruption discovered at the worst moment.
        Index("uq_links_code", "code", unique=True),
        # MANAGEMENT PLANE. tenant_id leads because it is the equality
        # predicate; created_at supports the sort; id is the tiebreaker keyset
        # pagination needs to stay stable across equal timestamps.
        Index("ix_links_tenant_created", "tenant_id", "created_at", "id",
              postgresql_ops={"created_at": "DESC", "id": "DESC"}),
        CheckConstraint("length(code) >= 7", name="ck_links_code_entropy"),
    )


class ClickEvent(Base):
    """Append-only click log.

    Separate from Link, not embedded. Embedding is wrong in any engine: the
    highest-traffic links would grow without bound, and every append would
    rewrite the object the redirect path reads -- fusing the analytics write
    path to the hot read path, which is the opposite of the isolation the
    1000:1 ratio demands.

    RANGE-partitioned by clicked_at, declared here and created in the
    migration. Done now, while empty, because retrofitting partitioning onto a
    populated table is a full rewrite. Module 7 retention then becomes
    DROP TABLE click_events_2026_08 -- a metadata operation -- instead of a
    DELETE over millions of rows generating WAL, bloat, and a long
    transaction competing with live traffic.
    """

    __tablename__ = "click_events"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), default=new_id)

    link_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    # Denormalised so tenant-scoped analytics never joins back to links on a
    # table this size. No FK to links: a partitioned child cannot carry a FK
    # to a table whose rows may be purged independently, and the join it would
    # protect is one this column exists to avoid.
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    clicked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user_agent: Mapped[str | None] = mapped_column(Text)
    referrer: Mapped[str | None] = mapped_column(Text)

    # Never the raw IP. A salted hash keeps per-visitor deduplication and
    # abuse detection possible while the stored value is not itself personal
    # data -- the branch constraint says no raw IPs, and "we delete them
    # later" is not the same guarantee as "we never wrote them down".
    ip_hash: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        # The partition key must be part of the primary key.
        PrimaryKeyConstraint("id", "clicked_at", name="pk_click_events"),
        Index("ix_click_events_link_time", "link_id", "clicked_at"),
        Index("ix_click_events_tenant_time", "tenant_id", "clicked_at"),
        {"postgresql_partition_by": "RANGE (clicked_at)"},
    )
