"""One bounded scheduler for web queries, legacy jobs, streams and OCR."""
from __future__ import annotations

import asyncio
from fastapi import HTTPException


class WorkQueue:
    def __init__(self, capacity=4, timeout=180):
        self.capacity = capacity
        self.timeout = timeout
        self.tasks: set[asyncio.Task] = set()
        self._slot = asyncio.Semaphore(1)
        self.active = 0

    def submit(self, operation):
        if len(self.tasks) >= self.capacity:
            raise HTTPException(429, "查询队列已满，请稍后重试", headers={"Retry-After": "10"})

        async def run():
            async with self._slot:
                self.active += 1
                try:
                    return await asyncio.wait_for(operation(), timeout=self.timeout)
                except asyncio.TimeoutError as exc:
                    raise HTTPException(504, "查询超时，请稍后重试") from exc
                finally:
                    self.active -= 1

        task = asyncio.create_task(run())
        self.tasks.add(task)

        def finished(done):
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return task

    async def call(self, operation):
        return await self.submit(operation)

    def status(self):
        return {"active": self.active, "queued": max(0, len(self.tasks) - self.active), "capacity": self.capacity}

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def work_queue(state) -> WorkQueue:
    queue = getattr(state, "work_queue", None)
    if queue is None:
        queue = WorkQueue()
        state.work_queue = queue
    return queue
