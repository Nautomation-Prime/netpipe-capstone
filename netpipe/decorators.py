#!/usr/bin/env python3
"""
Cross-cutting concerns for device operations.
"""

import functools
import json
import logging
import random
import time
from datetime import datetime, timezone

logger = logging.getLogger("netpipe")


def retry(attempts: int = 3, base_delay: float = 2.0,
          exceptions: tuple = (Exception,)):
    """
    Retry with exponential backoff and jitter.

    Only for transient failures. A rejected command is not transient —
    retrying it three times just means being wrong three times.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last: Exception | None = None

            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last = exc
                    if attempt == attempts:
                        break
                    delay = base_delay * (2 ** (attempt - 1))
                    delay += random.uniform(0, delay * 0.1)   # jitter
                    logger.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        func.__name__, attempt, attempts, exc, delay,
                    )
                    time.sleep(delay)

            raise last

        return wrapper
    return decorator


def audited(action: str):
    """
    Emit a structured record of an operation, whatever its outcome.

    Every write the pipeline performs passes through here. Chapter 7
    collects these into the change's audit artefact.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(task, *args, **kwargs):
            started = datetime.now(timezone.utc)
            record = {
                "timestamp": started.isoformat(),
                "action": action,
                "device": task.host.name,
                "site": task.host.get("site"),
                "change_reference": task.host.get("change_reference"),
            }

            try:
                result = func(task, *args, **kwargs)
                record["outcome"] = "success"
                return result
            except Exception as exc:
                record["outcome"] = "failure"
                record["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                record["duration_s"] = round(
                    (datetime.now(timezone.utc) - started).total_seconds(), 2)
                logger.info(json.dumps(record))

        return wrapper
    return decorator
