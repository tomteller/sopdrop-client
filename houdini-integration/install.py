#!/usr/bin/env python3
"""
Sopdrop Houdini Integration Installer

Automatically configures houdini.env for Sopdrop integration.

Usage:
    python install.py             # Install for the newest Houdini found
    python install.py --all       # Install for every Houdini found
    python install.py uninstall   # Uninstall (from every Houdini found)
"""

import os
import sys
import platform
from pathlib import Path

# The bundled client ships alongside this script as client/sopdrop/; in the
# monorepo it lives in the sibling sopdrop-client package.
_HERE = Path(__file__).parent.resolve()
for _candidate in (_HERE / "client", _HERE.parent / "sopdrop-client"):
    if (_candidate / "sopdrop" / "houdini_paths.py").exists():
        sys.path.insert(0, str(_candidate))
        break

from sopdrop import houdini_paths  # noqa: E402

# Used only when no Houdini preferences directory exists yet.
NEWEST_KNOWN_VERSION = "22.0"


def get_path_separator():
    """Get the path separator for the current platform."""
    if platform.system() == "Windows":
        return ";"
    return ":"


def get_houdini_prefs_dirs():
    """All Houdini preference directories on this machine, newest first."""
    return houdini_paths.find_prefs_dirs()


def get_houdini_env_path():
    """Find the houdini.env file location for the newest Houdini installed."""
    newest = houdini_paths.newest_prefs_dir()
    if newest is not None:
        print(f"Found Houdini preferences at: {newest}")
        return newest / "houdini.env"

    # Nothing installed yet — create prefs for the newest version we know of.
    # Houdini will pick this up once it is installed and first run.
    fallback = houdini_paths.prefs_root() / houdini_paths.prefs_dir_name(NEWEST_KNOWN_VERSION)
    print(f"No Houdini preferences found; defaulting to: {fallback}")
    return fallback / "houdini.env"


def get_sopdrop_path():
    """Get the path to this houdini-integration directory."""
    return Path(__file__).parent.resolve()



def strip_config_block(content):
    """Remove a previously written Sopdrop block from houdini.env content."""
    lines = content.split("\n")
    kept = []
    skipping = False

    for line in lines:
        if "# Sopdrop Integration" in line:
            skipping = True
            continue
        if skipping:
            if "SOPDROP" in line or "sopdrop" in line.lower():
                continue
            if line.strip() == "":
                continue
            skipping = False
        kept.append(line)

    return "\n".join(kept)


def build_config_block(sopdrop_path):
    """The lines Sopdrop adds to houdini.env."""
    # Use forward slashes for Houdini compatibility
    sopdrop_str = str(sopdrop_path).replace("\\", "/")
    sep = get_path_separator()

    # Build PYTHONPATH: scripts + bundled client
    pythonpath_parts = [
        "$SOPDROP_HOUDINI_PATH/scripts",
        "$SOPDROP_HOUDINI_PATH/client",
    ]

    return f"""# Sopdrop Integration
# https://sopdrop.com
SOPDROP_HOUDINI_PATH = "{sopdrop_str}"
HOUDINI_TOOLBAR_PATH = "$SOPDROP_HOUDINI_PATH/toolbar{sep}&"
HOUDINI_PYTHON_PANEL_PATH = "$SOPDROP_HOUDINI_PATH/python_panels{sep}&"
PYTHONPATH = "{sep.join(pythonpath_parts)}{sep}&"
"""


def install_to(env_path, sopdrop_path, assume_yes=False):
    """Write the Sopdrop configuration into one houdini.env."""
    print(f"houdini.env:  {env_path}")

    if env_path.exists():
        content = env_path.read_text()
        if "SOPDROP_HOUDINI_PATH" in content:
            if not assume_yes:
                print("  Sopdrop is already configured here.")
                response = input("  Update configuration? [y/N]: ").strip().lower()
                if response != "y":
                    print("  Skipped.")
                    return False
            content = strip_config_block(content)
    else:
        content = ""
        env_path.parent.mkdir(parents=True, exist_ok=True)

    if content and not content.endswith("\n"):
        content += "\n"
    content = content.lstrip("\n") + build_config_block(sopdrop_path)

    env_path.write_text(content)
    print("  Configured.")
    return True


def install(all_versions=False):
    """Install Sopdrop integration into houdini.env."""
    sopdrop_path = get_sopdrop_path()

    print("Sopdrop Houdini Integration Installer")
    print("=" * 40)
    print(f"Sopdrop path: {sopdrop_path}")
    print()

    prefs_dirs = get_houdini_prefs_dirs()
    if not prefs_dirs:
        targets = [get_houdini_env_path()]
    elif all_versions:
        targets = [d / "houdini.env" for d in prefs_dirs]
    else:
        targets = [prefs_dirs[0] / "houdini.env"]
        if len(prefs_dirs) > 1:
            others = ", ".join(d.name for d in prefs_dirs[1:])
            print(f"Also found: {others}")
            print("Run with --all to configure those too.")
            print()

    installed = 0
    for env_path in targets:
        if install_to(env_path, sopdrop_path, assume_yes=all_versions):
            installed += 1
        print()

    if not installed:
        print("Nothing changed.")
        return

    print("Next steps:")
    print("  1. Restart Houdini")
    print("  2. Look for the 'Sopdrop' shelf tab")
    print("  3. Click 'Settings' to log in")
    print()
    print("Installation complete!")


def uninstall():
    """Remove Sopdrop integration from every houdini.env we can find."""
    prefs_dirs = get_houdini_prefs_dirs()
    removed = 0

    for prefs_dir in prefs_dirs:
        env_path = prefs_dir / "houdini.env"
        if not env_path.exists():
            continue

        content = env_path.read_text()
        if "SOPDROP_HOUDINI_PATH" not in content:
            continue

        env_path.write_text(strip_config_block(content).strip() + "\n")
        print(f"Removed Sopdrop configuration from {env_path}")
        removed += 1

    if not removed:
        print("Sopdrop is not installed. Nothing to uninstall.")
        return

    print("Restart Houdini to complete uninstallation.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "uninstall" in args:
        uninstall()
    else:
        install(all_versions="--all" in args)
