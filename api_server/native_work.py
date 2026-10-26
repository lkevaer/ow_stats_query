"""Keep resource ownership until non-cancellable native work actually finishes."""
from __future__ import annotations

import asyncio


async def bounded_thread(operation, *args):
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Python cannot stop a running inference/compression thread. Do not
        # release an admission slot and accumulate hidden workers on timeouts.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
