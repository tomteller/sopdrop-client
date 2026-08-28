"""
Houdini preference directory discovery.

Houdini keeps one preferences directory per major.minor version, named
differently on each platform:

    macOS    ~/Library/Preferences/houdini/22.0
    Windows  ~/Documents/houdini22.0
    Linux    ~/houdini22.0

Sopdrop has to find these to install itself (houdini.env lives there) and to
fall back to a sane shelf directory outside Houdini.  Everything here
discovers versions rather than hardcoding a list, so a new Houdini release
works the day it ships.
"""

import platform
import re
from pathlib import Path
from typing import List, Optional, Tuple

# "22.0" or "houdini22.0" / "houdini20.5"
_VERSION_DIR = re.compile(r'^(?:houdini)?(\d+)\.(\d+)$')


def prefs_root() -> Path:
    """The directory that holds Houdini's per-version preference folders."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Preferences" / "houdini"
    if system == "Windows":
        return Path.home() / "Documents"
    return Path.home()


def prefs_dir_name(version: str) -> str:
    """The preferences folder name for a version, e.g. "22.0"."""
    if platform.system() == "Darwin":
        return version
    return f"houdini{version}"


def parse_version(dir_name: str) -> Optional[Tuple[int, int]]:
    """Parse a preferences folder name into (major, minor), or None."""
    match = _VERSION_DIR.match(dir_name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def find_prefs_dirs() -> List[Path]:
    """Every existing Houdini preferences directory, newest version first."""
    root = prefs_root()
    if not root.is_dir():
        return []

    found = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []

    for entry in entries:
        if not entry.is_dir():
            continue
        version = parse_version(entry.name)
        if version is not None:
            found.append((version, entry))

    found.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _, path in found]


def newest_prefs_dir() -> Optional[Path]:
    """The newest existing Houdini preferences directory, or None."""
    dirs = find_prefs_dirs()
    return dirs[0] if dirs else None
