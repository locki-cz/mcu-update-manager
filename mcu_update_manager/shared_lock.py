"""Coordinate firmware operations with the local aldis agent."""

from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def aldis_operation_lock(path: str = "/tmp/aldis.lock"):
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError("Firmware operations require Linux file locking.") from exc
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
                try:
                    os.fchmod(fd, 0o644)
                except BaseException:
                    os.close(fd)
                    raise
            except FileExistsError:
                fd = os.open(path, flags)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
    except BlockingIOError as exc:
        raise RuntimeError("Another firmware operation is running (aldis lock is held).") from exc
    except OSError as exc:
        raise RuntimeError(f"Cannot acquire firmware operation lock {Path(path)}: {exc}") from exc
    try:
        yield
    finally:
        os.close(fd)
