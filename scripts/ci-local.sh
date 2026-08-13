#!/bin/bash
# The same stages the GitHub workflow runs, runnable without a remote, a runner
# or registry credentials -- the local fallback path this module documents.
#
# The workflow file is deliberately a thin caller: every stage below is a command
# that also runs by hand. GitHub Actions YAML does not travel to another
# platform, so the portable part of a pipeline has to be the scripts, not the
# thing that calls them.
#
# Usage: scripts/ci-local.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PY=api/.venv/bin/python
FAILED=0

stage () {
  local name="$1"; shift
  local start; start=$(date +%s)
  if "$@" >"/tmp/ci-$name.log" 2>&1; then
    printf '  %-22s PASS  %ss\n' "$name" "$(( $(date +%s) - start ))"
  else
    printf '  %-22s FAIL  %ss   (see /tmp/ci-%s.log)\n' "$name" "$(( $(date +%s) - start ))" "$name"
    tail -3 "/tmp/ci-$name.log" | sed 's/^/      /'
    FAILED=1
  fi
}

echo "CI (local) -- commit $(git rev-parse --short HEAD 2>/dev/null || echo 'NO COMMITS')"
echo

# Cheapest first, so a style error costs seconds rather than the whole run.
stage lint                 api/.venv/bin/ruff check api
stage check_url_policy     "$PY" api/scripts/check_url_policy.py
stage check_log_injection  "$PY" api/scripts/check_log_injection.py
stage check_rls            "$PY" api/scripts/check_rls.py
stage audit_destinations   "$PY" api/scripts/audit_stored_destinations.py

if [ "$FAILED" -ne 0 ]; then
  echo
  echo "  gate: FAILED -- not building an image from code that does not pass"
  exit 1
fi

TAG=$(git rev-parse --short HEAD 2>/dev/null || echo dirty)
stage "docker-build" docker build -t "linkops:$TAG" .
[ "$FAILED" -ne 0 ] && exit 1

mkdir -p artifacts
stage "export-artifact" docker save "linkops:$TAG" -o "artifacts/linkops-$TAG.tar"

echo
echo "  artifact: artifacts/linkops-$TAG.tar ($(du -h "artifacts/linkops-$TAG.tar" 2>/dev/null | cut -f1 | tr -d ' '))"
echo "  tagged by commit SHA, never :latest -- three images tagged latest are"
echo "  three images you cannot tell apart."
