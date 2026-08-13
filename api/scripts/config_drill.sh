#!/usr/bin/env bash
#
# Configuration failure drill.
#
# Every case rewrites .env in full instead of patching a line. The previous
# version used `sed 's|^PORT=.*|PORT=X|'`, which matched nothing once the key
# had been renamed to APP_PORT -- so three different cases silently ran the
# same input and all three reported success. A harness that can no-op is worse
# than no harness: it reports green for work it never did. Writing the whole
# file cannot no-op.
#
# Each case asserts three things, not one:
#   1. non-zero exit
#   2. no "Uvicorn running on" line  (that line is emitted at bind)
#   3. the specific expected message  (so a case cannot pass by failing for
#      some unrelated reason -- an assertion on "it failed" alone is nearly
#      worthless, because everything fails when the interpreter is broken)
#
# And it includes a positive control. A drill whose every case expects failure
# passes trivially against an app that fails at everything.

set -u
cd "$(dirname "$0")/.."

PY=.venv/bin/python
ENV=.env
EXAMPLE=.env.example

BACKUP_ENV=$(mktemp)
BACKUP_EXAMPLE=$(mktemp)
cp "$ENV" "$BACKUP_ENV"
cp "$EXAMPLE" "$BACKUP_EXAMPLE"
restore () {
    cp "$BACKUP_ENV" "$ENV"
    cp "$BACKUP_EXAMPLE" "$EXAMPLE"
    rm -f "$BACKUP_ENV" "$BACKUP_EXAMPLE"
}
# EXIT alone is not enough: if a case hangs and the drill is killed from
# outside, .env is left holding a deliberately broken value. INT/TERM too.
trap restore EXIT INT TERM

# Seconds a case may run before it is treated as a failure. A validation
# failure exits in well under a second; anything still alive at this point has
# booted a server, which is the outcome the case exists to forbid. Without
# this bound an unexpectedly-successful boot hangs the drill forever, and a
# harness that hangs reports nothing at all -- the same class of silent
# non-report as the sed that matched nothing.
CASE_TIMEOUT=5

PASS=0
FAIL=0

# write_env <port-lines...>  -- always writes the other six keys
write_env () {
    : > "$ENV"
    for line in "$@"; do printf '%s\n' "$line" >> "$ENV"; done
    cat >> "$ENV" <<'EOF'
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg://upsk_app:upsk_app_pw@127.0.0.1:55432/upsk_sdf
MIGRATION_DATABASE_URL=postgresql+psycopg://upsk_owner:upsk_dev_pw@127.0.0.1:55432/upsk_sdf
MONGODB_URI=mongodb://localhost:27017/shortener
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=replace-with-a-long-random-string
API_KEY_A=replace-with-tenant-a-key
API_KEY_B=replace-with-tenant-b-key
EOF
}

expect_fail () {   # <name> <expected substring>
    local name="$1" want="$2" out code bound listener log pid watchdog timedout

    log=$(mktemp)
    $PY -m app.main > "$log" 2>&1 &
    pid=$!
    ( sleep "$CASE_TIMEOUT"; kill -9 "$pid" ) >/dev/null 2>&1 &
    watchdog=$!
    wait "$pid" 2>/dev/null; code=$?
    kill "$watchdog" >/dev/null 2>&1; wait "$watchdog" 2>/dev/null

    out=$(cat "$log"); rm -f "$log"
    timedout=0; [ "$code" -eq 137 ] && timedout=1   # 137 = killed by watchdog
    bound=$(printf '%s' "$out" | grep -c 'Uvicorn running on')
    listener=$(lsof -i :8000 2>/dev/null | wc -l | tr -d ' ')

    if [ "$timedout" -eq 0 ] && [ "$code" -ne 0 ] && [ "$bound" -eq 0 ] \
       && [ "$listener" -eq 0 ] && printf '%s' "$out" | grep -qF "$want"; then
        printf '  PASS  %-34s exit=%s  bind=0  :8000 empty\n' "$name" "$code"
        PASS=$((PASS + 1))
    else
        if [ "$timedout" -eq 1 ]; then
            printf '  FAIL  %-34s TIMEOUT after %ss -- it booted and kept serving\n' \
                   "$name" "$CASE_TIMEOUT"
        else
            printf '  FAIL  %-34s exit=%s  bind=%s  :8000 lines=%s\n' \
                   "$name" "$code" "$bound" "$listener"
        fi
        printf '        wanted substring: %s\n' "$want"
        printf '%s\n' "$out" | sed 's/^/        | /'
        FAIL=$((FAIL + 1))
    fi
}

expect_ok () {     # <name> <expected resolved PORT>
    local name="$1" want="$2" out code
    out=$($PY -c 'from app.config import settings; print(settings.PORT)' 2>&1); code=$?
    if [ "$code" -eq 0 ] && [ "$out" = "$want" ]; then
        printf '  PASS  %-34s exit=0  PORT=%s\n' "$name" "$out"
        PASS=$((PASS + 1))
    else
        printf '  FAIL  %-34s exit=%s  got=%s want=%s\n' "$name" "$code" "$out" "$want"
        FAIL=$((FAIL + 1))
    fi
}

expect_no_leak () {  # <name> <secret>...
    local name="$1"; shift
    local out code leaked=0 s
    out=$($PY -m app.main 2>&1); code=$?
    for s in "$@"; do
        if printf '%s' "$out" | grep -qF -- "$s"; then
            leaked=1
            printf '        LEAKED: %s\n' "$s"
        fi
    done
    if [ "$code" -ne 0 ] && [ "$leaked" -eq 0 ]; then
        printf '  PASS  %-34s exit=%s  0/%s secrets in output\n' "$name" "$code" "$#"
        PASS=$((PASS + 1))
    else
        printf '  FAIL  %-34s exit=%s  secrets leaked\n' "$name" "$code"
        printf '%s\n' "$out" | sed 's/^/        | /'
        FAIL=$((FAIL + 1))
    fi
}

# Preflight. Every case asserts ":8000 is empty" as proof it did not bind --
# but that is a global fact, not a fact about this case. If a server is already
# running, all nine cases fail for a reason that has nothing to do with them,
# and the output points at the wrong thing. Check the precondition once, name
# it, and refuse to run -- rather than emitting nine confusing failures.
if lsof -i :8000 >/dev/null 2>&1; then
    echo "REFUSING TO RUN: something is already listening on :8000" >&2
    lsof -i :8000 >&2
    echo "  fix: stop it first -- the drill needs the port free to prove a case did not bind" >&2
    exit 2
fi

echo "=== config drill ==="

# -- the case that was structurally unwritable while PORT had a default ------
write_env
expect_fail "PORT key absent" "missing key 'PORT'"

# -- the incident: key renamed, both halves of parity fire -------------------
write_env "APP_PORT=8000"
expect_fail "PORT renamed to APP_PORT" "did you mean 'PORT'"

# -- an unknown key alongside a valid PORT: extra=\"forbid\" is the guard -----
write_env "PORT=8000" "PROT=8000"
expect_fail "unknown key beside valid PORT" "unknown key 'PROT'"

# -- original value cases, unchanged -----------------------------------------
write_env "PORT="
expect_fail "PORT blank" "unable to parse string as an integer"

write_env "PORT=0"
expect_fail "PORT=0 (POSIX any-free-port)" "greater than or equal to 1024"

write_env "PORT=70000"
expect_fail "PORT=70000" "less than or equal to 65535"

write_env "PORT=80"
expect_fail "PORT=80 (privileged)" "greater than or equal to 1024"

# -- the model's own requirement, isolated from parity -----------------------
# This case used to drop PORT from .env.example so parity would stay silent.
# The third parity direction (contract <-> Settings fields) makes that setup
# illegal now -- it trips "undocumented setting 'PORT'" first. Which means
# parity strictly precedes Field(...) for every missing-key case, and the
# required marker is no longer reachable through the .env path at all.
#
# It still has to be tested, because parity guards the *contract* and Field()
# guards the *model*, and someone will eventually construct Settings without
# a dotenv (a test, a script, the Module 7 worker). So probe the model
# directly with dotenv disabled: config imports cleanly against a valid .env,
# then Settings(_env_file=None) sees no PORT anywhere and must refuse.
write_env "PORT=8000"
out=$($PY -c '
from pydantic import ValidationError
from app.config import Settings
try:
    Settings(_env_file=None)
    print("NO ERROR -- the model accepted a missing PORT")
except ValidationError as exc:
    print(exc)
' 2>&1)
if printf '%s' "$out" | grep -qF "Field required"; then
    printf '  PASS  %-34s model rejects absent PORT with dotenv disabled\n' "Field(...) alone"
    PASS=$((PASS + 1))
else
    printf '  FAIL  %-34s\n' "Field(...) alone"
    printf '%s\n' "$out" | sed 's/^/        | /'
    FAIL=$((FAIL + 1))
fi

# -- an OPTIONAL key renamed: no field-level check can see this -------------
# DATABASE_URL is Optional[str]="" this module, so Field() is content with its
# absence and would never fire. Parity is the only guard that notices.
cat > "$ENV" <<'EOF'
PORT=8000
DB_URL=postgresql://user:password@localhost:5432/shortener
MONGODB_URI=mongodb://localhost:27017/shortener
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=replace-with-a-long-random-string
API_KEY_A=replace-with-tenant-a-key
API_KEY_B=replace-with-tenant-b-key
EOF
expect_fail "optional key renamed" "missing key 'DATABASE_URL'"

# -- Module 2 carry-forwards -------------------------------------------------
# A bare required str accepts "". PostgresDsn does not, and also rejects a
# well-formed URL for the wrong protocol -- the format layer, not just presence.
write_env "PORT=8000"
sed -i '' -E 's|^DATABASE_URL=.*|DATABASE_URL=|' "$ENV"
expect_fail "DATABASE_URL empty" "DATABASE_URL"

write_env "PORT=8000"
sed -i '' -E 's|^DATABASE_URL=.*|DATABASE_URL=redis://localhost:6379/0|' "$ENV"
expect_fail "DATABASE_URL wrong protocol" "DATABASE_URL"

# Third parity direction: a key in the contract that no Settings field reads.
write_env "PORT=8000"
cp "$BACKUP_EXAMPLE" "$EXAMPLE"
echo "TOTALLY_INERT_KEY=x" >> "$EXAMPLE"
echo "TOTALLY_INERT_KEY=x" >> "$ENV"
expect_fail "inert key in contract" "inert key 'TOTALLY_INERT_KEY'"
cp "$BACKUP_EXAMPLE" "$EXAMPLE"

# -- secrets must never appear in error output ------------------------------
# A 'missing' error's input is the whole collected settings dict, so this path
# once printed every password in one line. Real-shaped secrets on purpose: a
# redaction test using "xxx" as the secret proves nothing.
cat > "$ENV" <<'EOF'
DATABASE_URL=postgresql://app_user:hunter2-prod-pw@db.internal:5432/shortener
MONGODB_URI=mongodb://root:mongo-prod-pw@mongo.internal:27017/shortener
REDIS_URL=redis://:redis-prod-pw@cache.internal:6379/0
JWT_SECRET=sk-live-9f3a2b1c8e7d6a5b4c3d2e1f
API_KEY_A=tenant-a-live-key-abc123
API_KEY_B=tenant-b-live-key-def456
EOF
grep -v '^PORT=' "$BACKUP_EXAMPLE" > "$EXAMPLE"   # parity silent, Field required fires
expect_no_leak "no secrets in error output" \
    "hunter2-prod-pw" "mongo-prod-pw" "redis-prod-pw" \
    "sk-live-9f3a2b1c8e7d6a5b4c3d2e1f" \
    "tenant-a-live-key-abc123" "tenant-b-live-key-def456"
cp "$BACKUP_EXAMPLE" "$EXAMPLE"

# -- positive control: valid config must still load --------------------------
write_env "PORT=8000"
expect_ok "valid config loads" "8000"

echo
printf '=== %s passed, %s failed ===\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
