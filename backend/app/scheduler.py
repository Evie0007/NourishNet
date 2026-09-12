"""
The background scheduler — what makes the expiration rules automatic.

FR-10.2 asks for a sweep at an interval no longer than 5 minutes. Before
this, both sweeps existed but nothing ran them: `POST
/reservations/expire-stale` was an endpoint someone had to remember to
call, and its own docstring admitted as much. A rule nobody runs is not a
rule, it is a comment.

This is an asyncio task on the app's own event loop rather than APScheduler
or a cron container. It adds no dependency and no second process to deploy,
which is the right trade at this size. Its limits, stated plainly so they
are chosen rather than discovered:

  · Each web worker runs its own copy. The sweeps are written to tolerate
    that (conditional updates, FOR UPDATE SKIP LOCKED), so the cost of N
    workers is N times the query, not N times the effect.
  · It dies with the process, so a crashed worker stops sweeping until it
    restarts. Acceptable while a missed sweep only delays a status change
    by one interval.
  · Set SWEEP_ENABLED=false and run `python -m scripts.sweep` from a real
    scheduler once either of those stops being acceptable — which is the
    point at which this should become a proper cron job.
"""
import asyncio
import logging
import os
from typing import Optional

from . import crud, expiration
from .database import SessionLocal

logger = logging.getLogger(__name__)

SWEEP_ENABLED = os.getenv("SWEEP_ENABLED", "true").lower() in ("1", "true", "yes")
SWEEP_INTERVAL_SECONDS = int(os.getenv("SWEEP_INTERVAL_SECONDS", "60"))

# Back off after a failure rather than hammering a database that is down;
# the next successful tick resets to the normal interval.
FAILURE_BACKOFF_SECONDS = 300

_task: Optional[asyncio.Task] = None


def run_once() -> dict[str, int]:
    """
    One pass of every automatic rule. Also the entry point for an external
    scheduler, so the two ways of running this cannot drift apart.

    Order matters: reservations are expired first so that an item whose
    hold just lapsed is back in the pool before the expiration sweep judges
    it. The other order would let a stale reservation keep an item out of
    the donation network for a whole extra interval.
    """
    db = SessionLocal()
    try:
        expired = crud.expire_stale_reservations(db)
        result = expiration.sweep(db)
        result["reservations_expired"] = len(expired)
        return result
    finally:
        db.close()


async def _loop() -> None:
    logger.info("expiration scheduler started (every %ss)", SWEEP_INTERVAL_SECONDS)
    while True:
        delay = SWEEP_INTERVAL_SECONDS
        try:
            # The sweep is blocking DB work. Off the event loop it goes, or
            # it stalls every request in flight for its duration.
            await asyncio.to_thread(run_once)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad tick must not end the loop
            logger.exception("expiration sweep failed; retrying in %ss", FAILURE_BACKOFF_SECONDS)
            delay = FAILURE_BACKOFF_SECONDS
        await asyncio.sleep(delay)


def start() -> None:
    """Start the loop, unless it is disabled or already running."""
    global _task
    if not SWEEP_ENABLED:
        logger.warning(
            "SWEEP_ENABLED is false — no automatic expiration. Items will not "
            "change status on their own; run `python -m scripts.sweep` on a "
            "schedule, or nothing will."
        )
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="nourishnet-expiration-sweep")


async def stop() -> None:
    """Cancel the loop and wait for it, so shutdown doesn't cut a sweep in half."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    finally:
        _task = None
        logger.info("expiration scheduler stopped")
