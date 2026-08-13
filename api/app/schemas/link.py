"""Request and response shapes for the links API.

Two rules run through this file, and both are about what the model does *not*
accept or emit.

Requests forbid unknown fields. A create body carrying `tenant_id` or
`created_by` must fail loudly rather than be quietly ignored, because "quietly
ignored" and "quietly honoured" look identical from outside and differ by one
careless `**payload` later. In a multi-tenant system the field a client should
never get to set is exactly the field that decides who owns the row.

Responses are an allowlist, not a dump of the ORM object. The Link row carries
tenant_id and created_by; neither belongs in a response body. They are internal
identifiers, and echoing them teaches a client to depend on values that exist
for authorization, which is how an identifier becomes an interface nobody meant
to publish.
"""

import unicodedata
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.url_policy import DestinationRejected, validate_destination

# Bounded because unbounded input is a cost parameter the caller controls: tags
# are stored in a Postgres ARRAY on the same row the redirect path reads, so an
# unbounded list inflates rows on the hot read path, not just the write.
MAX_TAGS = 20
MAX_TAG_LENGTH = 64


class LinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    long_url: str = Field(..., min_length=1)
    expires_at: datetime | None = None
    tags: list[str] | None = None

    @field_validator("long_url")
    @classmethod
    def _validate_destination(cls, value: str) -> str:
        """Delegate to the URL policy and store what it returns.

        The normalised form is what is persisted, so the string that was
        validated and the string later emitted in a Location header are the same
        string. Validating one form and storing another is how a check gets
        bypassed without anyone editing the check.
        """
        try:
            return validate_destination(value)
        except DestinationRejected as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("expires_at")
    @classmethod
    def _must_be_future_and_absolute(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None:
            # A naive datetime is not a moment, it is a moment *plus an
            # assumption about whose clock*. Accepting one means the server
            # silently supplies that assumption, and the caller finds out at the
            # boundary of daylight saving or in a different deployment region.
            # Postgres would store it as a real instant either way, so the
            # ambiguity would be permanent and invisible.
            raise ValueError("expires_at must include a UTC offset, e.g. 2027-01-01T00:00:00Z")
        if value <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return value

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > MAX_TAGS:
            raise ValueError(f"at most {MAX_TAGS} tags are allowed, got {len(value)}")

        cleaned: list[str] = []
        for raw in value:
            tag = raw.strip()
            if not tag:
                raise ValueError("tags must not be empty or whitespace-only")
            if len(tag) > MAX_TAG_LENGTH:
                raise ValueError(f"each tag must be at most {MAX_TAG_LENGTH} characters")
            # Same class of check as the URL policy, for the same reason: a tag
            # is displayed somewhere, and control or bidi characters in a
            # displayed string are a rendering attack, not a formatting quirk.
            for ch in tag:
                if unicodedata.category(ch) in {"Cc", "Cf"}:
                    raise ValueError(
                        f"tag contains a disallowed character: U+{ord(ch):04X}"
                    )
            if tag not in cleaned:
                # Deduplicated rather than rejected -- a repeated tag is a client
                # bug with an obvious intended meaning, unlike the cases above.
                cleaned.append(tag)
        return cleaned


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    short_url: str
    long_url: str
    created_at: datetime
    expires_at: datetime | None = None
    tags: list[str] | None = None


class LinkPage(BaseModel):
    """One page of links, plus the cursor for the next one.

    A cursor rather than a page number. Offset pagination re-scans every skipped
    row, so page 500 costs 500 pages of work, and it is *wrong* under
    concurrency: a row inserted while the client is paging shifts every
    subsequent offset by one, so a row slides from page 2 to page 3 and is never
    seen. The cursor is a position in the sort key, so inserts do not move it.
    """

    items: list[LinkOut]
    next_cursor: str | None = None

    # Only set on the page-numbered path. It is the snapshot the run is pinned
    # to, handed back so the client returns it with each subsequent page --
    # without that round trip every page would be a fresh query against a table
    # that has moved, which is exactly the drift this replaced.
    as_of: datetime | None = None
