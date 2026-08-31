from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DueJobRunner:
    """Small runtime loop that advances persisted, one-shot lifecycle jobs.

    The application remains the authority for claiming and completing jobs. This
    runner only supplies a clock pulse, so multiple processes remain safe through
    SQLite's atomic claim operation.
    """

    process_due_jobs: Callable[[], Any]
    interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("scheduler interval must be positive")

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.process_due_jobs)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Feed Passport due-job pass failed")
            await asyncio.sleep(self.interval_seconds)
