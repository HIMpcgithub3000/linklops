"""Regression control: a log record must never become two log lines.

Third control in the same family as check_rls.py and check_url_policy.py, and
for the same reason -- a property nobody has watched fail is a property nobody
knows holds. No database, no network, no server: it drives the formatter
directly, so it belongs in CI on every commit.

The invariant under test is narrow and total: for ANY value a caller can attach
to a log record, the formatter emits exactly one physical line, and that line
parses as exactly one JSON object whose field carries the hostile text as data.

What this control deliberately does NOT claim: that the logs are now safe to
grep. They are not, and no producer-side change can make them so. If a payload
contains the text "admin login successful", that text is present in the file,
correctly escaped inside a field value -- and `grep -c "admin login"` will still
count it. Producer escaping guarantees structure, not the absence of a string.
The remaining defence belongs to the consumer: parse each line, then read
fields. Anything that splits on newlines and pattern-matches the raw bytes can
still be told a story the structured log does not support.

Exit 0 = the invariant holds for every payload. Exit 1 = it does not.
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging_config import JsonFormatter  # noqa: E402

FORGED = (
    '{"ts":"2025-01-15T15:42:03.500Z","level":"info","req_id":"r-102",'
    '"msg":"admin login successful","user":"admin","ip":"10.0.0.1"}'
)

# (label, value attached to the record)
PAYLOADS: list[tuple[str, object]] = [
    ("the module's payload: newline + a complete forged record",
     "https://normal-url.com\n" + FORGED),
    ("carriage return instead of newline", "https://x.test\r" + FORGED),
    ("CRLF, as a log shipper would split it", "https://x.test\r\n" + FORGED),
    ("bare forged JSON with no line break", FORGED),
    ("quote-escape attempt out of the field", '"} ' + FORGED),
    ("backslash before the quote", '\\" ' + FORGED),
    ("NUL byte", "https://x.test\x00" + FORGED),
    ("unicode line separator U+2028", "https://x.test " + FORGED),
    ("unicode paragraph separator U+2029", "https://x.test " + FORGED),
    ("nested dict carrying a newline", {"url": "https://x.test\n" + FORGED}),
    ("list carrying a newline", ["https://x.test\n" + FORGED]),
    ("non-serialisable object with a hostile repr", object()),
]


def main() -> int:
    formatter = JsonFormatter()
    failures: list[str] = []

    for label, value in PAYLOADS:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="link created", args=None, exc_info=None,
        )
        record.url = value
        line = formatter.format(record)

        if "\n" in line or "\r" in line:
            failures.append(f"  emitted a line break: {label}")
            continue
        if " " in line or " " in line:
            # Not a line break to json.dumps, but JavaScript-based log viewers
            # historically treat both as terminators. Escaped output is required.
            failures.append(f"  emitted a raw U+2028/U+2029: {label}")
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            failures.append(f"  did not parse as one JSON object ({exc}): {label}")
            continue
        if parsed.get("msg") != "link created":
            failures.append(
                f"  the record's own msg was displaced by the payload: {label}"
            )

    if failures:
        print(
            f"LOG INJECTION REGRESSION -- {len(failures)} of {len(PAYLOADS)} payloads "
            "broke the one-record-one-line invariant:",
            file=sys.stderr,
        )
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print(
        f"log format ok: {len(PAYLOADS)} hostile payloads, each emitted as exactly one "
        "parseable JSON object with the payload contained in a field"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
