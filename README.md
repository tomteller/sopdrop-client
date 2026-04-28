# Sopdrop

Houdini asset registry client. Save, share, and install procedural nodes and HDAs.

## Installation

Sopdrop uses Houdini's built-in package system. No pip, no terminal, no Python environments.

### Option A: Git Clone (Recommended)

Cloning the repo makes updating easy — just `git pull` to get the latest version.

1. **Clone** the repo anywhere on your machine:

   ```bash
   git clone https://github.com/tomteller/sopdrop-client.git
   ```

2. **Copy** `sopdrop.json` from the cloned folder into your Houdini packages folder:

   ```
   # macOS
   ~/Library/Preferences/houdini/20.5/packages/

   # Windows
   %USERPROFILE%/Documents/houdini20.5/packages/

   # Linux
   ~/houdini20.5/packages/
   ```

   Create the `packages` folder if it doesn't exist.

3. **Edit** `sopdrop.json` and set the path to where you cloned the repo:

   ```json
   {
       "env": [
           { "SOPDROP": "/path/to/sopdrop-client" }
       ],
       "path": "$SOPDROP"
   }
   ```

4. **Restart Houdini.** The Sopdrop shelf and TAB menu entries appear automatically.

To update later, just run `git pull` in the cloned folder and restart Houdini.

### Option B: Download Release

If you don't have git or prefer a manual install:

1. **Download** the [latest release](https://github.com/tomteller/sopdrop-client/releases/latest) and extract it anywhere on your machine.

2. Follow steps 2–4 above, pointing the path to where you extracted the release.

To update, download the new release and replace the old folder.

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
- **Team Library** - Optional shared library for studios (see "Self-hosting")

## Self-hosting (team library)

For studios that want a private team registry on the LAN — no cloud
account, no NAS-shared SQLite, no per-artist Login dance — this repo
ships the server source and a turn-key Docker Compose stack:

- [`server/`](server) — the Node.js + Postgres server (same code that
  runs sopdrop.com).
- [`deploy/onprem/`](deploy/onprem) — `docker-compose.yml`,
  `.env.example`, and a setup README. One Linux box on the LAN runs
  Postgres + the API server. All workstations point at it via HTTP — no
  shared file locking, no auth tokens to distribute.
- [`scripts/migrate-nas-to-server.py`](scripts) — one-shot migration
  tool if you currently have a shared-SQLite team library and want to
  move everything onto a self-hosted server.
- [`docs/on-prem.md`](docs/on-prem.md) — the canonical reference for
  architecture, auth modes, schema, and operations.

### Sixty-second setup

```bash
# On the server box (Linux, Docker installed):
cd deploy/onprem
cp .env.example .env
# fill in POSTGRES_PASSWORD, JWT_SECRET, PUBLIC_URL, CORS_ORIGINS
docker compose up -d --build
```

The compose stack defaults to **trust-LAN auth** (`TRUST_LAN_AUTH=true`)
which is the recommended mode for an internal LAN. Artists don't need
tokens or a Login flow — identity comes from their workstation OS
username, sent automatically by the panel.

On every workstation (in Houdini → Sopdrop panel → Settings):

1. Tick **Local only mode** at the top.
2. Under **TEAM LIBRARY**, pick **On-prem Sopdrop server**.
3. In **SERVER**, set Server URL to your LAN address (e.g.
   `http://sopdrop.lan:4848`).
4. Click **Fetch Teams**, pick your team from the dropdown.
5. Status line shows: *Connected as alice to 'your-team' on
   http://sopdrop.lan:4848 — N asset(s).*
6. **Save Settings.**

That's it. No "Login" button, no API token, no slug typing. Personal
library stays local SQLite, untouched.

See [`deploy/onprem/README.md`](deploy/onprem/README.md) for the full
walkthrough including backups, upgrades, and the
`TRUST_LAN_AUTH=false` token-based flow if you'd rather have explicit
auth.

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
