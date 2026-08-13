"""Configuration loading and fail-fast validation.

Validation lives here, at module scope, on purpose.

Every entrypoint imports this module -- the API server, the Module 7 retention
purge worker, one-off scripts, tests. So none of them can skip the check.
Putting the check in a FastAPI lifespan/startup hook would make it
server-shaped: the worker never runs a lifespan hook, so it would boot with
invalid config and start deleting rows. In a multi-tenant system that is the
worst possible process to let start on a DATABASE_URL pointing at the wrong
environment.

Second reason: PORT is an input to *constructing* the server. It has to be
resolved before uvicorn exists, so validating it inside a server lifecycle
hook is structurally too late.

There are three independent guards here, and each owns a failure the other
two cannot see:

  key parity      a key was renamed or dropped -- including keys that are
                  still optional placeholders, which no field-level check
                  can protect because the field is allowed to be absent
  extra="forbid"  a key exists that the model does not know about, so it
                  would otherwise be read and silently discarded
  Field(...)      a key the app cannot run without is missing entirely

The incident that motivated all three: .env was renamed PORT -> APP_PORT
while the model kept a default. Every layer reported success and the service
came up green on a port nobody asked for.
"""

import difflib
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to this file, not the process cwd. Booting from a different
# directory must not silently produce "no .env found, all defaults".
_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = _ROOT / ".env"
EXAMPLE_FILE = _ROOT / ".env.example"

# Per-field remediation text. A validation error that does not tell you how to
# fix it just moves the guessing from runtime to startup.
FIX_HINTS = {
    "PORT": "set PORT to an integer between 1024 and 65535, e.g. PORT=8000",
}

# Fields whose value may be echoed back in an error message. Allowlist, not
# denylist: default-deny, because the cost of the two mistakes is wildly
# asymmetric. Redacting a value that was safe costs a slightly less helpful
# message; echoing one that was not prints a database password to stderr and
# into every log aggregator downstream. A denylist also silently fails open
# for every field added later -- the failure mode is "someone forgot", which
# is the same failure mode this whole module exists to eliminate.
ECHO_SAFE = {"PORT"}


def describe_input(field: str, err: dict) -> str:
    """Render an error's offending input without leaking secrets.

    Two distinct hazards, not one:

    1. A 'missing' error's `input` is the *parent object* -- the entire
       collected settings dict -- not the absent field. Printing it verbatim
       dumps every value the app was configured with, in one line, on the
       error path most likely to fire in a fresh deployment. Never echo it.

    2. Any other error's `input` is the field's own value, which is safe only
       for fields on ECHO_SAFE.

    A redacted value still carries the diagnostic that usually matters --
    whether something was supplied at all, and roughly what -- without
    carrying the secret.
    """
    if err.get("type") == "missing":
        return ""
    value = err.get("input")
    if field in ECHO_SAFE:
        return f" (got {value!r})"
    if value is None:
        return " (got nothing)"
    return f" (got {type(value).__name__}, {len(str(value))} chars, redacted)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # "forbid", not "ignore". Under "ignore" a key the model does not
        # recognise is read and dropped without a word -- which is precisely
        # how APP_PORT=8000 sat in .env looking correct while the app used a
        # default. Verified behaviour: this raises on an unknown key in the
        # dotenv file, and does NOT raise on unrelated process environment
        # variables (PATH, HOME, ...), because the environment source only
        # extracts declared field names. Only the dotenv source passes its
        # whole contents to the model.
        extra="forbid",
    )

    # --- live this module ------------------------------------------------
    # Required (...), not defaulted. A default here is not a convenience, it
    # is a silent substitution: it converts "you did not configure me" into
    # "I picked something", and the process then boots green on a value
    # nobody chose. Absence must be fatal.
    #
    # Bounds are 1024..65535, not 1..65535. Two legal-but-wrong values are
    # rejected deliberately:
    #
    #   PORT=0     POSIX reads 0 as "bind any free port". The type check
    #              passes, the server starts successfully on an unpredictable
    #              port, and every health check fails against a service that
    #              is running fine.
    #
    #   PORT<1024  Privileged ports. The value validates, then bind() fails
    #              with EACCES for a non-root process. Same shape as PORT=0 --
    #              legal value, illegal outcome, discovered at bind time.
    #
    # The <1024 bound is a judgment call, not a universal rule: a container
    # running as root can bind :80 fine. Rejecting it here because this
    # service is designed to run unprivileged behind a proxy that terminates
    # :80/:443, so a privileged port is always a misconfiguration in this
    # deployment shape. If that ever stops being true, the fix is an explicit
    # opt-in flag (ALLOW_PRIVILEGED_PORT), not loosening this bound -- an
    # opt-in keeps the failure loud for everyone who did not mean it.
    PORT: int = Field(..., ge=1024, le=65535)

    # Which deployment this process believes it is. Required, and an enum
    # rather than a str: "prod" vs "production" vs "Production" silently
    # disabling an environment-gated guard is the same class of drift as
    # APP_PORT. Literal makes the typo a startup failure.
    ENVIRONMENT: Literal["development", "staging", "production"]

    # --- live from Module 2 ----------------------------------------------
    # PostgresDsn, not str. Three layers in one type:
    #   required   -> absence is fatal        (the carry-forward)
    #   non-empty  -> DATABASE_URL= is fatal  (a bare required str accepts "")
    #   format     -> postgres//host, a Redis URL pasted into the wrong key,
    #                 or a bare hostname are all fatal
    # Still cannot tell me the host is the *wrong* Postgres -- that needs the
    # database to assert its own identity, which is connect-time, not parse.
    DATABASE_URL: PostgresDsn

    # Migrations run as the table owner; the app never does. Owners bypass
    # row-level security silently, so an app connected as owner would have
    # policies that exist and do nothing. Two URLs is the enforcement of that
    # split -- one credential per privilege level, not one shared connection
    # with a promise attached.
    MIGRATION_DATABASE_URL: PostgresDsn

    # --- live from Module 3 ----------------------------------------------
    # The origin this service's short links are handed out under, used to build
    # the short_url in a create response.
    #
    # Configured rather than derived from the request. The obvious alternative
    # is request.base_url, which reads the Host header -- a client-supplied
    # value. A caller sending Host: evil.example.com would then be handed back a
    # short_url on evil.example.com for a real code in my database, which is a
    # phishing link with my data behind it, minted by my own API. The same
    # header reaches password-reset emails and absolute links in any later
    # feature, so the rule is: an origin that appears in output is configuration,
    # never input.
    PUBLIC_BASE_URL: AnyHttpUrl

    # --- live from Debugging Module 2 ------------------------------------
    # Minimal-by-default (see the module DECIDE): INFO in normal operation,
    # raised to DEBUG to investigate. A plain str rather than a Literal so an
    # operator can set it without a code change; validated by the logging module
    # when it installs the handler.
    LOG_LEVEL: str = "info"

    # --- placeholders, not live yet --------------------------------------
    # These are exactly the keys field-level validation cannot protect: an
    # optional field is *allowed* to be absent, so renaming MONGODB_URI to
    # MONGO_URI fires nothing here. Key parity is what guards them until the
    # module that makes them required arrives.
    MONGODB_URI: str = ""
    REDIS_URL: str = ""
    JWT_SECRET: str = ""
    API_KEY_A: str = ""
    API_KEY_B: str = ""


def declared_keys(path: Path) -> set[str]:
    """Left-hand sides of assignments in a dotenv-style file.

    Deliberately not a full dotenv parser -- this only needs to know which
    keys a file claims, never their values, so it never touches secrets.
    """
    if not path.is_file():
        return set()
    keys = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def check_key_parity() -> list[str]:
    """Compare the configuration contract against what is actually supplied.

    Returns a list of violations; empty means the contract holds. Pure
    function, no exit, so a test can assert on the result directly.

    .env.example is the contract. Two directions, and they catch opposite
    halves of a rename:

      contract -> supplied   a declared key is not resolvable from .env or
                             the process environment (the key was dropped)
      supplied -> contract   .env declares a key the contract never did
                             (the key was renamed, or is a typo)

    The second direction only applies when .env exists. In a container where
    configuration arrives as real environment variables there is no .env to
    hold a stray key, so that half has no subject -- it is not being skipped,
    it has nothing to check. The first direction still runs against
    os.environ and is what protects production.
    """
    contract = declared_keys(EXAMPLE_FILE)
    if not contract:
        return [
            f"configuration contract missing or empty: {EXAMPLE_FILE}",
            "  fix: .env.example defines the required keys and must ship with the app",
        ]

    problems: list[str] = []
    supplied = declared_keys(ENV_FILE)
    resolvable = supplied | set(os.environ)

    # Third direction: contract <-> model. The other two compare the contract
    # against what is *supplied*; this one compares it against what the code
    # actually reads. Both halves are real failures:
    #
    #   in model, not in contract   an undocumented setting. Nobody deploying
    #                               this knows it exists, so it silently takes
    #                               its default forever.
    #   in contract, not in model   a contract that lies. Someone sets the key
    #                               expecting an effect, and there is no code
    #                               anywhere that reads it -- APP_PORT wearing
    #                               a different hat.
    #
    # model_fields is a class attribute, so this runs before Settings() is
    # ever constructed and stays ahead of instantiation like the other checks.
    fields = set(Settings.model_fields)
    for key in sorted(fields - contract):
        problems.append(
            f"undocumented setting {key!r}: read by Settings but absent from .env.example"
        )
    for key in sorted(contract - fields):
        problems.append(
            f"inert key {key!r}: declared in .env.example but no Settings field reads it"
        )

    for key in sorted(contract - resolvable):
        problems.append(
            f"missing key {key!r}: declared in .env.example but supplied by "
            f"neither .env nor the environment"
        )

    for key in sorted(supplied - contract):
        near = difflib.get_close_matches(key, contract, n=1)
        hint = f" -- did you mean {near[0]!r}?" if near else ""
        problems.append(
            f"unknown key {key!r} in .env: not part of the contract{hint}"
        )

    return problems


def load_settings() -> Settings:
    """Build Settings, letting ValidationError propagate.

    Kept separate from the module-scope call below so a test can assert on
    the failure directly. sys.exit() at import time would take the test
    runner down with it.
    """
    return Settings()


def _load_or_exit() -> Settings:
    # Parity first: it reports the contract violation in the language of the
    # mistake that was made ("did you mean 'PORT'?") rather than the language
    # of its downstream symptom ("Field required"). Both would stop the boot;
    # only one names what to edit.
    problems = check_key_parity()
    if problems:
        print("FATAL: configuration contract violated -- refusing to start", file=sys.stderr)
        print(f"  contract: {EXAMPLE_FILE}", file=sys.stderr)
        print(f"  supplied: {ENV_FILE}", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        sys.exit(1)

    try:
        return load_settings()
    except ValidationError as exc:
        print("FATAL: invalid configuration -- refusing to start", file=sys.stderr)
        print(f"  env file: {ENV_FILE}", file=sys.stderr)
        for err in exc.errors():
            field = ".".join(str(part) for part in err["loc"])
            print(
                f"  {field}: {err['msg']}{describe_input(field, err)}",
                file=sys.stderr,
            )
            hint = FIX_HINTS.get(field)
            if hint:
                print(f"    fix: {hint}", file=sys.stderr)
        sys.exit(1)


settings = _load_or_exit()
