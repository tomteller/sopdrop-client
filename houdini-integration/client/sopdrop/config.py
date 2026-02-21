"""
Sopdrop configuration management.

Config file location: ~/.sopdrop/config.json
Cache location: ~/.sopdrop/cache/
"""

import os
import json
from pathlib import Path

# Default configuration
DEFAULTS = {
    "server_url": "https://sopdrop.com",
    "api_version": "v1",
    "cache_enabled": True,
    "cache_max_size_mb": 500,
    # Library settings
    "active_library": "personal",  # "personal" or "team"
    "personal_library_path": None,  # Custom personal library path (None = ~/.sopdrop/library/)
    "team_library_path": None,  # Path to shared team library folder
    "team_slug": None,  # Slug of the team to sync from (e.g., "my-team")
    # UI settings
    "ui_scale": 1.0,  # UI scale factor (0.8 - 1.5)
}

def get_config_dir():
    """Get the sopdrop config directory."""
    return Path.home() / ".sopdrop"

def get_cache_dir():
    """Get the sopdrop cache directory."""
    return get_config_dir() / "cache"

def get_config_file():
    """Get the config file path."""
    return get_config_dir() / "config.json"

def get_token_file():
    """Get the token file path."""
    return get_config_dir() / "token"

def ensure_config_dir():
    """Ensure config directory exists."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return config_dir

def get_config():
    """Load configuration from file, with defaults."""
    config = DEFAULTS.copy()

    config_file = get_config_file()
    if config_file.exists():
        try:
            with open(config_file) as f:
                user_config = json.load(f)
                config.update(user_config)
        except (json.JSONDecodeError, IOError):
            pass

    # Also check environment variables (override file config)
    env_url = os.environ.get("SOPDROP_SERVER_URL")
    if env_url:
        config["server_url"] = env_url

    return config

def save_config(config):
    """Save configuration to file."""
    ensure_config_dir()
    config_file = get_config_file()

    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)

def set_server_url(url):
    """Set the server URL."""
    config = get_config()
    config["server_url"] = url.rstrip("/")
    save_config(config)
    print(f"Server URL set to: {config['server_url']}")

def get_api_url():
    """Get the full API base URL."""
    config = get_config()
    return f"{config['server_url']}/api/{config['api_version']}"

def get_token():
    """Get stored API token."""
    token_file = get_token_file()
    if token_file.exists():
        try:
            return token_file.read_text().strip()
        except IOError:
            pass
    return None

def save_token(token):
    """Save API token."""
    ensure_config_dir()
    token_file = get_token_file()
    token_file.write_text(token)
    # Secure permissions (readable only by owner)
    token_file.chmod(0o600)

def clear_token():
    """Clear stored token."""
    token_file = get_token_file()
    if token_file.exists():
        token_file.unlink()


# Clipboard functions for quick paste workflow

def get_clipboard_file():
    """Get the clipboard file path."""
    return get_config_dir() / "clipboard.json"


def get_clipboard():
    """Get clipboard contents (slug and package data)."""
    clipboard_file = get_clipboard_file()
    if clipboard_file.exists():
        try:
            return json.loads(clipboard_file.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return None


def set_clipboard(slug, package):
    """Set clipboard contents for quick paste."""
    ensure_config_dir()
    clipboard_file = get_clipboard_file()
    clipboard_file.write_text(json.dumps({
        "slug": slug,
        "package": package,
    }, separators=(',', ':')))  # Compact JSON


def clear_clipboard():
    """Clear the clipboard."""
    clipboard_file = get_clipboard_file()
    if clipboard_file.exists():
        clipboard_file.unlink()


# ==============================================================================
# Library Settings
# ==============================================================================

def get_active_library():
    """Get the currently active library type ('personal' or 'team')."""
    config = get_config()
    return config.get("active_library", "personal")


def set_active_library(library_type):
    """Set the active library type ('personal' or 'team')."""
    if library_type not in ("personal", "team"):
        raise ValueError("library_type must be 'personal' or 'team'")
    config = get_config()
    config["active_library"] = library_type
    save_config(config)


def get_team_library_path():
    """Get the team library path, or None if not set."""
    config = get_config()
    path = config.get("team_library_path")
    if path:
        return Path(path)
    return None


def set_team_library_path(path):
    """Set the team library path. Pass None to clear."""
    config = get_config()
    if path is None:
        config["team_library_path"] = None
    else:
        # Validate path exists or can be created
        path = Path(path)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        config["team_library_path"] = str(path.resolve())
    save_config(config)


def get_personal_library_path():
    """Get the personal library path. Returns custom path or default ~/.sopdrop/library/."""
    config = get_config()
    custom = config.get("personal_library_path")
    if custom:
        return Path(custom)
    return get_config_dir() / "library"


def set_personal_library_path(path):
    """Set custom personal library path. Pass None to reset to default."""
    config = get_config()
    if path is None:
        config["personal_library_path"] = None
    else:
        path = Path(path)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        config["personal_library_path"] = str(path.resolve())
    save_config(config)


def get_library_path():
    """Get the path to the currently active library."""
    active = get_active_library()
    if active == "team":
        team_path = get_team_library_path()
        if team_path and team_path.exists():
            return team_path / "library"
    # Personal library (custom or default)
    return get_personal_library_path()


def list_available_libraries():
    """List available libraries with their status."""
    libraries = []

    # Personal library (always available)
    personal_path = get_personal_library_path()
    libraries.append({
        "type": "personal",
        "name": "Personal Library",
        "path": str(personal_path),
        "exists": personal_path.exists(),
        "active": get_active_library() == "personal",
    })

    # Team library (if configured)
    team_path = get_team_library_path()
    if team_path:
        lib_path = team_path / "library"
        libraries.append({
            "type": "team",
            "name": "Team Library",
            "path": str(lib_path),
            "exists": lib_path.exists(),
            "active": get_active_library() == "team",
        })

    return libraries


# ==============================================================================
# Team Settings
# ==============================================================================

def get_team_slug():
    """Get the configured team slug for syncing."""
    config = get_config()
    return config.get("team_slug")


def set_team_slug(slug):
    """Set the team slug for syncing. Pass None to clear."""
    config = get_config()
    # Normalize to lowercase to match server-side slug format
    config["team_slug"] = slug.lower() if slug else None
    save_config(config)


def get_team_name():
    """Get the configured team name for display."""
    config = get_config()
    return config.get("team_name")


def set_team_name(name):
    """Set the team name for display. Pass None to clear."""
    config = get_config()
    config["team_name"] = name
    save_config(config)


def get_team_info():
    """Get team configuration info for display."""
    config = get_config()
    slug = config.get("team_slug")
    name = config.get("team_name")
    path = config.get("team_library_path")

    if not slug and not path:
        return None

    return {
        "slug": slug,
        "name": name or slug or "Team Library",
        "path": path,
    }


# ==============================================================================
# UI Scale
# ==============================================================================

def get_ui_scale():
    """Get the UI scale factor (clamped 0.8-1.5)."""
    config = get_config()
    scale = config.get("ui_scale", 1.0)
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1.0
    return max(0.8, min(1.5, scale))


def set_ui_scale(scale):
    """Set the UI scale factor (clamped 0.8-1.5)."""
    scale = max(0.8, min(1.5, float(scale)))
    config = get_config()
    config["ui_scale"] = round(scale, 2)
    save_config(config)


# ==============================================================================
# UI State Persistence
# ==============================================================================

def get_ui_state_file():
    """Get the UI state file path."""
    return get_config_dir() / "ui_state.json"


def get_ui_state():
    """Get persisted UI state."""
    state_file = get_ui_state_file()
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_ui_state(state):
    """Save UI state to file."""
    ensure_config_dir()
    state_file = get_ui_state_file()
    state_file.write_text(json.dumps(state, indent=2))


def get_library_ui_state():
    """Get library panel UI state (search, filters, etc.)."""
    state = get_ui_state()
    return state.get("library_panel", {
        "search_query": "",
        "context_filter": None,
        "tag_filters": [],
        "sort_by": "updated",
        "view_mode": "grid",
        "collection_id": None,
        "group_by_collection": False,
    })


_NOT_SET = object()  # Sentinel for "parameter not passed"


def save_library_ui_state(
    search_query=_NOT_SET,
    context_filter=_NOT_SET,
    tag_filters=_NOT_SET,
    sort_by=_NOT_SET,
    view_mode=_NOT_SET,
    collection_id=_NOT_SET,
    group_by_collection=_NOT_SET,
    show_subcontent=_NOT_SET,
):
    """Save library panel UI state. Only updates provided values."""
    state = get_ui_state()
    library_state = state.get("library_panel", {})

    if search_query is not _NOT_SET:
        library_state["search_query"] = search_query
    if context_filter is not _NOT_SET:
        library_state["context_filter"] = context_filter
    if tag_filters is not _NOT_SET:
        library_state["tag_filters"] = tag_filters
    if sort_by is not _NOT_SET:
        library_state["sort_by"] = sort_by
    if view_mode is not _NOT_SET:
        library_state["view_mode"] = view_mode
    if collection_id is not _NOT_SET:
        library_state["collection_id"] = collection_id
    if group_by_collection is not _NOT_SET:
        library_state["group_by_collection"] = group_by_collection
    if show_subcontent is not _NOT_SET:
        library_state["show_subcontent"] = show_subcontent

    state["library_panel"] = library_state
    save_ui_state(state)
