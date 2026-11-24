"""Live status of slow literature sources, for the progress panel.

The browser app sets a reporter for its run; retrieval code calls ``report`` as
searches come back ("Europe PMC: 9 of 24 searches back, 12 papers found"). Without
a reporter (CLI, Python API, tests) ``report`` does nothing.

The reporter lives in a context variable, so it follows the run into
``asyncio.to_thread`` workers. Code that starts its own thread pool should grab
``current()`` first and call that from its worker threads.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable, Optional

Reporter = Callable[[str, dict[str, Any]], None]

_reporter: ContextVar[Optional[Reporter]] = ContextVar("maples_activity_reporter", default=None)


def set_reporter(fn: Optional[Reporter]):
    """Install ``fn(source, fields)`` for the current run; returns a reset token."""
    return _reporter.set(fn)


def current() -> Optional[Reporter]:
    return _reporter.get()


def report(source: str, reporter: Optional[Reporter] = None, **fields: Any) -> None:
    fn = reporter or _reporter.get()
    if fn is None:
        return
    try:
        fn(source, fields)
    except Exception:  # noqa: BLE001 - status display must never break a run
        pass
