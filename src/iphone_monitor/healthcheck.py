"""Command used by Docker to decide whether the long-running process is alive."""

import os
import time

from .health import is_healthy, read_status


def main() -> int:
    status_path = os.environ.get("STATUS_PATH", "data/runtime-status.json")
    max_age_seconds = int(os.environ.get("HEALTH_MAX_AGE_SECONDS", "90"))
    try:
        return 0 if is_healthy(
            read_status(status_path),
            now=time.time(),
            max_age_seconds=max_age_seconds,
        ) else 1
    except (OSError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
