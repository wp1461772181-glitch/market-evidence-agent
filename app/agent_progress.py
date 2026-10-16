"""Request-local inactivity timers. Only trusted code may report real progress."""
from contextlib import asynccontextmanager
from contextvars import ContextVar
import asyncio

_WATCHDOGS = ContextVar('agent_progress_watchdogs', default=())


@asynccontextmanager
async def progress_watchdog(idle_seconds):
    async with asyncio.timeout(idle_seconds) as timer:
        token = _WATCHDOGS.set((*_WATCHDOGS.get(), (timer, idle_seconds)))
        try:
            yield
        finally:
            _WATCHDOGS.reset(token)


def mark_progress():
    now = asyncio.get_running_loop().time()
    for timer, idle in _WATCHDOGS.get():
        if not timer.expired():
            timer.reschedule(now + idle)
