"""
Run the automatic expiration rules once, then exit.

The API already sweeps on its own timer (app/scheduler.py). This is the
same code path for when that isn't the right place for it: a real cron job,
a Render cron service, a Kubernetes CronJob. Set SWEEP_ENABLED=false on the
web process and schedule this instead — running both is harmless, since the
sweep is idempotent, but it is pointless.

    python -m scripts.sweep

Exits non-zero on failure so a scheduler notices. A sweep that fails
silently is the failure mode that matters here: everything keeps serving
requests and nothing changes status, and the first sign of trouble is a
shelf of expired food nobody was told about.
"""
import json
import sys

from app import scheduler


def main() -> None:
    result = scheduler.run_once()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Sweep failed: {exc}")
