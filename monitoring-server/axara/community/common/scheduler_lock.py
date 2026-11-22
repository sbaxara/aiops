# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Shared scheduler leader-election lock.

Hoisted from the previously byte-identical `_acquire_scheduler_lock` copies in
main.py and other schedulers, so every scheduler shares one implementation.

Behaviour is unchanged from the original copies:
  * each named scheduler owns a lock file under the system temp dir,
  * the first uvicorn worker to flock(LOCK_EX | LOCK_NB) wins and keeps the fd
    open (stashed on `builtins`) for the lifetime of the process,
  * other workers get EWOULDBLOCK and skip scheduler startup,
  * non-Linux / unexpected errors fall back to "start anyway" (safe-open),
  * the OS releases the lock on process exit; explicit release is available for
    clean in-process restarts.
"""
import os
import fcntl
import tempfile
import builtins
import logging

logger = logging.getLogger(__name__)


def acquire_scheduler_lock(name: str) -> bool:
    """
    Acquire an exclusive file lock for a named scheduler.

    With --workers N, FastAPI startup events fire in every uvicorn worker
    process. Without a guard each worker would start its own scheduler
    instance, causing duplicate job execution (N escalation checks, N report
    runs, etc.).

    Strategy: each scheduler owns a lock file under the temp dir. The first
    worker that calls flock(LOCK_EX | LOCK_NB) wins and keeps the fd open (and
    locked) for the lifetime of that process. All other workers get
    EWOULDBLOCK and skip scheduler startup. When the winning worker exits the
    OS releases the lock automatically — the next restart election happens
    cleanly.

    Falls back to True on non-Linux platforms (e.g. macOS dev machines) where
    fcntl behaves identically, and gracefully handles any unexpected errors by
    allowing the scheduler to start (safe-open default).

    Returns True if this worker acquired the lock (and should start the named
    scheduler), False if another worker already holds it.
    """
    try:
        lock_path = os.path.join(tempfile.gettempdir(), f'.{name}.scheduler.lock')
        fd = open(lock_path, 'w')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Store fd on the built-in so it stays open (and locked) for the
        # lifetime of this worker process. Python keeps a reference via the
        # module globals dict — good enough for a long-running server.
        setattr(builtins, f'_scheduler_lock_fd_{name}', fd)
        return True
    except OSError:
        # Another worker already holds the lock
        return False
    except Exception as e:
        logger.warning(
            f'Scheduler lock check failed for {name}: {e} — starting anyway'
        )
        return True


def release_scheduler_lock(name: str) -> None:
    """
    Release a named scheduler's leader lock, if this process holds it.

    Generalises the report-specific release block that was previously inline in
    reporting/scheduler.py:stop_scheduler() (which hard-coded
    `_scheduler_lock_fd_report`). The OS releases the lock on process exit
    anyway, but explicit release is cleaner for in-process restarts (e.g. some
    test scenarios). No-op if this process never held the named lock.
    """
    attr = f'_scheduler_lock_fd_{name}'
    lock_fd = getattr(builtins, attr, None)
    if lock_fd is not None:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            delattr(builtins, attr)
        except Exception as e:
            logger.warning(f'Could not release scheduler lock {name} cleanly: {e}')
