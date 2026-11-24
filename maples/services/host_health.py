"""Patient requests to slow literature services, and a skip for ones that are down.

Slow is fine. Europe PMC full-text searches take 10-40 s on a bad day and find
papers the other sources miss, so ``patient_get`` waits a long time, and retries
when the service says it is busy (503/429) or a read times out.

Only a host that cannot be reached at all (connection refused, DNS failure,
connect timeout) counts as down. After a few of those in a short window the host
is skipped for a few minutes, so a run does not keep knocking on a closed door.
"""
from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

FAILS_TO_TRIP = 3        # unreachable errors within WINDOW_SECONDS before the host is skipped
WINDOW_SECONDS = 180
COOLDOWN_SECONDS = 300   # how long the host is skipped

_lock = threading.Lock()
_fail_times: dict[str, list[float]] = {}
_skip_until: dict[str, float] = {}


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_down(url: str) -> bool:
    """True while the URL's host is being skipped after repeated timeouts."""
    host = _host(url)
    with _lock:
        until = _skip_until.get(host, 0.0)
        if until and time.time() >= until:
            _skip_until.pop(host, None)
            _fail_times.pop(host, None)
            return False
        return bool(until)


def is_outage_error(exc: BaseException) -> bool:
    """True when the host could not be reached. A slow answer (read timeout) is not an outage."""
    return isinstance(exc, requests.ConnectionError)


RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_WAITS = (3.0, 8.0)   # seconds before the 2nd and 3rd attempt


def patient_get(url: str, *, params=None, headers=None, timeout=(5, 60),
                retry_waits=RETRY_WAITS) -> requests.Response:
    """GET that waits for slow services and retries busy ones; raises on final failure.

    Raises ``HostSkipped`` without a request when the host is currently skipped.
    """
    if is_down(url):
        raise HostSkipped(url)
    attempts = len(retry_waits) + 1
    timeouts = 0
    for attempt in range(attempts):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.ConnectionError as exc:  # unreachable: do not retry, count it
            record_failure(url)
            raise exc
        except requests.Timeout:
            # a slow answer gets one more try; waiting a third time rarely pays off
            timeouts += 1
            if timeouts < 2 and attempt < attempts - 1:
                time.sleep(retry_waits[attempt])
                continue
            raise
        record_success(url)
        if resp.status_code in RETRY_STATUSES and attempt < attempts - 1:
            wait = retry_waits[attempt]
            try:  # honour Retry-After when the service sends a short one
                wait = min(30.0, max(wait, float(resp.headers.get("Retry-After", 0))))
            except ValueError:
                pass
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("unreachable")  # pragma: no cover


class HostSkipped(requests.RequestException):
    """The host is being skipped because it was unreachable moments ago."""


def record_failure(url: str) -> None:
    host = _host(url)
    now = time.time()
    with _lock:
        times = [t for t in _fail_times.get(host, []) if now - t < WINDOW_SECONDS]
        times.append(now)
        _fail_times[host] = times
        if len(times) >= FAILS_TO_TRIP and host not in _skip_until:
            _skip_until[host] = now + COOLDOWN_SECONDS
            logger.warning(
                "%s timed out %d times; skipping it for %d minutes and using other sources",
                host, len(times), COOLDOWN_SECONDS // 60,
            )


def record_success(url: str) -> None:
    host = _host(url)
    with _lock:
        _fail_times.pop(host, None)


def reset() -> None:
    """Forget all failures (used by tests)."""
    with _lock:
        _fail_times.clear()
        _skip_until.clear()
