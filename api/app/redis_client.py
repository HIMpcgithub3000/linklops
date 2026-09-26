"""Redis connection for the analytics queue.

REDIS_URL is read through app.config, not os.environ. The reference sketches
`os.environ["REDIS_URL"]`, which fails at the first use rather than at startup
and skips the contract checks in config.py entirely -- the same shape as the
APP_PORT incident that motivated those checks. Going through Settings means a
missing or renamed key is a refused boot with a named field, everywhere, for
every entrypoint including this worker.

Not on /ready. Redis is a queue for a background rollup here, and the redirect
path is designed to survive its absence -- see enqueue_click(). Adding it to
readiness would drop the whole fleet out of the load balancer for a dependency
the user-facing path does not need, which is the more thorough check that makes
availability worse.
"""

import redis

from app.config import settings

ANALYTICS_QUEUE = "analytics:queue"

# Where a job lives while a worker is applying it. BRPOPLPUSH moves the job to
# this list atomically, so a worker that dies mid-job leaves the job visible
# here instead of losing it -- the difference between at-most-once and
# at-least-once delivery. It is only safe to upgrade to at-least-once because
# the write is idempotent; without that, this list would convert lost clicks
# into double-counted ones.
ANALYTICS_PROCESSING = "analytics:processing"

# Jobs that failed MAX_ATTEMPTS times. Kept rather than dropped: a poison
# message is a bug report with the payload attached, and a queue that silently
# discards what it cannot handle is a queue that hides the bug forever.
ANALYTICS_DEAD = "analytics:dead"

# decode_responses=True so the queue carries str, not bytes. The worker compares
# what it pops against a uuid; bytes would compare unequal to every str and fail
# silently rather than loudly.
client = redis.from_url(str(settings.REDIS_URL), decode_responses=True)
