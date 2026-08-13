"""Structured logging: one JSON object per line, one guaranteed line per request.

Design follows the Module 02 DECIDE (minimal by default), and the three things
that make "minimal" safe rather than blind:

  a guaranteed completion line   every request emits exactly one `request
                                 completed` at INFO with the same field names
                                 regardless of endpoint or outcome. The sample
                                 logs in the lesson fail this -- some requests
                                 end with `response sent`, others with a domain
                                 event -- so "how long did requests take" needs
                                 a different query per endpoint. One line per
                                 request, not per query, is also what keeps this
                                 affordable at a 1000:1 read ratio.

  a runtime-changeable level     LOG_LEVEL is read from configuration, so
                                 raising verbosity does not require a redeploy
                                 that restarts the process holding the state you
                                 were trying to observe.

  correlation                    req_id ties every line of one request together,
                                 and tenant_id answers the question every
                                 incident asks second: which customers.

Level assignment answers "would someone have to act on this?", never "is this
interesting?". That is why a 404 on an unknown short code is INFO here: an
unknown code is the service working correctly, and an error stream that is
mostly 404s teaches everyone that ERROR does not mean act -- the same blindness
as filtering out a line that mattered, reached from the opposite direction.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

# Set per request by the middleware and read by the formatter. A ContextVar
# rather than a thread local: the request may hop threads inside the async
# stack, and a thread local would silently attach one request's id to another's
# lines -- a correlation error is worse than no correlation, because it is
# believed.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)

# Keys the stdlib puts on every LogRecord. Anything outside this set was passed
# by the caller via `extra=` and belongs in the JSON output.
_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            # Explicitly UTC and explicitly marked as such. A timestamp whose
            # zone is implied is the Module 02 Bug #4 waiting to happen: two
            # systems each assume their own local zone and the gap only shows
            # up near a day boundary.
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if (rid := request_id.get()) is not None:
            payload["req_id"] = rid
        if (tid := tenant_id.get()) is not None:
            payload["tenant_id"] = tid
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # json.dumps escapes newlines inside string values, so one record is
        # always one physical line. That is the property the whole format rests
        # on -- a log line is only trustworthy if a value cannot end it -- and it
        # is currently implicit in a library's behaviour. Asserted here so that
        # swapping the serialiser, or adding a "fast path" that formats by hand,
        # fails loudly instead of silently reopening log injection.
        line = json.dumps(payload, default=str)
        if "\n" in line or "\r" in line:  # pragma: no cover - invariant guard
            line = json.dumps(
                {
                    "ts": payload["ts"],
                    "level": "error",
                    "logger": __name__,
                    "msg": "log record contained a line break and was suppressed",
                    "original_logger": record.name,
                }
            )
        return line


def configure(level: str) -> None:
    """Install the JSON handler on the root logger.

    stdout, not a file. A container's logs belong to the orchestrator, and a
    process that owns its own log file also owns rotation, disk-full behaviour,
    and the question of what happens to the file when the container is replaced.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn's access log duplicates the completion line below, in a different
    # shape and without req_id or tenant. Two records of the same event that
    # disagree on format is worse than one.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def new_request_id() -> str:
    return f"r-{uuid.uuid4().hex[:8]}"
