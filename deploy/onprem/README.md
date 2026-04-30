# Sopdrop on-prem deployment

Run your own Sopdrop server on the LAN so a team can share assets without the
SQLite-on-NAS locking failure mode. This is the recommended production setup
for teams of more than 2-3 artists.

## What this replaces

The NAS-shared `library.db` (a single SQLite file every workstation reads,
writes and locks) is replaced by a real client/server registry:

- **Postgres** for metadata (handles concurrent writers natively)
- **sopdrop-server** for the HTTP API the Houdini client already speaks
- **Local Docker volume** for the `.sopdrop` / `.hda` files

Each workstation talks HTTP to the server. No more shared file locks.

## Requirements

- A small Linux box on the LAN (4 GB RAM, 1 vCPU, 50 GB disk is plenty for
  ~12 artists). Can be a VM, a mini-PC, or a container on the existing TrueNAS.
- Docker + Docker Compose v2.
- A hostname the workstations can resolve (e.g. `sopdrop.lan` via internal
  DNS, or an `/etc/hosts` entry on each machine).

## First-time setup

```sh
cd deploy/onprem
cp .env.example .env
# Fill in POSTGRES_PASSWORD, JWT_SECRET, PUBLIC_URL, CORS_ORIGINS
$EDITOR .env

docker compose up -d --build
docker compose logs -f sopdrop-server   # watch for "Sopdrop Server" banner
curl http://localhost:4800/api/health   # → {"status":"healthy",...}
```

The schema is created automatically on first boot (`initDB()` in
`packages/sopdrop-server/src/models/db.js`).

## Creating accounts

With `SKIP_EMAIL_VERIFICATION=true` and `BETA_MODE=false` (the defaults in
`.env.example`), each artist can register directly:

```python
import sopdrop
sopdrop.set_server_url("http://sopdrop.lan:4800")
sopdrop.login()   # opens browser → register → token saved to ~/.sopdrop/config.json
```

For invite-only access, set `BETA_MODE=true` and generate invite codes via
`POST /api/v1/invites` from an admin account.

## Pointing the Houdini client at the on-prem server

The recommended on-prem flow is **trust-LAN** — artists don't need a
token, identity comes from the workstation OS username. With
`TRUST_LAN_AUTH=true` set on the server (the default in this compose
stack), each workstation just configures the server URL and team:

1. In Houdini → open the Sopdrop panel → **Settings** (gear icon).
2. Tick **Local only mode** at the top.
3. Under **TEAM LIBRARY**, pick **On-prem Sopdrop server**.
4. In **SERVER**, set Server URL to `http://sopdrop.lan:4800` (or your IP).
5. Click **Fetch Teams** → pick your team from the dropdown.
6. Status line shows: `Connected as <your-os-username> to '<team>' on
   http://sopdrop.lan:4800 — N asset(s).`
7. Click **Save Settings.**

No "Login" button, no API token, no browser dance. The panel sends the
workstation OS username (`X-Sopdrop-User: alice`) on every request; the
server auto-creates the user record on first sight.

Or set the same thing programmatically:

```python
import sopdrop
import sopdrop.config as cfg
sopdrop.set_server_url("http://sopdrop.lan:4800")
cfg.set_team_library_mode("http")
cfg.set_team_slug("your-team")
cfg.set_active_library("team")
# In a config:
# { "local_only": true } so the trust-LAN auth path activates
```

### When to use tokens instead

If you'd rather have explicit auth (e.g. you don't fully trust everyone
on your LAN, or you have contractors on VPN), set `TRUST_LAN_AUTH=false`
on the server. Artists then go through the standard Login flow (browser →
paste token). All other behavior is the same.

Personal library (`active_library: personal`) stays local SQLite,
untouched in either mode.

## Migrating the existing NAS library

Use `scripts/migrate-nas-to-server.py` from a workstation that can read both
the NAS share and reach the on-prem server. See the script header for usage;
short version:

```sh
# 1. Get an API token from the on-prem server (Settings → API tokens)
export SOPDROP_TOKEN=sdrop_...

# 2. Dry run — lists what would be uploaded
python3 scripts/migrate-nas-to-server.py \
    --nas /Volumes/team/library \
    --server http://sopdrop.lan:4800 \
    --dry-run

# 3. Real run
python3 scripts/migrate-nas-to-server.py \
    --nas /Volumes/team/library \
    --server http://sopdrop.lan:4800 \
    --visibility unlisted
```

The script is idempotent — re-running skips assets already on the server (by
matching `name + owner`). Safe to interrupt and resume.

## Backups

The `backup` service runs `pg_dump` once every 24 h into `./backups/`,
keeping the last 14 dumps. Asset files live in the `assets` Docker volume —
back that up separately:

```sh
# DB
ls deploy/onprem/backups/

# Files (run on the server host)
docker run --rm -v sopdrop_assets:/src -v "$PWD/backups:/dst" alpine \
    tar czf "/dst/library-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" -C /src .
```

For real disaster recovery, rsync `deploy/onprem/backups/` and a snapshot of
the `assets` volume off-box (the existing TrueNAS is a perfectly good target).

## Upgrading

```sh
cd deploy/onprem
git pull
docker compose build sopdrop-server
docker compose up -d sopdrop-server
```

`initDB()` is idempotent and runs migrations on every boot, so schema
upgrades are automatic.

## Decommissioning the NAS library

Once migration is verified and every workstation is pointed at the on-prem
server:

1. Set `active_library` back to `personal` on every workstation, or remove
   `team_library_path` from `~/.sopdrop/config.json`.
2. Archive `<NAS>/library/` somewhere cold. Don't delete it for at least
   30 days in case you need to re-migrate.

## Troubleshooting

**Workstations get connection refused.** Check `BIND_HOST` (must be `0.0.0.0`,
not `127.0.0.1`) and the host firewall on the server box.

**"CORS_ORIGINS is required in production".** Set it in `.env` to the exact
URL artists hit, e.g. `http://sopdrop.lan:4800`. No trailing slash.

**Login redirects to localhost.** `WEB_URL` is wrong in `.env`. Set it to the
LAN URL.

**Schema init fails on first boot.** Check `docker compose logs postgres` —
usually a password mismatch between an old `pgdata` volume and a new
`POSTGRES_PASSWORD`. If this is a fresh deploy, `docker compose down -v` and
start over.
