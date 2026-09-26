"""Prometheus metrics: the numbers that describe the shape of traffic.

Pull-based (Module 04 DECIDE): the process exposes GET /metrics and knows nothing
about who scrapes it. Same principle as the logging design — the app emits, the
infrastructure collects — so a metrics collector being down can never affect a
request, exactly as a log shipper being down cannot.

Two metrics, and the label choices are the whole design:

  http_requests_total   a counter (only ever goes up) labeled by method, path
                        and status. Rate is derived by the scraper over time.

  http_request_duration_seconds  a histogram, because an average lies: 99
                        requests at 10ms and one at 10s average to ~110ms, a
                        number no request ever experienced. A histogram answers
                        p95/p99, which is what an SLO is written against.

**`path` is the ROUTE TEMPLATE, not the raw URL.** `/r/aB3xY9k` and `/r/Zq1mn8P`
are the same route `/r/{code}`; labeling by the raw path would mint a new label
series per short code and blow up cardinality until the scraper falls over — the
classic Prometheus footgun. The middleware resolves the matched route and passes
that, so there is one series per route, not per request.

Tenant is deliberately NOT a label here. The Stripe lesson (a per-merchant
failure hidden by a healthy global average) argues for a tenant dimension — but
a label multiplies series by its cardinality, and tenant count is unbounded and
growing, so tenant belongs in the logs (where it already is, queryable per line)
and not in a metric label. Per-tenant analysis is a log query; per-route health
is a metric.
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    ProcessCollector,
    generate_latest,
)

# Process metrics: resident memory, CPU, open FDs. Registered so a slow memory
# climb — the Module 08 leak — is a graph with a trend, not a surprise OOM. The
# default registry picks these up automatically; naming the collector here makes
# the intent explicit. process_resident_memory_bytes is the one to alert on: a
# line that trends up instead of plateauing is a leak, and the earliest place to
# see it is here rather than in the container getting OOM-killed.
try:
    ProcessCollector()
except ValueError:
    pass

REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests, by method, route template and status.",
    ["method", "path", "status"],
)

DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds, by method and route template.",
    ["method", "path"],
    # Buckets tuned to this service: a redirect should be single-digit ms, a
    # management query tens of ms; the tail above 1s is where an SLO breach
    # lives. Default buckets top out coarser than this service's happy path.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)


def observe(method: str, path: str, status: int, duration_seconds: float) -> None:
    REQUESTS.labels(method=method, path=path, status=str(status)).inc()
    DURATION.labels(method=method, path=path).observe(duration_seconds)


def render() -> tuple[bytes, str]:
    """The scrape payload and its content type."""
    return generate_latest(), CONTENT_TYPE_LATEST
