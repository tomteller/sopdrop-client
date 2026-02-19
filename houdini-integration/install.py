#!/usr/bin/env python3
"""
Sopdrop Houdini Integration Installer

Automatically configures houdini.env for Sopdrop integration.

Usage:
    python install.py             # Install
    python install.py uninstall   # Uninstall
"""

import os
import sys
import platform
from pathlib import Path


def get_path_separator():
    """Get the path separator for the current platform."""
    if platform.system() == "Windows":
        return ";"
    return ":"


def get_houdini_env_path():
    """Find the houdini.env file location."""
    system = platform.system()
    versions = ["21.5", "21.0", "20.5", "20.0", "19.5", "19.0"]

    if system == "Darwin":
        base = Path.home() / "Library" / "Preferences" / "houdini"
    elif system == "Windows":
        base = Path.home() / "Documents"
    else:
        base = Path.home()

    # Find existing houdini directory
    for version in versions:
        if system == "Darwin":
            env_dir = base / version
        else:
            env_dir = base / f"houdini{version}"

        if env_dir.exists():
            print(f"Found Houdini {version} preferences at: {env_dir}")
            return env_dir / "houdini.env"

    # Try to find any houdini directory
    if base.exists():
        existing = sorted(
            [d.name for d in base.iterdir() if d.is_dir()],
            reverse=True
        )
        for name in existing:
            stripped = name.replace("houdini", "").replace(".", "")
            if stripped.isdigit():
                print(f"Found Houdini preferences at: {base / name}")
                return base / name / "houdini.env"

    # Default to latest
    version = versions[0]
    if system == "Darwin":
        return base / version / "houdini.env"
    else:
        return base / f"houdini{version}" / "houdini.env"


def get_sopdrop_path():
    """Get the path to this houdini-integration directory."""
    return Path(__file__).parent.resolve()


def install():
    """Install Sopdrop integration into houdini.env."""
    env_path = get_houdini_env_path()
    sopdrop_path = get_sopdrop_path()

    print("Sopdrop Houdini Integration Installer")
    print("=" * 40)
    print(f"Sopdrop path: {sopdrop_path}")
    print(f"houdini.env:  {env_path}")
    print()

    # Check if already installed
    if env_path.exists():
        content = env_path.read_text()
        if "SOPDROP_HOUDINI_PATH" in content:
            print("Sopdrop is already configured in houdini.env")
            response = input("Update configuration? [y/N]: ").strip().lower()
            if response != "y":
                print("Aborted.")
                return

            # Remove existing sopdrop config block
            lines = content.split("\n")
            new_lines = []
            skip_block = False
            for line in lines:
                if "# Sopdrop Integration" in line:
                    skip_block = True
                    continue
                if skip_block:
                    if "SOPDROP" in line or "sopdrop" in line.lower():
                        continue
                    elif line.strip() == "":
                        continue
                    elif line.strip().startswith("#") and "SOPDROP" not in line.upper():
                        skip_block = False
                        new_lines.append(line)
                    else:
                        skip_block = False
                        new_lines.append(line)
                else:
                    new_lines.append(line)

            content = "\n".join(new_lines)
    else:
        content = ""
        env_path.parent.mkdir(parents=True, exist_ok=True)

    # Use forward slashes for Houdini compatibility
    sopdrop_str = str(sopdrop_path).replace("\\", "/")
    sep = get_path_separator()

    # Build PYTHONPATH: scripts + bundled client
    pythonpath_parts = [
        "$SOPDROP_HOUDINI_PATH/scripts",
        "$SOPDROP_HOUDINI_PATH/client",
    ]

    config_block = f"""
# Sopdrop Integration
# https://sopdrop.com
SOPDROP_HOUDINI_PATH = "{sopdrop_str}"
HOUDINI_TOOLBAR_PATH = "$SOPDROP_HOUDINI_PATH/toolbar{sep}&"
HOUDINI_PYTHON_PANEL_PATH = "$SOPDROP_HOUDINI_PATH/python_panels{sep}&"
PYTHONPATH = "{sep.join(pythonpath_parts)}{sep}&"
"""

    # Append to file
    if content and not content.endswith("\n"):
        content += "\n"
    content += config_block.strip() + "\n"

    env_path.write_text(content)

    print("Configuration written to houdini.env")
    print()
    print("Next steps:")
    print("  1. Restart Houdini")
    print("  2. Look for the 'Sopdrop' shelf tab")
    print("  3. Click 'Settings' to log in")
    print()
    print("Installation complete!")


def uninstall():
    """Remove Sopdrop integration from houdini.env."""
    env_path = get_houdini_env_path()

    if not env_path.exists():
        print("houdini.env not found. Nothing to uninstall.")
        return

    content = env_path.read_text()
    if "SOPDROP_HOUDINI_PATH" not in content:
        print("Sopdrop is not installed. Nothing to uninstall.")
        return

    lines = content.split("\n")
    new_lines = []
    skip_block = False

    for line in lines:
        if "# Sopdrop Integration" in line:
            skip_block = True
            continue
        if skip_block:
            if "SOPDROP" in line or "sopdrop" in line.lower():
                continue
            elif line.strip() == "":
                continue
            elif line.strip().startswith("#") and "SOPDROP" not in line.upper():
                skip_block = False
                new_lines.append(line)
            else:
                skip_block = False
                new_lines.append(line)
        else:
            new_lines.append(line)

    content = "\n".join(new_lines).strip() + "\n"
    env_path.write_text(content)

    print("Sopdrop configuration removed from houdini.env")
    print("Restart Houdini to complete uninstallation.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        uninstall()
    else:
        install()
