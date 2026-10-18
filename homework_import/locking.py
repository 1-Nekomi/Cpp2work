"""Prevent concurrent CLI/server writers from losing review decisions."""
from contextlib import contextmanager
import os
from functools import wraps


@contextmanager
def workspace_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".write.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("此工作目录正在进行另一项写入或编译检查，请稍后重试") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def locked(function):
    @wraps(function)
    def wrapper(root, *args, **kwargs):
        with workspace_lock(root):
            return function(root, *args, **kwargs)
    return wrapper
