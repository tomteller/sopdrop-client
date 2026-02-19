# Sopdrop Houdini Integration

Shelf tools for integrating Sopdrop with Houdini.

## Installation

### Method A: Houdini Packages (Recommended)

1. Copy the `sopdrop.json` file to your Houdini packages directory:
   - **Windows:** `C:\Users\<username>\Documents\houdini20.5\packages\`
   - **macOS:** `~/Library/Preferences/houdini/20.5/packages/`
   - **Linux:** `~/houdini20.5/packages/`

2. Edit `sopdrop.json` and change the `SOPDROP` path to point to this `houdini-integration` folder:
   ```json
   {
       "env": [
           { "SOPDROP": "F:/path/to/sopdrop-client/houdini-integration" },
           { "SOPDROP_HOUDINI_PATH": "$SOPDROP" }
       ]
   }
   ```

3. Restart Houdini.

### Method B: houdini.env

Add the following to your `houdini.env` file:

**Location of houdini.env:**
- **Windows:** `C:\Users\<username>\Documents\houdini20.5\houdini.env`
- **macOS:** `~/Library/Preferences/houdini/20.5/houdini.env`
- **Linux:** `~/houdini20.5/houdini.env`

**Add these lines:**

```bash
# Sopdrop Integration
SOPDROP_HOUDINI_PATH = "/path/to/sopdrop-client/houdini-integration"
HOUDINI_TOOLBAR_PATH = "$SOPDROP_HOUDINI_PATH/toolbar;&"
HOUDINI_PYTHON_PANEL_PATH = "$SOPDROP_HOUDINI_PATH/python_panels;&"
PYTHONPATH = "$SOPDROP_HOUDINI_PATH/scripts;$PYTHONPATH"
```

Replace the path with the actual path to the `houdini-integration` folder.

### Method C: Automatic Installer

```bash
python install.py
```

This auto-detects your Houdini preferences and configures `houdini.env`.

### Install the Python Client

The sopdrop Python client must be importable by Houdini's Python:

```bash
# Using Houdini's Python
/path/to/houdini/python/bin/pip install sopdrop

# Or from source
cd sopdrop-client
pip install -e .
```

If using Method A (packages), the `sopdrop.json` already adds the `client/` folder to PYTHONPATH, so the client is available without pip install as long as the folder structure is intact.

### Restart Houdini

After configuration, restart Houdini. You should see a new "Sopdrop" shelf tab.

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
houdini-integration/
├── sopdrop.json               # Houdini package config
├── install.py                 # Auto-installer for houdini.env
├── toolbar/
│   ├── sopdrop.shelf          # Main shelf (Library, Save, Settings, About)
│   ├── sopdrop_library.shelf  # TAB menu tools (auto-generated)
│   └── icons/                 # SVG icons for shelf tools
├── python_panels/
│   └── sopdrop_library.pypanel  # Library panel interface
├── scripts/
│   └── sopdrop_library_panel.py # Library panel implementation
└── README.md
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
