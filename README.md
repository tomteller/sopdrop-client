# Sopdrop

Houdini asset registry client. Save, share, and install procedural nodes and HDAs.

## Installation

```bash
pip install sopdrop
```

## Quick Start

### In Houdini's Python Shell

```python
import sopdrop

# Authenticate (one-time setup)
sopdrop.login()

# Search for assets
sopdrop.search("scatter")

# Get asset info
sopdrop.info("username/scatter-points")

# Paste into current network
sopdrop.paste("username/scatter-points")

# Install a specific version
sopdrop.install("username/scatter-points@1.2.0")
```

### Command Line

```bash
# Authenticate
sopdrop login

# Search
sopdrop search "scatter" --context sop

# Get info
sopdrop info username/scatter-points

# Download to cache
sopdrop install username/scatter-points

# Preview contents
sopdrop preview username/scatter-points

# View code (v1 packages)
sopdrop code username/scatter-points

# Cache management
sopdrop cache
sopdrop cache clear

# Configuration
sopdrop config
sopdrop config server https://sopdrop.com
```

## Features

- **Search & Browse** - Find assets by keyword, context, or tags
- **One-Click Paste** - Paste nodes directly into your Houdini network
- **Version Management** - Install specific versions or get the latest
- **HDA Support** - Install Houdini Digital Assets
- **Local Caching** - Downloaded assets are cached for offline use
- **Security Warnings** - Review assets before executing code

## Configuration

Config files are stored in `~/.sopdrop/`:

- `config.json` - Server URL and settings
- `token` - API authentication token
- `cache/` - Downloaded asset cache

### Environment Variables

- `SOPDROP_SERVER_URL` - Override the server URL

## Security

Sopdrop distributes executable code. When pasting assets:

1. You'll see a security prompt with publisher info and warnings
2. You can preview the code before pasting with `sopdrop.preview()`
3. Use `trust=True` to skip warnings for trusted publishers

HDAs receive an additional warning since they can execute arbitrary Python via callbacks.

## License

MIT
