"""What a link is allowed to point at, and why each rule exists.

This module is a security boundary, not input hygiene. The distinction matters
because it decides where the rule lives. Hygiene is a convenience for the
caller and may live anywhere; a boundary is the place after which the rest of
the system is allowed to stop being suspicious, so it must be impossible to
route around. Every destination reaching the database passes through
`validate_destination()`, and what it returns -- not what the client sent -- is
what gets stored, so the string that was checked and the string later emitted in
a Location header are provably the same string.

The threat here is not "bad data". A shortener's whole function is to make an
attacker-chosen destination wear my domain, so the redirect lends my reputation
to whatever it points at. In a B2B product on one shared hostname, that
reputation is shared by every tenant: one abusive signup mass-producing phishing
links gets the *domain* blocklisted, which takes down links belonging to
customers who did nothing and cannot self-remediate.

Parse, then decide. Never decide on the raw string.

Every scheme-check bypass in the abuse list is the same trick: a string that a
naive matcher reads one way and a browser reads another.

    JaVaScRiPt:alert(1)          case
    " javascript:alert(1) "      leading whitespace
    java<TAB>script:...          embedded control characters
    http:%2f%2fevil.example.com  scheme passes, but there is no host at all
    http%3A%2F%2Fevil...         the colon is encoded, so there is no scheme
    http:\\evil.example.com      backslashes: a path to urlsplit, a host to a browser
    //evil.example.com           no scheme, inherits the page's
    https://good.com@evil...     the part before @ is userinfo, not the host

Substring matching loses to all of them. So the order is fixed: reject what
cannot appear in a URL at all, parse with a real parser, then judge the parsed
components -- and reject anything where the parser's reading and a browser's
reading could differ.
"""

import ipaddress
import socket
import unicodedata
from urllib.parse import urlsplit, urlunsplit

# Schemes are an allowlist, never a denylist -- the same default-deny reasoning
# as ECHO_SAFE in app/config.py. A denylist fails open for every scheme invented
# after this line was written, and the two mistakes are wildly asymmetric: a
# wrongly rejected destination is a support ticket, a wrongly accepted one is an
# incident. javascript: and data: are blocked by not being on the list, which is
# stronger than blocking them by name.
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Bounded because every unbounded input is a denial-of-service parameter, and
# because a destination this long is not a link anyone typed.
MAX_URL_LENGTH = 2048

# A raw backslash cannot legally appear in a URL -- it must be percent-encoded.
# It is called out separately from the generic checks because it is the one
# character where Python's parser and a browser disagree about *structure*:
# urlsplit reads "http:\\evil.example.com" as a path, a browser reads it as a
# host. Anything the two disagree about is rejected rather than normalised,
# because "normalise to whose reading?" has no safe answer.
_BACKSLASH = "\\"


class DestinationRejected(ValueError):
    """A destination that will not be stored.

    A distinct type so the router can map it to 400 without catching unrelated
    ValueErrors from deeper in the stack and reporting a server bug as user error.
    """


def _describe_forbidden_char(ch: str) -> str:
    """Name a rejected character without echoing it into the response.

    Echoing the offending character back would put attacker-controlled bytes --
    including the control and bidi characters this function exists to reject --
    into an error message that ends up in logs and consoles. The codepoint is
    the diagnostic that matters and is inert.
    """
    return f"U+{ord(ch):04X} ({unicodedata.category(ch)})"


def _forbidden_char(value: str) -> str | None:
    """First character that must not appear anywhere in a URL, if any.

    Runs after trimming, so it is judging the *interior* of the string.

    Cc  C0/C1 controls, which is NUL, tab, newline and friends. The WHATWG URL
        parser silently strips tab and newline anywhere in a URL, so
        "java\\tscript:alert(1)" is a working javascript: URL in a browser while
        being a harmless-looking non-match for any scheme comparison.
    Cf  format characters: zero-width spaces and, worse, the bidi overrides
        (U+202E and friends) that reorder how a URL is *displayed* without
        changing where it points -- the mechanism behind a link that reads as
        one domain and resolves to another.
    Zs/Zl/Zp  interior whitespace of any kind. A real space in a URL is %20.
    """
    for ch in value:
        if unicodedata.category(ch) in {"Cc", "Cf", "Zs", "Zl", "Zp"}:
            return ch
        if ch == _BACKSLASH:
            return ch
    return None


def _as_ip(host: str) -> ipaddress._BaseAddress | None:
    """Interpret a host as an IP literal the way the resolver would, or None.

    All of these reach 127.0.0.1, and only the first is what anyone pictures
    when they say "an IP address":

        http://127.0.0.1        dotted quad
        http://[::1]            IPv6 literal, brackets stripped by urlsplit
        http://2130706433       the same address as a 32-bit decimal integer
        http://0x7f000001       and as hex
        http://0177.0.0.1       octal first octet
        http://127.1            short form: one octet, then the low 24 bits
        http://127.0.1          short form: two octets, then the low 16 bits

    The short forms are why this delegates to socket.inet_aton() rather than
    enumerating spellings by hand. An earlier version of this function parsed
    the integer and dotted-quad cases itself and missed 127.1 -- which is not an
    exotic trick, it is what inet_aton has always done, and it is what a browser
    does with it. inet_aton *is* the algorithm the resolver applies, so asking it
    is the difference between blocking the spellings I thought of and blocking
    the ones that actually resolve. It rejects hostnames ('example.com') and
    over-long forms ('1.2.3.4.5'), so a non-match here is a real name.

    ipaddress.ip_address() is still tried first because it is the only one of the
    two that understands IPv6.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def _is_internal(ip: ipaddress._BaseAddress) -> bool:
    """Addresses a public short link has no business pointing at."""
    return (
        ip.is_private          # RFC1918 and friends -- someone's LAN
        or ip.is_loopback      # the visitor's own machine
        or ip.is_link_local    # includes 169.254.169.254, cloud instance metadata
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_destination(raw: str) -> str:
    """Return the normalised destination, or raise DestinationRejected.

    What this does NOT do, stated plainly because the gap is the interesting
    part: it does not resolve DNS. `http://totally-normal.example` pointing at
    127.0.0.1 passes here. That is a deliberate limit, not an oversight --

      it would be a network call on a write path, so an attacker chooses how
      long my create endpoint blocks;

      and it would be time-of-check-to-time-of-use anyway. DNS is mutable, so a
      name that resolves publicly at create time can resolve privately an hour
      later, and nothing validated at write time can bind what happens at read.

    Blocking IP *literals* is cheap, needs no network, and cannot be undone by a
    later DNS change, so it is worth doing on its own terms. It is not SSRF
    protection and must not be mistaken for it. Right now there is no SSRF
    surface at all: the server never fetches the destination, it hands the
    visitor a 302. The moment a preview, unfurl, favicon or link-health feature
    fetches a stored URL, protection has to be re-applied at fetch time, against
    the resolved address, and re-applied again on every hop of that fetch's own
    redirect chain.

    What the literal block does buy today: a 302 to http://192.168.1.1/ is
    fetched by the *visitor's* browser from inside the visitor's network, which
    reaches devices this server never could.
    """
    # Trim spaces, and *only* spaces. A leading or trailing space is a plausible
    # copy-paste artifact, so trimming it is a kindness with no security cost.
    # A tab, newline or zero-width character is never an artifact -- something
    # put it there -- so those must reach the rejection below rather than be
    # silently removed. Trimming them would mean the string I validated is not
    # the string the caller sent, which is exactly how a trailing "\r\n" payload
    # survives a check and turns up somewhere that treats CRLF as structure.
    trimmed = raw.strip(" ")
    if not trimmed:
        raise DestinationRejected("destination is empty")
    if len(trimmed) > MAX_URL_LENGTH:
        raise DestinationRejected(
            f"destination is {len(trimmed)} characters; the limit is {MAX_URL_LENGTH}"
        )

    bad = _forbidden_char(trimmed)
    if bad is not None:
        raise DestinationRejected(
            f"destination contains a character that cannot appear in a URL: "
            f"{_describe_forbidden_char(bad)}"
        )

    try:
        parts = urlsplit(trimmed)
    except ValueError as exc:
        # urlsplit raises on a malformed IPv6 literal, among others. The reason
        # is not echoed: it is derived from attacker input.
        raise DestinationRejected("destination is not a parseable URL") from exc

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        # Covers javascript:, data:, file:, and -- because the scheme is empty --
        # scheme-relative "//evil.example.com" and percent-encoded "http%3A//".
        shown = scheme or "none"
        raise DestinationRejected(
            f"scheme {shown!r} is not allowed; permitted schemes are "
            f"{', '.join(sorted(ALLOWED_SCHEMES))}"
        )

    if not parts.netloc:
        # "http:%2f%2fevil.example.com" gets here: the scheme is legitimately
        # http, but the encoded slashes mean there is no authority component at
        # all, so the whole thing is a path. A browser handed this does not agree.
        raise DestinationRejected("destination has no host")

    if "%" in parts.netloc:
        # No legitimate authority needs percent-encoding: a host is punycode or
        # ASCII, and a port is digits. An encoded one exists to be decoded later
        # by something that disagrees with the parser here -- which is exactly
        # how https://good.com%40evil.example.com/ got stored. urlsplit sees one
        # opaque host and finds no '@' to object to; a browser decodes %40 back
        # to '@', at which point good.com is userinfo and the real host is
        # evil.example.com. Rejecting the encoding outright is stronger than
        # decoding and re-checking, because decode-then-check invites the next
        # question -- how many times to decode -- and any answer below "until it
        # stops changing" is another bypass.
        raise DestinationRejected("destination host must not contain percent-encoding")

    if "@" in parts.netloc:
        # https://good.com@evil.example.com resolves to evil.example.com; the
        # trusted-looking part is userinfo. Rejected outright rather than
        # stripped, because a destination whose visible name disagrees with its
        # real host is the phishing primitive itself, not a formatting quirk.
        raise DestinationRejected("destination must not contain userinfo before '@'")

    try:
        host = parts.hostname
    except ValueError as exc:
        raise DestinationRejected("destination has an invalid host") from exc
    if not host:
        raise DestinationRejected("destination has no host")

    if not host.isascii():
        # Unicode hosts are homograph territory -- аpple.com with a Cyrillic а
        # is a different domain that renders identically. Requiring the caller
        # to submit punycode makes the ambiguity theirs to resolve explicitly,
        # and xn--... is at least visibly not what it imitates.
        raise DestinationRejected(
            "destination host must be ASCII; submit an internationalised domain "
            "in punycode (xn--...) form"
        )

    ip = _as_ip(host)
    if ip is not None and _is_internal(ip):
        raise DestinationRejected(
            f"destination points at a non-public address ({ip.compressed}); "
            "short links must resolve to the public internet"
        )

    try:
        port = parts.port
    except ValueError as exc:
        raise DestinationRejected("destination has an invalid port") from exc

    # Rebuild from the parsed components, lowercasing only the host. Scheme and
    # host are case-insensitive by definition; path and query are not, and
    # touching them would silently change what the destination means.
    netloc = host if port is None else f"{host}:{port}"
    if ip is not None and ip.version == 6:
        netloc = f"[{host}]" if port is None else f"[{host}]:{port}"

    return urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment))
