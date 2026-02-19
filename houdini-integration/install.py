#!/usr/bin/env python3
"""
Sopdrop Houdini Integration Installer

Automatically configures houdini.env for Sopdrop integration.
"""

import os
import sys
import subprocess
import platform
from pathlib import Path


def get_sopdrop_client_path():
    """Get the path to the sopdrop-client package."""
    # Assume it's a sibling directory
    return Path(__file__).parent.parent / "sopdrop-client"


def get_path_separator():
    """Get the path separator for the current platform."""
    # Houdini uses : on macOS/Linux, ; on Windows
    if platform.system() == "Windows":
        return ";"
    return ":"


def install_client_package():
    """Try to install the sopdrop client package via pip."""
    client_path = get_sopdrop_client_path()

    if not client_path.exists():
        return False, "sopdrop-client package not found"

    print(f"Installing sopdrop client from: {client_path}")

    try:
        # Try pip install -e (editable mode for development)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(client_path)],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return True, "Installed via pip"
        else:
            return False, result.stderr
    except Exception as e:
        return False, str(e)


def get_houdini_env_path():
    """Find the houdini.env file location."""
    system = platform.system()

    # Common Houdini versions to check (newest first)
    versions = ["21.5", "21.0", "20.5", "20.0", "19.5", "19.0"]

    if system == "Darwin":  # macOS
        base = Path.home() / "Library" / "Preferences" / "houdini"
    elif system == "Windows":
        base = Path.home() / "Documents"
    else:  # Linux
        base = Path.home()

    # Find existing houdini directory
    for version in versions:
        if system == "Windows":
            env_dir = base / f"houdini{version}"
        elif system == "Darwin":
            env_dir = base / version
        else:  # Linux
            env_dir = base / f"houdini{version}"

        if env_dir.exists():
            print(f"Found Houdini {version} preferences at: {env_dir}")
            return env_dir / "houdini.env"

    # If no existing directory found, try to detect from running Houdini
    # or list what directories exist
    print("No Houdini preferences directory found.")
    print(f"Checked in: {base}")

    # List what's actually there
    if base.exists():
        existing = [d.name for d in base.iterdir() if d.is_dir()]
        houdini_dirs = [d for d in existing if d.replace("houdini", "").replace(".", "").isdigit() or d.replace(".", "").isdigit()]
        if houdini_dirs:
            print(f"Found directories: {houdini_dirs}")
            # Use the first one found (should be sorted by name)
            houdini_dirs.sort(reverse=True)
            chosen = houdini_dirs[0]
            if system == "Darwin":
                return base / chosen / "houdini.env"
            else:
                return base / chosen / "houdini.env"

    # Default to latest version
    version = versions[0]
    if system == "Windows":
        return base / f"houdini{version}" / "houdini.env"
    elif system == "Darwin":
        return base / version / "houdini.env"
    else:
        return base / f"houdini{version}" / "houdini.env"


def get_sopdrop_path():
    """Get the path to the sopdrop-houdini package."""
    return Path(__file__).parent.resolve()


def install():
    """Install Sopdrop integration into houdini.env."""
    env_path = get_houdini_env_path()
    sopdrop_path = get_sopdrop_path()
    client_path = get_sopdrop_client_path()

    print("Sopdrop Houdini Integration Installer")
    print("=" * 40)
    print(f"Sopdrop Houdini path: {sopdrop_path}")
    print(f"Sopdrop client path: {client_path}")
    print(f"houdini.env: {env_path}")
    print()

    # Always add client to PYTHONPATH for Houdini
    # (Houdini uses its own Python, separate from system Python)
    client_in_pythonpath = True
    print(f"Client will be added to PYTHONPATH for Houdini's Python.")
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

            # Remove existing config
            lines = content.split("\n")
            new_lines = []
            skip_block = False
            for line in lines:
                if "# Sopdrop Integration" in line:
                    skip_block = True
                    continue
                if skip_block and line.strip() and not line.strip().startswith("#"):
                    if "SOPDROP" not in line and "sopdrop" not in line.lower():
                        skip_block = False
                        new_lines.append(line)
                elif not skip_block:
                    new_lines.append(line)

            content = "\n".join(new_lines)
    else:
        content = ""
        # Create directory if needed
        env_path.parent.mkdir(parents=True, exist_ok=True)

    # Build configuration block
    # Use forward slashes even on Windows for Houdini compatibility
    sopdrop_str = str(sopdrop_path).replace("\\", "/")

    # Get user prefs directory for OPmenu
    if platform.system() == "Darwin":
        opmenu_path = "$HOME/Library/Preferences/houdini/$HOUDINI_MAJOR_RELEASE.$HOUDINI_MINOR_RELEASE/OPmenu"
    elif platform.system() == "Windows":
        opmenu_path = "$HOME/Documents/houdini$HOUDINI_MAJOR_RELEASE.$HOUDINI_MINOR_RELEASE/OPmenu"
    else:
        opmenu_path = "$HOME/houdini$HOUDINI_MAJOR_RELEASE.$HOUDINI_MINOR_RELEASE/OPmenu"

    config_block = f"""
# Sopdrop Integration
# https://sopdrop.com
SOPDROP_HOUDINI_PATH = "{sopdrop_str}"
HOUDINI_TOOLBAR_PATH = "$SOPDROP_HOUDINI_PATH/toolbar;&"
HOUDINI_PYTHON_PANEL_PATH = "$SOPDROP_HOUDINI_PATH/python_panels;&"
HOUDINI_OPMENU_PATH = "{opmenu_path};&"
"""

    # Build PYTHONPATH additions
    sep = get_path_separator()
    pythonpath_parts = [
        "$SOPDROP_HOUDINI_PATH/scripts",
        "$SOPDROP_HOUDINI_PATH/scripts/python",  # For pythonrc.py startup
    ]

    # Add client path
    if client_in_pythonpath:
        client_str = str(client_path).replace("\\", "/")
        pythonpath_parts.append(client_str)

    # Check if PYTHONPATH already exists
    if "PYTHONPATH" in content:
        # Append to existing PYTHONPATH
        print("Note: PYTHONPATH already exists. Adding Sopdrop paths to it.")
        pythonpath_value = sep.join(pythonpath_parts) + sep + '$PYTHONPATH'
        content = content.replace(
            "PYTHONPATH",
            f'PYTHONPATH = "{pythonpath_value}"\n# Original PYTHONPATH',
            1
        )
        config_block = config_block.rstrip()
    else:
        pythonpath_value = sep.join(pythonpath_parts)
        config_block += f'PYTHONPATH = "{pythonpath_value}"'

    # Add to content
    if content and not content.endswith("\n"):
        content += "\n"
    content += config_block + "\n"

    # Write
    env_path.write_text(content)

    print("Configuration added to houdini.env")
    print()
    print("Next steps:")
    print("1. Restart Houdini")
    print("2. Look for the 'Sopdrop' shelf")
    print("3. Click 'Settings' to log in")
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

    # Remove Sopdrop configuration
    lines = content.split("\n")
    new_lines = []
    skip_block = False

    for line in lines:
        if "# Sopdrop Integration" in line:
            skip_block = True
            continue
        if skip_block:
            if line.strip().startswith("#") and "SOPDROP" not in line.upper():
                skip_block = False
                new_lines.append(line)
            elif "SOPDROP" in line or "sopdrop" in line.lower():
                continue
            elif line.strip() == "":
                continue
            else:
                skip_block = False
                new_lines.append(line)
        else:
            new_lines.append(line)

    content = "\n".join(new_lines).strip() + "\n"
    env_path.write_text(content)

    print("Sopdrop configuration removed from houdini.env")
    print("Restart Houdini to complete uninstallation.")


def main():
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
