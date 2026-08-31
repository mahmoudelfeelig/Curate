from __future__ import annotations

import asyncio
from contextlib import suppress
from threading import Event

from feed_passport.runtime import DueJobRunner


def test_due_job_runner_processes_without_manual_endpoint_calls() -> None:
    processed = Event()

    async def exercise() -> None:
        runner = DueJobRunner(processed.set, interval_seconds=0.01)
        task = asyncio.create_task(runner.run())
        try:
            completed = await asyncio.wait_for(asyncio.to_thread(processed.wait, 0.5), timeout=1)
            assert completed is True
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(exercise())
