"""
Sopdrop logging helpers.

Sopdrop runs inside Houdini, where every ``print()`` lands in the Houdini
Console.  Step-by-step tracing of a paste or a library sync is invaluable
when something goes wrong and pure noise the rest of the time, so it goes
through ``debug()`` and is silent unless the user asks for it.

Enable tracing with either:

    SOPDROP_DEBUG=1                        # environment variable
    sopdrop.set_debug(True)                # at runtime, from the Python shell

Warnings and errors keep using ``print()`` directly — those are always
worth showing.
"""

import os

_TRUTHY = ("1", "true", "yes", "on")

_enabled = os.environ.get("SOPDROP_DEBUG", "").strip().lower() in _TRUTHY


def is_debug() -> bool:
    """True if verbose tracing is on."""
    return _enabled


def set_debug(enabled: bool) -> None:
    """Turn verbose tracing on or off for this session."""
    global _enabled
    _enabled = bool(enabled)


def debug(message: str) -> None:
    """Print a trace message, but only when debugging is enabled."""
    if _enabled:
        print(f"[Sopdrop] {message}")


def info(message: str) -> None:
    """Print a message the user should always see."""
    print(f"[Sopdrop] {message}")


def warn(message: str) -> None:
    """Print a warning the user should always see."""
    print(f"[Sopdrop] Warning: {message}")
