"""Regression control for the destination validation boundary.

Same shape as scripts/check_rls.py, for the same reason: a security control
nobody has watched fail is a control nobody knows is on. This one is cheap
enough to run anywhere -- it touches no database and no network -- so it belongs
in CI on every commit, not just after a schema change.

The table is the point. Each row is a destination and the verdict the boundary
must reach, and rows marked REGRESSION are ones that were once accepted and must
never be accepted again. Adding the failing case here at the same time as the
fix is what turns "I fixed it" into something that stays fixed after the next
refactor of the parser.

Exit 0 = every case behaves. Exit 1 = at least one does not.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.url_policy import DestinationRejected, validate_destination  # noqa: E402

REJECT = "reject"
ACCEPT = "accept"

# (verdict, url, why this case exists)
CASES: list[tuple[str, str, str]] = [
    # --- Module 03 BREAK: accepted once, must never be again ----------------
    (REJECT, "https://good.com%40evil.example.com/login",
     "REGRESSION: percent-encoded userinfo. Browser decodes %40 to '@', so the "
     "host is evil.example.com while the stored text reads good.com"),
    (REJECT, "http://127.1/admin",
     "REGRESSION: short-form IPv4, inet_aton expands to 127.0.0.1"),
    (REJECT, "http://127.0.1/admin",
     "REGRESSION: three-part IPv4, inet_aton expands to 127.0.0.1"),
    (REJECT, "http://0x7f.1/",
     "REGRESSION: hex first octet plus short form"),
    (REJECT, "https://example.com%2f@evil.example.com/",
     "REGRESSION: percent-encoding anywhere in the authority"),

    # --- scheme handling -----------------------------------------------------
    (REJECT, "javascript:alert(1)", "scheme not on the allowlist"),
    (REJECT, "JaVaScRiPt:alert(1)", "scheme comparison is case-insensitive"),
    (REJECT, "  javascript:alert(1)  ", "trimming spaces must not rescue a bad scheme"),
    (REJECT, "data:text/html,<svg/onload=alert(1)>", "data: is not on the allowlist"),
    (REJECT, "file:///etc/passwd", "file: is not on the allowlist"),
    (REJECT, "ftp://example.com/x", "ftp: is not on the allowlist"),
    (REJECT, "//evil.example.com", "scheme-relative: no scheme at all"),
    (REJECT, "http%3A%2F%2Fevil.example.com", "encoded colon means there is no scheme"),

    # --- structure the parser and a browser could read differently -----------
    (REJECT, "http:%2f%2fevil.example.com", "http scheme but no authority: it is all path"),
    (REJECT, "http:\\\\evil.example.com", "backslash: a path here, a host to a browser"),
    (REJECT, "https://good.com@evil.example.com", "literal userinfo"),

    # --- characters that cannot appear in a URL ------------------------------
    # The two below are written as \u escapes on purpose. As literal characters
    # they are invisible in an editor and indistinguishable from an accidental
    # paste -- which is the exact property that makes them worth rejecting.
    (REJECT, "https://example.com\n", "trailing newline must reject, not be trimmed"),
    (REJECT, "https://example.com\r\n", "CRLF: header injection if it ever reached Location"),
    (REJECT, "java\tscript:alert(1)", "embedded tab, stripped by the WHATWG parser"),
    (REJECT, "https://exam\u200bple.com", "zero-width space inside the host"),
    (REJECT, "https://example.com\u202e", "bidi override: changes display, not destination"),

    # --- non-public destinations --------------------------------------------
    (REJECT, "http://127.0.0.1/", "loopback, dotted quad"),
    (REJECT, "http://[::1]/", "loopback, IPv6"),
    (REJECT, "http://[::ffff:127.0.0.1]/", "loopback, IPv4-mapped IPv6"),
    (REJECT, "http://169.254.169.254/latest/meta-data/", "cloud instance metadata"),
    (REJECT, "http://2130706433/", "loopback as a 32-bit integer"),
    (REJECT, "http://0x7f000001/", "loopback in hex"),
    (REJECT, "http://0177.0.0.1/", "loopback with an octal octet"),
    (REJECT, "http://192.168.1.1/admin", "RFC1918: the visitor's own network"),
    (REJECT, "http://0.0.0.0/", "unspecified address"),

    # --- other refusals ------------------------------------------------------
    (REJECT, "https://аpple.com", "Cyrillic homograph: must arrive as punycode"),
    (REJECT, "", "empty"),
    (REJECT, "   ", "whitespace only"),
    (REJECT, "https://example.com/" + "x" * 4000, "over the length cap"),

    # --- must keep working ---------------------------------------------------
    (ACCEPT, "https://example.com", "the ordinary case"),
    (ACCEPT, "https://example.com/path?q=1&b=2#frag", "query and fragment survive"),
    (ACCEPT, "http://example.com:8080/x", "explicit port"),
    (ACCEPT, "https://xn--80ak6aa92e.com/", "punycode is the supported way to say IDN"),
    (ACCEPT, "https://example.com/a%20b", "percent-encoding in the *path* is fine"),
    (ACCEPT, "  https://example.com/ok  ", "surrounding spaces are trimmed"),
    (ACCEPT, "https://[2606:4700:4700::1111]/x", "public IPv6 literal"),
    (ACCEPT, "https://example.com/Case/SENSITIVE", "path case is preserved"),
    (ACCEPT, "https://8.8.8.8/", "a public IP literal is still a valid destination"),
]


def main() -> int:
    failures: list[str] = []

    for expected, url, why in CASES:
        try:
            result = validate_destination(url)
            actual = ACCEPT
        except DestinationRejected as exc:
            result, actual = str(exc), REJECT

        if actual != expected:
            shown = url if len(url) <= 60 else url[:57] + "..."
            failures.append(
                f"  expected {expected}, got {actual}: {shown!r}\n"
                f"      why this case exists: {why}\n"
                f"      boundary said: {result}"
            )

    if failures:
        print(
            f"URL POLICY REGRESSION -- {len(failures)} of {len(CASES)} cases behaved wrongly:",
            file=sys.stderr,
        )
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    rejected = sum(1 for verdict, _, _ in CASES if verdict == REJECT)
    print(
        f"URL policy ok: {len(CASES)} cases -- {rejected} refused, "
        f"{len(CASES) - rejected} accepted, all as declared"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
