#!/usr/bin/env python3
"""
Re-encode every thumbnail in a sopdrop team library to a smaller size.

The Houdini panel used to upload raw clipboard PNGs as thumbnails — full
resolution, no compression — so a 100-asset team library could carry
hundreds of MB of thumbnail data. The panel now compresses on upload
(JPG q85, max 1024 px), but pre-existing assets still hold their fat
originals. This script walks the library, downloads each thumbnail,
re-encodes if it looks oversized, and re-uploads via the existing
PATCH /api/v1/assets/<owner>/<slug>/thumbnail endpoint.

Usage:
    export SOPDROP_TOKEN=sdrop_xxxxxxxxxxxx...
    python3 scripts/optimize-thumbnails.py \\
        --server http://sopdrop.lan:4800 \\
        --team frame48

    # Dry run first:
    python3 scripts/optimize-thumbnails.py \\
        --server http://sopdrop.lan:4800 --team frame48 --dry-run

Tunables:
    --max-dim       Longest edge in pixels (default 1024).
    --quality       JPG quality 1-100 (default 85).
    --min-bytes     Skip thumbnails already smaller than this (default
                    150 KB — typical post-compression size).
    --keep-png      Don't convert PNG → JPG even when there's no alpha.
                    Useful if you specifically want lossless at any cost.

Requires Pillow:
    pip install Pillow
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from urllib.parse import quote

try:
    import requests
except ImportError:
    sys.exit("requests is required: pip install requests")

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--server", required=True,
                   help="On-prem server URL, e.g. http://sopdrop.lan:4800")
    p.add_argument("--team", required=True,
                   help="Team slug. Required so we operate on the right library.")
    p.add_argument("--token", default=os.environ.get("SOPDROP_TOKEN"),
                   help="API token (or SOPDROP_TOKEN env var).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report potential savings without modifying anything.")
    p.add_argument("--max-dim", type=int, default=1024,
                   help="Longest edge in pixels (default 1024)")
    p.add_argument("--quality", type=int, default=85,
                   help="JPG quality 1-100 (default 85)")
    p.add_argument("--min-bytes", type=int, default=150 * 1024,
                   help="Skip thumbnails already at or below this size "
                        "(default 150 KB)")
    p.add_argument("--keep-png", action="store_true",
                   help="Don't convert PNG -> JPG even when there's no alpha")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after N successful re-uploads (0 = no limit)")
    return p.parse_args()


def fetch_team_assets(server: str, token: str | None, team: str) -> list[dict]:
    """Page through GET /teams/:slug/library and return all assets."""
    out: list[dict] = []
    offset = 0
    limit = 100
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    while True:
        url = f"{server}/api/v1/teams/{quote(team, safe='')}/library"
        params = {"limit": limit, "offset": offset}
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code >= 400:
            sys.exit(f"list assets failed: HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        page = body.get("assets") or []
        out.extend(page)
        total = body.get("total", 0)
        offset += len(page)
        if not page or offset >= total:
            break
    return out


def _absolute(url: str | None, server: str) -> str | None:
    """Server returns relative /library/... paths when storage is local
    (no R2 configured). The panel handles this via its own _absolute_url
    helper; this script needs to too."""
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        return server.rstrip("/") + url
    return server.rstrip("/") + "/" + url


def download_thumbnail(url: str) -> bytes | None:
    """Fetch the current thumbnail bytes. None if missing/unreachable."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code != 200 or not r.content:
        return None
    return r.content


def recompress(raw: bytes, *, max_dim: int, quality: int,
               keep_png: bool) -> tuple[bytes, str] | None:
    """Return (new_bytes, mime) or None if we couldn't decode."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        print(f"  decode failed: {e}", file=sys.stderr)
        return None

    # Resize if either edge exceeds max_dim. Pillow's thumbnail() preserves
    # aspect ratio and only ever shrinks.
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)

    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )

    out = io.BytesIO()
    if has_alpha or keep_png:
        # Keep PNG; let Pillow's optimizer pass cut what it can.
        if img.mode not in ("RGBA", "LA", "P", "L"):
            img = img.convert("RGBA")
        img.save(out, format="PNG", optimize=True)
        return out.getvalue(), "image/png"
    # JPG: ensure no alpha channel sneaks through.
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue(), "image/jpeg"


def upload_thumbnail(server: str, token: str | None, owner: str,
                     asset_slug: str, payload: bytes, mime: str) -> tuple[bool, str]:
    """POST to /assets/:owner/:slug/thumbnail. Returns (ok, info)."""
    url = (
        f"{server}/api/v1/assets/{quote(owner, safe='')}/"
        f"{quote(asset_slug, safe='')}/thumbnail"
    )
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    ext = ".jpg" if mime == "image/jpeg" else ".png"
    files = {"thumbnail": (f"thumb{ext}", payload, mime)}
    try:
        r = requests.post(url, headers=headers, files=files, timeout=60)
    except requests.RequestException as e:
        return False, f"network: {e}"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    return True, "ok"


def main() -> int:
    args = parse_args()
    if not args.dry_run and not args.token:
        sys.exit("--token or SOPDROP_TOKEN env var required (unless --dry-run)")

    server = args.server.rstrip("/")
    print(f"Server:  {server}")
    print(f"Team:    {args.team}")
    print(f"Mode:    {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Target:  max {args.max_dim}px, JPG q={args.quality}, "
          f"skip <= {args.min_bytes // 1024}KB")
    print()

    assets = fetch_team_assets(server, args.token, args.team)
    print(f"Found {len(assets)} assets in team library")

    converted = skipped = no_thumb = errored = 0
    saved_bytes = 0
    examined_bytes = 0

    for i, asset in enumerate(assets, 1):
        slug = asset.get("slug")  # "owner/name"
        if not slug or "/" not in slug:
            continue
        owner, asset_slug = slug.split("/", 1)
        prefix = f"[{i}/{len(assets)}] {slug}"

        thumb_url = _absolute(asset.get("thumbnailUrl"), server)
        if not thumb_url:
            print(f"{prefix} — no thumbnail")
            no_thumb += 1
            continue

        raw = download_thumbnail(thumb_url)
        if raw is None:
            print(f"{prefix} — thumbnail download failed", file=sys.stderr)
            errored += 1
            continue

        before = len(raw)
        examined_bytes += before
        if before <= args.min_bytes:
            print(f"{prefix} — already small ({before // 1024}KB), skip")
            skipped += 1
            continue

        result = recompress(raw, max_dim=args.max_dim, quality=args.quality,
                            keep_png=args.keep_png)
        if result is None:
            errored += 1
            continue
        payload, mime = result
        after = len(payload)
        if after >= before:
            # Re-encoding didn't help (already optimal, or oddball image).
            print(f"{prefix} — recompress didn't help "
                  f"({before // 1024}KB → {after // 1024}KB), skip")
            skipped += 1
            continue

        delta = before - after
        ratio = after / before
        print(f"{prefix} — {before // 1024}KB → {after // 1024}KB "
              f"({ratio:.0%}, save {delta // 1024}KB)")

        if args.dry_run:
            converted += 1
            saved_bytes += delta
            continue

        ok, info = upload_thumbnail(server, args.token, owner, asset_slug,
                                    payload, mime)
        if not ok:
            print(f"  upload failed: {info}", file=sys.stderr)
            errored += 1
            continue
        converted += 1
        saved_bytes += delta

        if args.limit and converted >= args.limit:
            print(f"\n--limit {args.limit} reached, stopping")
            break

    print()
    print(f"Summary: {converted} re-encoded, {skipped} skipped, "
          f"{no_thumb} no thumbnail, {errored} errored")
    if examined_bytes:
        print(f"Total examined: {examined_bytes / (1024 * 1024):.1f} MB; "
              f"saved {saved_bytes / (1024 * 1024):.1f} MB "
              f"({saved_bytes / examined_bytes:.0%})")
    return 0 if errored == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
