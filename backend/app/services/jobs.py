"""Minimal background job runner.

Long work (builds, browser QA) runs off the request thread with its own DB session. This is the seam
where a Redis/Celery or managed queue plugs in for multi-worker deployments.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

log = logging.getLogger("siteforge.jobs")
_pool = ThreadPoolExecutor(max_workers=int(os.getenv("SITEFORGE_WORKERS", "2")), thread_name_prefix="sf-job")
_futures: list[Future] = []


def inline() -> bool:
    return os.getenv("SITEFORGE_INLINE_JOBS", "0") == "1"


def submit(fn: Callable, *args) -> None:
    if inline():
        fn(*args)
        return

    def wrapped():
        try:
            fn(*args)
        except Exception:  # pragma: no cover
            log.exception("job failed")

    _futures.append(_pool.submit(wrapped))


def wait_all(timeout: float = 120) -> None:
    for f in list(_futures):
        f.result(timeout=timeout)
    _futures.clear()
