"""Exceptions raised by the sandbox."""


class SandboxSecurityError(Exception):
    """Code tried to do something the sandbox policy forbids."""
