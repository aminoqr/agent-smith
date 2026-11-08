"""Audit hook: a second line of defence behind the restricted builtins.

The restricted builtins only see what the model writes. The audit hook sees
what Python actually does (opening a file, connecting a socket, starting a
program), whatever route the code took to get there.
"""
import contextlib
import os
import sys
from typing import List

from agent_smith.sandbox.policy import is_path_allowed

BLOCKED_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty", "pty.spawn", "os.kill", "os.killpg",
    "os.chdir", "os.putenv", "os.unsetenv", "os.mkfifo", "os.mknod",
})
BLOCKED_PREFIXES = ("socket.", "ctypes.")

READ_EVENTS = frozenset({"os.listdir", "os.scandir"})
WRITE_EVENTS = frozenset({
    "os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.chmod",
    "os.truncate", "os.symlink", "os.link",
})
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _opens_for_writing(args: tuple) -> bool:
    """For an `open` event: does it ask for write access?"""
    mode, flags = args[1], args[2]
    if isinstance(mode, str) and any(letter in mode for letter in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & WRITE_FLAGS)


class _Guard:
    """The audit hook. It only acts while `enforcing()` is active."""

    def __init__(self):
        self.active = False
        self.allowed_directories: List[str] = []
        self.library_roots: List[str] = []

    def __call__(self, event: str, args: tuple) -> None:
        if not self.active:
            return
        if event in BLOCKED_EVENTS or event.startswith(BLOCKED_PREFIXES):
            raise PermissionError(f"{event} is not allowed in the sandbox")
        if event == "open":
            writes, paths = _opens_for_writing(args), args[:1]
        elif event in READ_EVENTS:
            writes, paths = False, [args[0] if args[0] is not None else "."]
        elif event in WRITE_EVENTS:
            writes, paths = True, args[:2]
        else:
            return
        for path in paths:
            if isinstance(path, (str, bytes, os.PathLike)):
                self._check_path(os.fsdecode(path), writes)

    def _check_path(self, path: str, writes: bool) -> None:
        if is_path_allowed(path, self.allowed_directories):
            return
        if not writes and is_path_allowed(path, self.library_roots):
            return
        allowed = ", ".join(self.allowed_directories)
        raise PermissionError(f"access to '{path}' denied (allowed: {allowed})")


guard = _Guard()


def install(allowed_directories: List[str]) -> None:
    """Register the hook. Call once per process, before running model code."""
    guard.allowed_directories = list(allowed_directories)
    guard.library_roots = sorted({sys.prefix, sys.base_prefix, sys.exec_prefix})
    sys.addaudithook(guard)


@contextlib.contextmanager
def enforcing():
    """Turn the hook's rules on for the duration of the block."""
    guard.active = True
    try:
        yield
    finally:
        guard.active = False
