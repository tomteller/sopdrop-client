# Sopdrop Houdini Integration

Shelf tools for integrating Sopdrop with Houdini.

## Installation

### 1. Install the Python Client

```bash
# Using pip
pip install sopdrop

# Or from source
cd packages/sopdrop-client
pip install -e .
```

### 2. Configure Houdini

Add the following to your `houdini.env` file:

**Location of houdini.env:**
- **Windows:** `C:\Users\<username>\Documents\houdini20.5\houdini.env`
- **macOS:** `~/Library/Preferences/houdini/20.5/houdini.env`
- **Linux:** `~/houdini20.5/houdini.env`

**Add these lines:**

```bash
# Sopdrop Integration
SOPDROP_HOUDINI_PATH = "/path/to/sopdrop/packages/sopdrop-houdini"
HOUDINI_TOOLBAR_PATH = "$SOPDROP_HOUDINI_PATH/toolbar;&"
HOUDINI_PYTHON_PANEL_PATH = "$SOPDROP_HOUDINI_PATH/python_panels;&"
PYTHONPATH = "$SOPDROP_HOUDINI_PATH/scripts;$PYTHONPATH"

# Optional: Set default server (defaults to https://sopdrop.com)
# SOPDROP_SERVER_URL = "https://sopdrop.com"
```

Replace `/path/to/sopdrop` with the actual path to your Sopdrop installation.

### 3. Restart Houdini

After updating `houdini.env`, restart Houdini. You should see a new "Sopdrop" shelf tab.

## Shelf Tools

### Publish
Publish selected nodes to the Sopdrop registry.

1. Select nodes, network boxes, and/or sticky notes
2. Click the Publish tool
3. Enter asset name, description, and tags
4. Choose a license
5. Click Publish

### Paste
Paste nodes from Sopdrop into your current network.

1. Click the Paste tool
2. Select from recent assets, or enter an asset slug
3. Review the asset details
4. Click Paste

### Search
Search the Sopdrop registry.

1. Click the Search tool
2. Enter a search term
3. Filter by context if needed
4. Browse results and paste

### Settings
Manage your Sopdrop account and settings.

- Log in / Log out
- Generate API tokens
- Change server URL
- View and clear cache

## Usage from Python Shell

You can also use Sopdrop directly from Houdini's Python shell:

```python
import sopdrop

# Log in (one-time)
sopdrop.login()

# Search
results = sopdrop.search("scatter", context="sop")

# Paste an asset
sopdrop.paste("username/scatter-points")

# Publish selected nodes
sopdrop.publish(hou.selectedNodes())

# Preview before pasting
sopdrop.show_code("username/scatter-points")
sopdrop.show_info("username/scatter-points")
```

## Troubleshooting

### "Sopdrop client not installed"
Make sure you've installed the Python client:
```bash
pip install sopdrop
```

And ensure Houdini is using the correct Python environment.

### "SOPDROP_HOUDINI_PATH not set"
Add the environment variable to your `houdini.env` file and restart Houdini.

### Shelf not appearing
1. Check that `HOUDINI_TOOLBAR_PATH` includes the Sopdrop toolbar directory
2. Restart Houdini
3. Right-click on the shelf bar → "Shelves..." → Check "Sopdrop"

### Login issues
1. Make sure you can access the Sopdrop website in your browser
2. Check your firewall settings
3. Try generating a new token from the website

## File Structure

```
sopdrop-houdini/
├── toolbar/
│   └── sopdrop.shelf      # Shelf definition
├── python_panels/
│   └── sopdrop_browser.pypanel  # Embedded browser panel
├── scripts/
│   ├── sopdrop_browser.py # Browser panel implementation
│   ├── sopdrop_publish.py # Publish tool
│   ├── sopdrop_paste.py   # Paste tool
│   ├── sopdrop_search.py  # Search tool
│   └── sopdrop_settings.py # Settings tool
├── icons/                  # Custom icons (optional)
└── README.md              # This file
```

## Embedded Browser Panel

Sopdrop includes an embedded browser panel that shows the full Sopdrop website inside Houdini.

**To open as a pane tab:**
1. Go to Windows → Python Panel → Sopdrop Browser
2. Or split a pane and choose "New Pane Tab Type" → Python Panel → Sopdrop Browser

**Features:**
- Browse and search all assets
- Paste directly into your current network with one click
- Context-aware filtering shows compatible assets
- No need to leave Houdini

## Network Configuration

For studio deployments, you can configure all workstations to use a local Sopdrop server:

```bash
# In houdini.env
SOPDROP_SERVER_URL = "http://your-server:4848"
```

Or use the Settings tool to change the server URL per-user.
