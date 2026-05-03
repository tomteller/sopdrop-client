#!/usr/bin/env bash
# Create a team on a fresh on-prem sopdrop-server.
#
# Usage:
#   ./create-team.sh <team-name> [owner-username]
#
# Environment:
#   PUBLIC_URL   Base URL of the API (default: http://localhost:4800)
#
# Requires the server to have TRUST_LAN_AUTH=true (the on-prem default),
# since this script authenticates via the X-Sopdrop-User header rather
# than going through the browser login flow.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $(basename "$0") <team-name> [owner-username]" >&2
    exit 2
fi

team_name="$1"
owner="${2:-admin}"
api_url="${PUBLIC_URL:-http://localhost:4800}"
api_url="${api_url%/}"

if ! command -v curl >/dev/null 2>&1; then
    echo "error: curl is required" >&2
    exit 1
fi

# Body: only `name` is required — the server slugifies it.
body=$(printf '{"name":%s}' "$(printf '%s' "$team_name" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")

echo "Creating team '$team_name' (owner=$owner) on $api_url ..."
response=$(curl -sS -w '\n%{http_code}' -X POST "$api_url/api/v1/teams" \
    -H "Content-Type: application/json" \
    -H "X-Sopdrop-User: $owner" \
    -d "$body")

http_code=$(printf '%s' "$response" | tail -n1)
body_out=$(printf '%s' "$response" | sed '$d')

case "$http_code" in
    20[01])
        echo "$body_out"
        echo "Team created. Use the 'slug' field above when configuring workstations."
        ;;
    *)
        echo "error: server returned HTTP $http_code" >&2
        echo "$body_out" >&2
        exit 1
        ;;
esac
