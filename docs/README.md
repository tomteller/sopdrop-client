# Sopdrop Developer Documentation

Detailed implementation documentation for the Sopdrop codebase. These docs serve as ground truth for development sessions and are linked from [CLAUDE.md](../CLAUDE.md).

## Documents

| Document | Covers |
|----------|--------|
| [import-export.md](import-export.md) | Package formats (V1/V2/HDA/VEX/path/curves), export & import flows, container HDA handling, network box positioning, retry logic |
| [library.md](library.md) | Local SQLite library, schema, asset types & contexts, CRUD, versioning, team libraries (NAS + mirror), cloud sync, diagnostic logging |
| [houdini-panel.md](houdini-panel.md) | Qt panel architecture, startup & TAB menu system, asset detail views, crash-safety patterns, background loading, paste flow |
| [crash-safety.md](crash-safety.md) | All 15 crash/stability mitigations applied, mandatory patterns for future code |

## Key Files

```
packages/sopdrop-client/sopdrop/
  __init__.py       Public API (search, install, paste, share, publish)
  api.py            HTTP client for sopdrop-api.fly.dev
  config.py         ~/.sopdrop/config.json management
  export.py         V1/V2 export from Houdini nodes to .sopdrop packages
  importer.py       V1/V2 import from .sopdrop packages into Houdini
  library.py        Local SQLite asset library (offline, team, cloud sync)
  menu.py           Houdini TAB menu generation + paste actions
  curves.py         Curves asset metadata extraction

packages/sopdrop-houdini/
  scripts/sopdrop_library_panel.py   Qt-based library browser panel (~10k lines)
  scripts/sopdrop_paste.py           Paste-by-slug entry point (share codes, library slugs)
  scripts/python/pythonrc.py         Startup hook (TAB menu init, skip_team)
  toolbar/*.shelf                    Shelf tool definitions
```
