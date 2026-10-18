"""Per-app run lock: stops two pipelines (e.g. CLI + dashboard) from working on the same app at once."""
from __future__ import annotations

import fcntl
import os
import threading
from contextlib import contextmanager
from typing import Iterator

from engine.settings import CACHE_DIR

_held: dict[str, int] = {}          # app_id → depth (re-entrant within this process)
_fds: dict[str, int] = {}
_mu = threading.Lock()


class AppBusy(RuntimeError):
    pass


def _path(app_id: str) -> str:
    d = CACHE_DIR / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{app_id}.lock")


@contextmanager
def app_lock(app_id: str, what: str = "run") -> Iterator[None]:
    """Exclusive, re-entrant (within one process) lock for an app. Raises AppBusy if another process holds it."""
    with _mu:
        if _held.get(app_id):
            _held[app_id] += 1
            reentrant = True
        else:
            fd = os.open(_path(app_id), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                holder = os.read(fd, 200).decode(errors="replace").strip()
                os.close(fd)
                raise AppBusy(f"Another run is already working on “{app_id}” ({holder or 'unknown'}). "
                              "Wait for it to finish, then try again.") from None
            os.ftruncate(fd, 0)
            os.write(fd, f"{what} · pid {os.getpid()}".encode())
            _fds[app_id], _held[app_id], reentrant = fd, 1, False
    try:
        yield
    finally:
        with _mu:
            _held[app_id] -= 1
            if _held[app_id] == 0:
                fd = _fds.pop(app_id)
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                del _held[app_id]
        del reentrant


def is_busy(app_id: str) -> bool:
    fd = os.open(_path(app_id), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return not _held.get(app_id)
    finally:
        os.close(fd)


def locked(what: str):  # noqa: ANN201
    """Decorator: hold the app lock (first positional arg = app_id) while the function runs."""
    import functools

    def deco(fn):  # noqa: ANN001, ANN202
        @functools.wraps(fn)
        def wrapper(app_id, *a, **kw):  # noqa: ANN001, ANN202
            with app_lock(app_id, what):
                return fn(app_id, *a, **kw)
        return wrapper
    return deco
