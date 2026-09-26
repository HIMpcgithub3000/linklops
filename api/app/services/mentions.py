"""Agent 2 — @mention extraction. Pure function, no table, no route, no import
of any other agent's module.

Every rule below was fixed in the interface contract before this ran. They are
the cases an agent would otherwise decide silently, and each decision is
invisible in a diff and visible in production.
"""

import re

# @ must NOT be preceded by a word character, which is what makes
# "email@example.com" not a mention. Handle: 1-64 of [A-Za-z0-9._-].
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9._-]{1,64})")
# Fenced blocks first (non-greedy, multiline), then inline spans.
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


def extract_mentions(body: str) -> list[str]:
    """Normalised, de-duplicated handles in first-appearance order.

    Code is blanked rather than removed so that offsets do not shift -- and,
    more importantly, so a mention cannot be created by deletion: removing a
    span could join "@" and "user" that were never adjacent.
    """
    scrubbed = _CODE.sub(lambda m: " " * len(m.group(0)), body)
    seen, out = set(), []
    for handle in _MENTION.findall(scrubbed):
        h = handle.lower().rstrip(".")
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out
