"""Cross-platform advisory file locks.

POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking`` on the first byte.
Both wrappers operate on an already-open file descriptor and never close it.
"""
from __future__ import annotations

import os

if os.name == "nt":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    def _ensure_byte(fd: int) -> None:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)

    def lock_exclusive(fd: int) -> None:
        _ensure_byte(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def lock_nonblocking(fd: int) -> None:
        _ensure_byte(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError("lock is held") from exc

    def unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def lock_exclusive(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def lock_nonblocking(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise

    def unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
