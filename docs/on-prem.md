# On-Prem Server Deployment

The on-prem path runs your own `sopdrop-server` on a Linux box on your LAN, with all artists' Houdini panels pointing at it over HTTP. It exists to fix the unrecoverable lock contention that hits a NAS-shared SQLite team library once you have more than 2–3 concurrent writers.

This doc is the canonical reference for everything on-prem. For the broader library system, see [`library.md`](library.md). For the panel UI, see [`houdini-panel.md`](houdini-panel.md).

## When to use this

- **Use on-prem** if you have more than 2–3 artists publishing to a shared library. Eliminates the NAS-locking class of bug entirely.
- **Use NAS** (legacy) only for very small teams or local single-user testing.
- **Use the hosted sopdrop.com** for community / open publishing. Not the team-library use case this doc covers.

## Architecture

```
LAN
 │
 ├─ <linux-box>:4800         ← docker compose up
 │  ├─ sopdrop-server (Node.js + Express)
 │  ├─ Postgres 16 (named volume: pgdata)
 │  ├─ Asset files (named volume: assets, served at /library/*)
 │  └─ Nightly pg_dump backup sidecar (./backups/, 14-day retention)
 │
 └─ Workstations (12+)
    └─ Houdini → Sopdrop panel → HTTPS to LAN box
       ├─ Personal library → still local SQLite at ~/.sopdrop/library/
       └─ Team library    → HTTP to <linux-box>:4800/api/v1/teams/<slug>/library
                            (no NAS share needed)
```

The Postgres server replaces the SQLite-on-NAS pattern. There's no shared file anymore — every workstation makes HTTP requests, the server handles concurrency natively.

## Auth modes

Two ways the server can identify the caller. Choose at deploy time via the `TRUST_LAN_AUTH` env var:

### Trust-LAN (recommended for trusted internal LAN)

`TRUST_LAN_AUTH=true`. No tokens, no Login dance. Identity comes from the workstation OS username via the `X-Sopdrop-User` request header, sent automatically by the panel when **Local-only mode** + **HTTP team mode** are both on.

- Server auto-creates the user record on first sight (sanitized username, synthesized `<name>@lan.local` email, no password)
- All per-user features still work: favorites, recents, attribution, audit log
- Reserved usernames (`admin`, `root`, etc.) refused
- Sanitization is safe against impersonation: `alice<script>` becomes a separate user `alicescript`, not silently merged with `alice`

**Tradeoff:** anyone on your LAN can claim any unused username by setting their own `X-Sopdrop-User` header. In a 12-person trusted studio that's a non-issue. **Never enable on a server reachable from the public internet.**

### Token-based (default for sopdrop.com; opt-in for paranoid on-prem)

`TRUST_LAN_AUTH=false` (the default). Standard OAuth-style flow:
- Artist clicks **Login** in the panel → browser opens to `<server>/auth/cli` → enters credentials → copies token → pastes back into Houdini
- Token stored at `~/.sopdrop/token` with mode 0600
- Token sent as `Authorization: Bearer sdrop_<hex>` on every request

Use this if your LAN has guests / contractors / VPN users you don't fully trust. Otherwise trust-LAN is much less friction.

## Server schema additions

Both modes work atop the existing public-registry schema. Two columns were added to support team-owned assets:

```sql
-- assets table
ALTER TABLE assets ADD COLUMN team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX idx_assets_team ON assets(team_id) WHERE team_id IS NOT NULL;

-- user_folders table (collections sidebar)
ALTER TABLE user_folders ADD COLUMN team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX idx_user_folders_team ON user_folders(team_id) WHERE team_id IS NOT NULL;
```

`team_id IS NULL` → existing per-user / public registry behavior (unchanged).
`team_id IS NOT NULL` → asset is team-scoped, only visible to team members via the `/teams/:slug/library/*` routes.

## Server endpoints (team-scoped)

All under `/api/v1/teams/:slug/library/*`. Defined in `packages/sopdrop-server/src/routes/teamLibrary.js`. Every route requires team membership (or `is_admin`). Non-members get **404** (not 403) so team existence isn't leaked.

### Reads

| Endpoint | Returns |
|---|---|
| `GET .../library` | Paginated browse with `q`, `context`, `type`, `tags`, `sort`, `since`, `limit`, `offset` query params. Strong ETag → revalidates with 304. |
| `GET .../library/collections` | Folder/collection sidebar (flat list). Strong ETag. |
| `GET .../library/tags` | Tag counts within the team library. Strong ETag. |
| `GET .../library/stats` | `{ assetCount, collectionCount, totalSizeBytes, totalSizeMb, lastUpdated }` |
| `GET .../library/assets/:assetId` | Single asset by UUID. |
| `GET .../library/trash` | Soft-deleted team assets (sorted newest first). |

### Writes

| Endpoint | Notes |
|---|---|
| `POST .../library/assets/:assetId/use` | Bump `download_count`. Best-effort. |
| `POST .../library/collections` | Create folder. Conflict on duplicate slug. |
| `PUT  .../library/collections/:folderId` | Rename / recolor / reposition. |
| `DELETE .../library/collections/:folderId` | Delete folder. |
| `POST .../library/assets/:assetId/restore` | Undelete a trashed asset. **Any team member.** |
| `DELETE .../library/assets/:assetId/purge` | Permanently delete (file + thumbnail removed from disk). **Team admin/owner only.** |

Asset CRUD itself (publish, edit, soft-delete, thumbnail) reuses the existing `/api/v1/assets/*` routes. Those were extended to recognize team-owned assets via the `canReadAsset` / `canWriteAsset` helpers in `middleware/teamAccess.js` — public-registry behavior is unchanged.

The upload route accepts an optional `teamSlug` body param. When set, the new asset's `team_id` is populated and visibility defaults to private (only team members can read).

## Caching headers

- **JSON list responses**: `Cache-Control: private, must-revalidate, max-age=0` + strong `ETag`. Repeat fetches with `If-None-Match` return 304 — no body, no SQL, no JSON parse on the server.
- **`/library/*` static asset/thumbnail responses**: `Cache-Control: public, max-age=31536000, immutable`. Files are content-addressed (UUID/hash filenames), never mutate after write. Warm clients make zero network calls for thumbnails.

## Client architecture

`packages/sopdrop-client/sopdrop/`:

| Module | Purpose |
|---|---|
| `library.py` | Public API. Every team-library function dispatches to `_team_http.py` when `team_library_mode == "http"`. The SQLite implementation lives in the same functions for the NAS path. |
| `_team_http.py` | HTTP shim: converts server JSON to the SQLite-row dict shape the panel expects. ETag cache. Invalidation on writes. |
| `http_library.py` | `HttpLibraryClient` for read methods, plus `upload_asset` multipart helper, `update_asset_meta`, `delete_asset_remote`. Typed errors: `OfflineError`, `AuthError`, `ForbiddenError`, `NotFoundError`, `ServerError`. `_auth_headers()` decides whether to send Bearer token, `X-Sopdrop-User`, or both. |
| `thumbnail_cache.py` | Disk LRU at `~/.sopdrop/cache/thumbnails/`, content-addressed by sha256(URL). 500 MB cap. No Qt imports. |
| `config.py` | Stores `team_library_mode`, `team_slug`, `local_only`, etc. `use_lan_trust_auth()` returns true iff trust-LAN should be active. `get_workstation_user()` for the X-Sopdrop-User header. |

Shape contract: every dict returned from `_team_http.py` matches the SQLite row shape exactly, plus two HTTP-only keys:
- `_thumbnail_url` — absolute URL the thumbnail dispatcher fetches from
- `_download_url` — absolute URL for asset download

The panel widget tree was not modified for the data swap; only `AssetCardWidget._load_thumbnail` was extended to recognize `_thumbnail_url` and route through the async loader.

## Recovery (trash → restore → purge)

Same UX as the SQLite path. Implemented for HTTP mode in PR landing the trash flow.

| Action | Who | What happens |
|---|---|---|
| Right-click → Delete | Any team member | Server sets `is_deprecated = true`. **File stays on disk.** Asset disappears from active list. |
| Sidebar → Trash | Any team member | `GET /trash` returns deleted-but-not-purged assets, newest first. |
| Right-click trashed → Restore | Any team member | `POST /restore` clears `is_deprecated`. Cache invalidated. |
| Right-click trashed → Purge | **Team owner / admin only** | `DELETE /purge` removes the row, best-effort cleans up file + thumbnail. Members get 403. |

## Settings dialog UI

Two radio buttons in the **TEAM LIBRARY** section of the Settings dialog:

- **Shared folder (NAS / SMB)** — legacy NAS path. Shows `Team Library Path:` field + Browse button.
- **On-prem Sopdrop server** — HTTP mode. Hides the path field, populates the Team dropdown via Fetch Teams.

Visibility rules computed by `_apply_settings_visibility()`:
- **SERVER section** (Server URL field) — visible whenever any cloud OR on-prem-server interaction is possible. Hidden only in pure local-only NAS mode.
- **ACCOUNT section** (Login button) — hidden in the **trust-LAN scenario** (Local-only ON + HTTP team mode). Otherwise follows the local-only rule.
- **Fetch Teams button** — visible whenever SERVER is visible.

Status line shows different messages depending on mode:
- NAS: counts assets via local SQLite (existing behavior)
- HTTP, token mode: `Connected to '<slug>' on <url> — N asset(s).`
- HTTP, trust-LAN: `Connected as <ws-user> to '<slug>' on <url> — N asset(s).`

The "Team Slug" field is **not** exposed in the UI. Slug is an internal identifier; users pick their team from the **Fetch Teams** dropdown which queries `GET /api/v1/teams` and shows team names.

## Config keys

In `~/.sopdrop/config.json`:

```json
{
  "server_url": "http://sopdrop.lan:4800",
  "active_library": "team",
  "team_library_mode": "http",
  "team_slug": "f48",
  "local_only": true
}
```

| Key | Default | Effect |
|---|---|---|
| `server_url` | `https://sopdrop.com` | Where API requests go. |
| `active_library` | `personal` | `personal` (local SQLite) or `team`. |
| `team_library_mode` | `nas` | `nas` (legacy shared SQLite) or `http` (on-prem server). |
| `team_slug` | `null` | Team identifier on the server. Populated by Fetch Teams or set by code. |
| `local_only` | `false` | Hides cloud-branded UI. Combined with `team_library_mode=http`, activates trust-LAN auth on the client. |

## Migration from NAS

`scripts/migrate-nas-to-server.py` reads the old NAS `library.db` read-only and uploads each active asset via `POST /api/v1/assets/upload`. Idempotent (skips assets the authenticated user already has by slug). See the script header for usage.

## Files of interest

```
packages/sopdrop-server/
├── src/
│   ├── middleware/
│   │   ├── auth.js              ← TRUST_LAN_AUTH + sanitizeLanUsername + findOrCreateLanUser
│   │   └── teamAccess.js        ← canReadAsset / canWriteAsset / resolveTeamIdBySlug
│   ├── routes/
│   │   ├── assets.js            ← extended for team-aware writes; teamSlug body param on upload
│   │   └── teamLibrary.js       ← new file: all /teams/:slug/library/* routes
│   └── models/schema.sql        ← team_id columns + indexes appended at end

packages/sopdrop-client/sopdrop/
├── _team_http.py                ← dispatch shim + shape converter
├── http_library.py              ← HttpLibraryClient + upload helpers + auth headers
├── thumbnail_cache.py           ← disk LRU
├── config.py                    ← team_library_mode + workstation user helpers
└── library.py                   ← every team-library function dispatches via _http_mode()

packages/sopdrop-houdini/scripts/sopdrop_library_panel.py
├── _HttpThumbnailDispatcher     ← QThreadPool-based thumbnail loader
├── _HttpThumbnailRunnable       ← worker that fetches via thumbnail_cache
└── SettingsDialog               ← mode toggle + trust-LAN-aware visibility

deploy/onprem/
├── docker-compose.yml           ← Postgres + sopdrop-server + nightly pg_dump
├── .env.example                 ← TRUST_LAN_AUTH defaults to true here
└── README.md                    ← step-by-step deployment guide

scripts/migrate-nas-to-server.py ← NAS → on-prem migration tool
```

## Operations

### Backups

Compose stack runs nightly `pg_dump` into `deploy/onprem/backups/` (last 14 retained). Asset files live in the `assets` Docker volume — not auto-backed-up; rsync to off-box storage. See `deploy/onprem/README.md` for the recipe.

### Upgrading

```sh
cd deploy/onprem
git pull
docker compose build sopdrop-server
docker compose up -d sopdrop-server
```

`initDB()` is idempotent and runs schema migrations on every boot.

### Decommissioning the old NAS share

After every workstation has been on `team_library_mode=http` for ~30 days without issues:

1. Verify: `cat ~/.sopdrop/config.json | grep team_library_mode` on each → should be `"http"`
2. Archive the NAS `library/` somewhere cold for another 30 days
3. Stop pointing anyone's `team_library_path` at the NAS

The legacy NAS code path stays in `library.py` and the panel — switching back is just a config flip.
