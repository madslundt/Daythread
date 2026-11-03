"""Request-local single-flight for identical daily-summary map reductions."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar


class SummaryMapCache:
    """Share in-flight map notes within one daily synthesis request only."""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task[str]] = {}
        self.hits = 0

    @staticmethod
    def _retrieve_task_exception(task: asyncio.Task[str]) -> None:
        if not task.cancelled():
            task.exception()

    async def get_or_create(self, key: str, factory: Callable[[], Awaitable[str]]) -> str:
        task = self._tasks.get(key)
        if task is not None and task.done():
            failed = task.cancelled()
            if not failed:
                failed = task.exception() is not None
            if failed:
                self._tasks.pop(key, None)
                task = None
        if task is None:
            task = asyncio.create_task(factory())
            task.add_done_callback(self._retrieve_task_exception)
            self._tasks[key] = task
        else:
            self.hits += 1
        try:
            # A canceled writer must not cancel a map reduction another writer awaits.
            protected = asyncio.shield(task)
            protected.add_done_callback(self._retrieve_task_exception)
            return await protected
        except asyncio.CancelledError:
            if task.cancelled() and self._tasks.get(key) is task:
                self._tasks.pop(key, None)
            raise
        except BaseException:
            if self._tasks.get(key) is task:
                self._tasks.pop(key, None)
            raise

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        pending = [task for task in tasks if not task.done()]
        for task in pending:
            task.cancel()
        if tasks:
            # Retrieve completed exceptions too, including when their sole waiter
            # was canceled while the shielded task was still running.
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


_current_cache: ContextVar[SummaryMapCache | None] = ContextVar(
    "daily_summary_map_cache", default=None,
)


def current_summary_map_cache() -> SummaryMapCache | None:
    return _current_cache.get()


@asynccontextmanager
async def summary_map_cache_scope():
    cache = SummaryMapCache()
    token = _current_cache.set(cache)
    try:
        yield cache
    finally:
        _current_cache.reset(token)
        await cache.close()
