"""Coordinate cleanup with synchronous draft copies and reference changes."""
from contextlib import contextmanager
from functools import wraps
import fcntl
import os
import stat
import threading


_held = threading.local()


@contextmanager
def _lock(store, exclusive):
    key = str(store.home)
    held = getattr(_held, 'locks', None)
    if held is None:
        held = _held.locks = {}
    if key in held:
        if exclusive and not held[key]:
            raise ValueError('A workspace operation is still using storage. Wait for it to finish before cleanup.')
        yield
        return
    fd = os.open(store.home / 'storage-activity.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('The storage activity lock is not a regular file.')
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Storage cleanup or a workspace operation is in progress. Wait for it to finish.') from exc
        held[key] = exclusive
        try:
            yield
        finally:
            del held[key]
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def activity(store):
    with _lock(store, False):
        from .storage_cleanup import assert_mutation_allowed
        with store.connect() as db:
            assert_mutation_allowed(db)
        yield


@contextmanager
def exclusive(store):
    with _lock(store, True):
        yield


def mutation(function):
    @wraps(function)
    def guarded(store, *args, **kwargs):
        with activity(store):
            return function(store, *args, **kwargs)
    return guarded
