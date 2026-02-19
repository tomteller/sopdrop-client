"""
Sopdrop command-line interface.

Usage:
    sopdrop login           - Authenticate with Sopdrop
    sopdrop logout          - Clear stored credentials
    sopdrop search <query>  - Search for assets
    sopdrop info <slug>     - Get asset details
    sopdrop install <slug>  - Download asset to cache
    sopdrop cache           - Show cache status
    sopdrop cache clear     - Clear the cache
    sopdrop config          - Show configuration
    sopdrop config server <url> - Set server URL
"""

import sys
import argparse

from . import (
    __version__,
    _get_client,
    search,
    info,
    install,
    versions,
    cache_status,
    cache_clear,
    show_code,
    preview,
)
from .config import get_config, set_server_url


def cmd_login(args):
    """Authenticate with Sopdrop."""
    client = _get_client()
    client.login()


def cmd_logout(args):
    """Clear stored credentials."""
    client = _get_client()
    client.logout()


def cmd_search(args):
    """Search for assets."""
    results = search(args.query, context=args.context, tags=args.tags)

    if not results:
        print("No results found.")
        return

    print(f"Found {len(results)} asset(s):\n")
    for asset in results:
        slug = f"{asset.get('owner', {}).get('username', '?')}/{asset.get('slug', '?')}"
        desc = asset.get('description', '')[:60]
        context = asset.get('houdiniContext', '?').upper()
        downloads = asset.get('downloadCount', 0)
        print(f"  {slug}")
        print(f"    {context} | {downloads:,} downloads")
        if desc:
            print(f"    {desc}")
        print()


def cmd_info(args):
    """Get asset details."""
    asset = info(args.slug)

    owner = asset.get('owner', {})
    owner_name = owner.get('username', '?') if isinstance(owner, dict) else owner

    print(f"\n{args.slug}")
    print("=" * 40)
    print(f"Owner:       @{owner_name}")
    print(f"Context:     {asset.get('houdiniContext', '?').upper()}")
    print(f"License:     {asset.get('license', 'unknown')}")
    print(f"Version:     {asset.get('latestVersion', '?')}")
    print(f"Downloads:   {asset.get('downloadCount', 0):,}")

    if asset.get('description'):
        print(f"\n{asset['description']}")

    if asset.get('tags'):
        print(f"\nTags: {', '.join(asset['tags'])}")

    print()


def cmd_versions(args):
    """List asset versions."""
    vers = versions(args.slug)

    if not vers:
        print("No versions found.")
        return

    print(f"\nVersions of {args.slug}:\n")
    for v in vers:
        version = v.get('version', '?')
        published = v.get('publishedAt', '?')[:10] if v.get('publishedAt') else '?'
        downloads = v.get('downloadCount', 0)
        print(f"  {version:12} | {published} | {downloads:,} downloads")
    print()


def cmd_install(args):
    """Download asset to cache."""
    result = install(args.slug, force=args.force)

    if result['type'] == 'hda':
        print(f"\nHDA saved to: {result['path']}")
    else:
        print(f"\nPackage saved to: {result['path']}")


def cmd_preview(args):
    """Preview asset contents."""
    preview(args.slug)


def cmd_code(args):
    """Show asset code."""
    show_code(args.slug)


def cmd_cache(args):
    """Cache management."""
    if args.action == 'clear':
        cache_clear()
    else:
        cache_status()


def cmd_config(args):
    """Configuration management."""
    if args.action == 'server' and args.value:
        set_server_url(args.value)
    else:
        config = get_config()
        print("\nSopdrop Configuration:")
        print("=" * 40)
        for key, value in config.items():
            if key == 'token':
                value = '***' if value else None
            print(f"  {key}: {value}")
        print()


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description='Sopdrop - Houdini Asset Registry Client',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--version', '-v',
        action='version',
        version=f'sopdrop {__version__}',
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # login
    subparsers.add_parser('login', help='Authenticate with Sopdrop')

    # logout
    subparsers.add_parser('logout', help='Clear stored credentials')

    # search
    search_parser = subparsers.add_parser('search', help='Search for assets')
    search_parser.add_argument('query', help='Search query')
    search_parser.add_argument('--context', '-c', help='Filter by context (sop, vop, etc.)')
    search_parser.add_argument('--tags', '-t', help='Filter by tags (comma-separated)')

    # info
    info_parser = subparsers.add_parser('info', help='Get asset details')
    info_parser.add_argument('slug', help='Asset slug (user/asset-name)')

    # versions
    versions_parser = subparsers.add_parser('versions', help='List asset versions')
    versions_parser.add_argument('slug', help='Asset slug (user/asset-name)')

    # install
    install_parser = subparsers.add_parser('install', help='Download asset to cache')
    install_parser.add_argument('slug', help='Asset reference (user/asset or user/asset@1.0.0)')
    install_parser.add_argument('--force', '-f', action='store_true', help='Force re-download')

    # preview
    preview_parser = subparsers.add_parser('preview', help='Preview asset contents')
    preview_parser.add_argument('slug', help='Asset reference')

    # code
    code_parser = subparsers.add_parser('code', help='Show asset code')
    code_parser.add_argument('slug', help='Asset reference')

    # cache
    cache_parser = subparsers.add_parser('cache', help='Cache management')
    cache_parser.add_argument('action', nargs='?', choices=['status', 'clear'], default='status')

    # config
    config_parser = subparsers.add_parser('config', help='Configuration management')
    config_parser.add_argument('action', nargs='?', choices=['show', 'server'], default='show')
    config_parser.add_argument('value', nargs='?', help='Value to set')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Route to command handler
    commands = {
        'login': cmd_login,
        'logout': cmd_logout,
        'search': cmd_search,
        'info': cmd_info,
        'versions': cmd_versions,
        'install': cmd_install,
        'preview': cmd_preview,
        'code': cmd_code,
        'cache': cmd_cache,
        'config': cmd_config,
    }

    try:
        handler = commands.get(args.command)
        if handler:
            handler(args)
            return 0
        else:
            parser.print_help()
            return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
