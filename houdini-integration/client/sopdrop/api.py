"""
Sopdrop API client.

Handles communication with the Sopdrop server.
"""

import os
import json
import hashlib
import tempfile
import webbrowser
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote
from urllib.error import HTTPError, URLError

from .config import (
    get_api_url,
    get_token,
    save_token,
    clear_token,
    get_cache_dir,
    get_config,
)

# Import export/import modules (lazy, only when needed in Houdini)
_export_module = None
_import_module = None


def _get_export_module():
    global _export_module
    if _export_module is None:
        from . import export as _export_module
    return _export_module


def _get_import_module():
    global _import_module
    if _import_module is None:
        from . import importer as _import_module
    return _import_module


class SopdropError(Exception):
    """Base exception for Sopdrop errors."""
    pass


class AuthError(SopdropError):
    """Authentication error."""
    pass


class NotFoundError(SopdropError):
    """Asset not found."""
    pass


def _ssl_urlopen(req, timeout=30):
    """urlopen with SSL fallback for Houdini's bundled Python."""
    import ssl
    url = req.full_url if hasattr(req, 'full_url') else str(req)
    if url.startswith("https://"):
        try:
            ctx = ssl.create_default_context()
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                pass
            return urlopen(req, timeout=timeout, context=ctx)
        except (ssl.SSLCertVerificationError, URLError) as e:
            is_ssl = isinstance(e, ssl.SSLCertVerificationError)
            if isinstance(e, URLError) and 'CERTIFICATE_VERIFY_FAILED' in str(e.reason):
                is_ssl = True
            if not is_ssl:
                raise
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return urlopen(req, timeout=timeout, context=ctx)
    return urlopen(req, timeout=timeout)


class SopdropClient:
    """Sopdrop API client."""

    def __init__(self):
        self._hou = None

    @property
    def hou(self):
        """Lazy import of hou module (only available in Houdini)."""
        if self._hou is None:
            try:
                import hou
                self._hou = hou
            except ImportError:
                raise SopdropError("This function requires Houdini. Run inside Houdini Python.")
        return self._hou

    def _request(self, method, endpoint, data=None, auth=True):
        """Make an API request."""
        url = f"{get_api_url()}/{endpoint.lstrip('/')}"

        headers = {
            "Accept": "application/json",
            "User-Agent": "sopdrop-client/0.1.2",
        }

        if auth:
            token = get_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")

        req = Request(url, data=body, headers=headers, method=method)

        try:
            response = _ssl_urlopen(req, timeout=30)
            content = response.read().decode("utf-8")
            if content:
                return json.loads(content)
            return None
        except HTTPError as e:
            if e.code == 401:
                raise AuthError("Authentication required. Run sopdrop.login()")
            elif e.code == 404:
                raise NotFoundError(f"Not found: {endpoint}")
            else:
                error_body = e.read().decode("utf-8")
                try:
                    error_data = json.loads(error_body)
                    message = error_data.get("error", str(e))
                except json.JSONDecodeError:
                    message = error_body or str(e)
                raise SopdropError(f"API error ({e.code}): {message}")
        except URLError as e:
            raise SopdropError(f"Connection error: {e.reason}")

    def _get(self, endpoint, auth=True):
        return self._request("GET", endpoint, auth=auth)

    def _post(self, endpoint, data=None, auth=True):
        return self._request("POST", endpoint, data=data, auth=auth)

    def _put(self, endpoint, data=None, auth=True):
        return self._request("PUT", endpoint, data=data, auth=auth)

    def _delete(self, endpoint, auth=True):
        return self._request("DELETE", endpoint, auth=auth)

    # === Authentication ===

    def login(self):
        """
        Authenticate with Sopdrop.

        Opens browser for OAuth flow, then prompts for token.
        """
        config = get_config()
        auth_url = f"{config['server_url']}/auth/cli"

        print(f"Opening browser for authentication...")
        print(f"URL: {auth_url}")
        webbrowser.open(auth_url)

        print("\nAfter authenticating, copy the token and paste it here.")
        token = input("Token: ").strip()

        if not token:
            print("No token provided. Login cancelled.")
            return False

        # Verify token
        save_token(token)
        try:
            user = self._get("me")
            print(f"\n✓ Logged in as {user.get('username', user.get('email'))}")
            return True
        except AuthError:
            clear_token()
            print("Invalid token. Please try again.")
            return False

    def logout(self):
        """Clear stored credentials."""
        clear_token()
        print("Logged out.")

    # === Search & Browse ===

    def search(self, query, context=None, tags=None):
        """Search for assets."""
        params = {"q": query}
        if context:
            params["context"] = context
        if tags:
            params["tags"] = ",".join(tags) if isinstance(tags, list) else tags

        results = self._get(f"assets?{urlencode(params)}", auth=False)
        return results.get("assets", [])

    def info(self, asset_slug):
        """
        Get asset details.

        Args:
            asset_slug: Format 'username/asset-name'
        """
        return self._get(f"assets/{asset_slug}", auth=False)

    def versions(self, asset_slug):
        """List all versions of an asset."""
        return self._get(f"assets/{asset_slug}/versions", auth=False)

    # === Install & Cache ===

    def _get_cache_path(self, asset_slug, version, ext=".sopdrop"):
        """Get local cache path for an asset version."""
        safe_slug = asset_slug.replace("/", "_")
        return get_cache_dir() / f"{safe_slug}@{version}{ext}"

    def _is_cached(self, asset_slug, version):
        """Check if asset version is cached locally."""
        # Check for .sopdrop or .hda
        sopdrop_path = self._get_cache_path(asset_slug, version, ".sopdrop")
        hda_path = self._get_cache_path(asset_slug, version, ".hda")
        return sopdrop_path.exists() or hda_path.exists()

    def _download_to_cache(self, asset_slug, version):
        """
        Download asset to local cache.

        For node assets, returns the package data.
        For HDA assets, returns the file path.
        """
        # Get download URL
        download_url = f"{get_api_url()}/assets/{asset_slug}/download/{version}"

        headers = {
            "User-Agent": "sopdrop-client/0.1.2",
            "Accept": "application/json",
        }
        token = get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = Request(download_url, headers=headers)

        try:
            response = _ssl_urlopen(req, timeout=60)
            content_type = response.headers.get("Content-Type", "")

            if "application/json" in content_type:
                # Node package (.sopdrop)
                data = json.loads(response.read().decode("utf-8"))
                package = data.get("package", data)

                # Save to cache
                cache_path = self._get_cache_path(asset_slug, version, ".sopdrop")
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(package, indent=2))

                return {"type": "node", "package": package, "path": cache_path}
            else:
                # Binary file (HDA)
                content = response.read()
                cache_path = self._get_cache_path(asset_slug, version, ".hda")
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(content)

                return {"type": "hda", "path": cache_path}

        except HTTPError as e:
            if e.code == 404:
                raise NotFoundError(f"Asset not found: {asset_slug}@{version}")
            raise SopdropError(f"Download failed: {e}")

    def _load_from_cache(self, asset_slug, version):
        """Load a cached asset."""
        sopdrop_path = self._get_cache_path(asset_slug, version, ".sopdrop")
        hda_path = self._get_cache_path(asset_slug, version, ".hda")

        if sopdrop_path.exists():
            package = json.loads(sopdrop_path.read_text())
            return {"type": "node", "package": package, "path": sopdrop_path}
        elif hda_path.exists():
            return {"type": "hda", "path": hda_path}
        else:
            return None

    def install(self, asset_ref, force=False):
        """
        Install an asset locally.

        Args:
            asset_ref: 'user/asset' or 'user/asset@1.0.0'
            force: Force re-download even if cached

        Returns:
            Dict with 'type', 'package' (for nodes), and 'path'
        """
        # Parse asset reference
        if "@" in asset_ref:
            asset_slug, version = asset_ref.rsplit("@", 1)
        else:
            asset_slug = asset_ref
            # Get latest version
            asset_info = self.info(asset_slug)
            version = asset_info.get("latestVersion") or asset_info.get("latest_version")
            if not version:
                raise NotFoundError(f"Asset '{asset_slug}' has no published versions")

        # Check cache
        if not force and self._is_cached(asset_slug, version):
            print(f"✓ {asset_slug}@{version} (cached)")
            return self._load_from_cache(asset_slug, version)

        # Download
        print(f"Downloading {asset_slug}@{version}...")
        result = self._download_to_cache(asset_slug, version)
        print(f"✓ Installed {asset_slug}@{version}")

        return result

    # === Paste ===

    def paste(self, asset_ref=None, force=False, trust=False):
        """
        Paste asset into current Houdini network.

        Args:
            asset_ref: Asset to paste (e.g., 'user/scatter-points@1.0.0')
            force: Skip context mismatch check
            trust: Skip security warning for untrusted assets (use with caution)

        Security Note:
            Pasting executes code from the Sopdrop registry. While packages are
            scanned for dangerous patterns, they can still contain arbitrary
            Houdini Python. Always review assets from unknown publishers.
        """
        hou = self.hou
        importer = _get_import_module()

        if asset_ref:
            # Parse asset reference
            asset_slug = asset_ref.rsplit("@", 1)[0] if "@" in asset_ref else asset_ref

            # Install and get package
            result = self.install(asset_ref)

            if result["type"] == "hda":
                # HDAs require extra warning - they can execute arbitrary code
                if not trust:
                    print("\n" + "=" * 60)
                    print("⚠️  HDA SECURITY WARNING")
                    print("=" * 60)
                    print(f"Asset: {asset_ref}")
                    print("\nHoudini Digital Assets can execute arbitrary Python code")
                    print("via callbacks, shelf tools, and expressions.")
                    print("\nOnly install HDAs from publishers you trust.")
                    print("=" * 60)

                    try:
                        result_choice = hou.ui.displayMessage(
                            f"Install HDA: {asset_ref}\n\n"
                            "⚠️ HDAs can execute arbitrary code via callbacks.\n\n"
                            "Only install HDAs from publishers you trust.\n\n"
                            "Continue?",
                            buttons=("Install", "Cancel"),
                            severity=hou.severityType.Warning,
                            default_choice=1,
                            close_choice=1,
                            title="Sopdrop - HDA Security Warning",
                        )
                        if result_choice == 1:
                            print("Installation cancelled.")
                            return
                    except Exception:
                        # Non-interactive - prompt in console
                        response = input("\nInstall this HDA? (y/N): ").strip().lower()
                        if response != 'y':
                            print("Installation cancelled.")
                            return

                # Install HDA
                hda_path = result["path"]
                try:
                    hou.hda.installFile(str(hda_path))
                    print(f"✓ Installed HDA: {asset_ref}")
                except Exception as e:
                    raise SopdropError(f"Failed to install HDA: {e}")
                return

            # Node package
            package = result["package"]
            metadata = package.get("metadata", {})

            # Security check - always show warning for untrusted assets
            if not trust:
                # Get asset info
                asset_info = None
                owner_name = "unknown"
                download_count = 0
                is_own = False
                is_verified = False

                try:
                    asset_info = self.info(asset_slug)
                    owner = asset_info.get("owner", {})
                    owner_name = owner.get("username", "unknown") if isinstance(owner, dict) else owner
                    download_count = asset_info.get("downloadCount", 0)
                    is_verified = owner.get("emailVerified", False) if isinstance(owner, dict) else False

                    # Check if it's from current user
                    try:
                        me = self._get("auth/me")
                        is_own = me.get("username") == owner_name
                    except Exception:
                        is_own = False
                except Exception:
                    pass

                # Always show security info (unless it's your own asset)
                if not is_own:
                    # Build warning details
                    warnings = []
                    node_count = metadata.get("node_count", "?")
                    context = package.get("context", "unknown").upper()

                    if metadata.get("has_python_sops"):
                        warnings.append("Contains Python SOP nodes (executes code)")

                    if metadata.get("has_hda_dependencies"):
                        warnings.append("Requires external HDAs")

                    if download_count < 10:
                        warnings.append(f"Low download count ({download_count})")

                    if not is_verified:
                        warnings.append("Publisher email not verified")

                    # Console output
                    print("\n" + "=" * 60)
                    print("⚠️  SECURITY CHECK - Review Before Pasting")
                    print("=" * 60)
                    print(f"Asset:      {asset_slug}")
                    print(f"Publisher:  @{owner_name}" + (" ✓" if is_verified else ""))
                    print(f"Downloads:  {download_count:,}")
                    print(f"Context:    {context}")
                    print(f"Nodes:      {node_count}")

                    if warnings:
                        print("\nWarnings:")
                        for w in warnings:
                            print(f"  ⚠️  {w}")

                    print("\nTo review the code first:")
                    print(f"  sopdrop.show_code(\"{asset_slug}\")")
                    print("\nTo preview without executing:")
                    print(f"  sopdrop.preview(\"{asset_slug}\")")
                    print("=" * 60)

                    # Interactive confirmation
                    try:
                        # Build dialog message
                        dialog_msg = f"Asset from '@{owner_name}'\n\n"
                        dialog_msg += f"• {node_count} nodes ({context})\n"
                        dialog_msg += f"• {download_count:,} downloads\n"

                        if warnings:
                            dialog_msg += "\nWarnings:\n"
                            for w in warnings:
                                dialog_msg += f"• {w}\n"

                        dialog_msg += "\nThis will execute code in your Houdini session."

                        severity = hou.severityType.Warning if warnings else hou.severityType.Message

                        result_choice = hou.ui.displayMessage(
                            dialog_msg,
                            buttons=("Paste", "Preview Code", "Cancel"),
                            severity=severity,
                            default_choice=2,  # Default to Cancel
                            close_choice=2,
                            title="Sopdrop - Security Check",
                        )

                        if result_choice == 2:  # Cancel
                            print("Paste cancelled.")
                            return
                        elif result_choice == 1:  # Preview Code
                            self.show_code(asset_ref)
                            print("\nRun sopdrop.paste() again to paste after reviewing.")
                            return

                    except Exception:
                        # Non-interactive mode - require explicit confirmation
                        response = input("\nPaste this asset? (y/N): ").strip().lower()
                        if response != 'y':
                            print("Paste cancelled.")
                            return

            # Import the package
            try:
                items = importer.import_at_cursor(package)
                if items:
                    print(f"✓ Pasted {len(items)} items from {asset_ref}")
                else:
                    # Items might have been created even if not returned
                    print(f"✓ Paste completed for {asset_ref}")
            except importer.ContextMismatchError as e:
                raise SopdropError(str(e))
            except importer.MissingDependencyError as e:
                raise SopdropError(str(e))
            except importer.ChecksumError as e:
                raise SopdropError(f"Security check failed: {e}")
            except importer.ImportError as e:
                raise SopdropError(f"Import failed: {e}")
            except Exception as e:
                raise SopdropError(f"Failed to paste: {e}")
        else:
            # Paste from Houdini clipboard
            hou.pasteNodesFromClipboard(hou.node("/"))

    def preview(self, asset_ref):
        """
        Preview an asset without executing it.

        Shows detailed information about what would be pasted,
        including node names, types, and any potential risks.

        Args:
            asset_ref: Asset to preview (e.g., 'user/scatter-points@1.0.0')
        """
        result = self.install(asset_ref)

        if result["type"] == "hda":
            print("\n" + "=" * 60)
            print("HDA PREVIEW")
            print("=" * 60)
            print(f"Asset: {asset_ref}")
            print(f"Path:  {result['path']}")
            print("\nThis is a Houdini Digital Asset.")
            print("Use Houdini's Type Properties (RMB > Type Properties)")
            print("to inspect the HDA contents before installing.")
            print("=" * 60)
            return

        package = result["package"]
        metadata = package.get("metadata", {})
        fmt = package.get("format", "unknown")

        print("\n" + "=" * 60)
        print("ASSET PREVIEW")
        print("=" * 60)
        print(f"Asset:    {asset_ref}")
        print(f"Format:   {fmt}")
        print(f"Context:  {package.get('context', 'unknown').upper()}")
        print(f"Houdini:  {package.get('houdini_version', 'unknown')}")

        print(f"\nNodes ({metadata.get('node_count', 0)}):")
        node_names = metadata.get("node_names", [])
        node_types = metadata.get("node_types", [])
        for i, name in enumerate(node_names[:20]):
            node_type = node_types[i] if i < len(node_types) else "?"
            print(f"  • {name} ({node_type})")
        if len(node_names) > 20:
            print(f"  ... and {len(node_names) - 20} more")

        if metadata.get("network_boxes"):
            print(f"\nNetwork Boxes: {', '.join(metadata['network_boxes'])}")

        if metadata.get("sticky_notes"):
            print(f"Sticky Notes: {metadata['sticky_notes']}")

        # Risk assessment
        print("\nRisk Assessment:")
        risks = []

        if metadata.get("has_python_sops"):
            risks.append("⚠️  Contains Python SOPs (arbitrary code execution)")
        if metadata.get("has_hda_dependencies"):
            risks.append("⚠️  Requires external HDAs")
        if metadata.get("has_expressions"):
            risks.append("ℹ️  Uses channel expressions (ch/chs references)")

        if risks:
            for r in risks:
                print(f"  {r}")
        else:
            print("  ✓ No obvious risks detected")

        # Show checksum for v2 packages
        if package.get("checksum"):
            print(f"\nChecksum: {package['checksum'][:32]}...")
            print("          (SHA-256 verified on download)")

        print("\n" + "=" * 60)
        print("To paste this asset:")
        print(f"  sopdrop.paste(\"{asset_ref}\")")
        print("\nTo see the raw code (v1 packages only):")
        print(f"  sopdrop.show_code(\"{asset_ref}\")")
        print("=" * 60)

    # === Publish ===

    def publish(self, nodes=None, name=None, description=None, license="mit", tags=None):
        """
        Publish nodes to Sopdrop.

        Args:
            nodes: Houdini items to publish (default: selected items)
            name: Asset name (prompted if not provided)
            description: Asset description
            license: License type (default: 'mit')
            tags: List of tags

        Returns:
            Dict with published asset info
        """
        hou = self.hou
        exporter = _get_export_module()

        # Get items
        if nodes is None:
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if pane:
                parent = pane.pwd()
                nodes = list(parent.selectedItems())

        if not nodes:
            raise SopdropError("No items selected. Select nodes to publish.")

        # Preview what we're exporting
        exporter.preview_export(nodes)

        # Get name
        if not name:
            try:
                result = hou.ui.readInput(
                    "Asset name:",
                    buttons=("Publish", "Cancel"),
                    default_choice=0,
                    close_choice=1,
                )
                if result[0] == 1:  # Cancel
                    return None
                name = result[1].strip()
            except Exception:
                # Non-interactive mode
                name = input("Asset name: ").strip()

        if not name:
            raise SopdropError("Name is required")

        # Get description if not provided
        if description is None:
            try:
                result = hou.ui.readInput(
                    "Description (optional):",
                    buttons=("Continue", "Skip"),
                    default_choice=0,
                    close_choice=1,
                )
                if result[0] == 0:
                    description = result[1].strip()
            except Exception:
                pass  # Skip in non-interactive mode

        # Export to .sopdrop package
        print(f"Exporting {name}...")
        try:
            package = exporter.export_network(nodes)
        except Exception as e:
            raise SopdropError(f"Failed to export: {e}")

        # Check for HDA dependencies
        if package.get("dependencies"):
            deps = package["dependencies"]
            print(f"\n⚠️  This asset uses {len(deps)} custom HDA(s):")
            for dep in deps:
                print(f"   - {dep['name']} v{dep.get('version', 'unknown')}")
            print("\n   Note: Users will need these HDAs installed to use this asset.")

            try:
                result = hou.ui.displayMessage(
                    f"This asset uses {len(deps)} custom HDA(s).\n\n"
                    "Users will need these HDAs installed to use this asset.\n\n"
                    "Continue publishing?",
                    buttons=("Publish", "Cancel"),
                    default_choice=0,
                    close_choice=1,
                )
                if result == 1:
                    print("Publish cancelled.")
                    return None
            except Exception:
                pass  # Non-interactive mode, continue

        # Prepare request data
        data = {
            "name": name,
            "description": description or "",
            "license": license,
            "tags": tags if tags else [],
            "package": package,
        }

        # Send request
        print(f"Publishing {name}...")

        token = get_token()
        if not token:
            raise AuthError("Please login first: sopdrop.login()")

        try:
            result = self._post("assets", data=data, auth=True)
            slug = result.get("slug", name)
            print(f"\n✓ Published: {slug}")
            print(f"  Version: {result.get('version', '1.0.0')}")
            print(f"  Nodes: {result.get('nodeCount', package['metadata']['node_count'])}")
            print(f"  Context: {result.get('context', package['context'])}")
            return result
        except HTTPError as e:
            error_body = e.read().decode()
            try:
                error_data = json.loads(error_body)
                message = error_data.get("error", error_body)
            except json.JSONDecodeError:
                message = error_body
            raise SopdropError(f"Publish failed: {message}")

    def publish_hda(
        self,
        hda_info: dict,
        name: str = None,
        description: str = None,
        license: str = "mit",
        tags: list = None,
        is_public: bool = True,
    ):
        """
        Publish an HDA to Sopdrop.

        Args:
            hda_info: Dict from export.detect_publishable_hda()
            name: Asset name (defaults to HDA type label)
            description: Asset description
            license: License type (default: 'mit')
            tags: List of tags
            is_public: Whether the asset is publicly visible

        Returns:
            Dict with published asset info
        """
        hou = self.hou

        if not hda_info:
            raise SopdropError("No HDA info provided")

        lib_path = hda_info.get('library_path')
        if not lib_path:
            raise SopdropError("HDA library path not found")

        import os
        if not os.path.exists(lib_path):
            raise SopdropError(f"HDA file not found: {lib_path}")

        # Use label as default name
        if not name:
            name = hda_info.get('type_label') or hda_info.get('type_name', 'Unnamed HDA')

        # Get description if not provided
        if description is None:
            try:
                result = hou.ui.readInput(
                    "Description (optional):",
                    buttons=("Continue", "Skip"),
                    default_choice=0,
                    close_choice=1,
                )
                if result[0] == 0:
                    description = result[1].strip()
            except Exception:
                pass

        token = get_token()
        if not token:
            raise AuthError("Please login first: sopdrop.login()")

        # Read HDA file
        print(f"Publishing HDA: {name}...")

        # Use multipart form upload
        import urllib.request
        import mimetypes

        boundary = '----SopdropHDAUpload' + str(uuid.uuid4()).replace('-', '')

        # Helper to safely encode text that might have non-UTF-8 chars
        def safe_encode(text):
            if isinstance(text, bytes):
                # Try to decode as UTF-8, fall back to latin-1
                try:
                    text = text.decode('utf-8')
                except UnicodeDecodeError:
                    text = text.decode('latin-1', errors='replace')
            # Encode as UTF-8, replacing any problematic characters
            return str(text).encode('utf-8', errors='replace')

        # Build multipart body
        body_parts = []

        # Add form fields
        fields = {
            'name': name,
            'description': description or '',
            'license': license,
            'houdiniContext': hda_info.get('category', 'Sop').lower(),
            'isPublic': str(is_public).lower(),
        }

        if tags:
            fields['tags'] = json.dumps(tags)

        for key, value in fields.items():
            body_parts.append(f'--{boundary}'.encode())
            body_parts.append(f'Content-Disposition: form-data; name="{key}"'.encode())
            body_parts.append(b'')
            body_parts.append(safe_encode(value))

        # Add file
        file_name = os.path.basename(lib_path)
        with open(lib_path, 'rb') as f:
            file_data = f.read()

        body_parts.append(f'--{boundary}'.encode())
        body_parts.append(
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"'.encode()
        )
        body_parts.append(b'Content-Type: application/octet-stream')
        body_parts.append(b'')
        body_parts.append(file_data)
        body_parts.append(f'--{boundary}--'.encode())

        body = b'\r\n'.join(body_parts)

        # Make request
        url = f"{get_api_url()}/assets/hda"
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('Authorization', f'Bearer {token}')
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

        try:
            import ssl
            if url.startswith("https://"):
                try:
                    ctx = ssl.create_default_context()
                    try:
                        import certifi
                        ctx = ssl.create_default_context(cafile=certifi.where())
                    except ImportError:
                        pass
                    response = urllib.request.urlopen(req, timeout=120, context=ctx)
                except (ssl.SSLCertVerificationError, URLError) as ssl_err:
                    is_ssl = isinstance(ssl_err, ssl.SSLCertVerificationError)
                    if isinstance(ssl_err, URLError) and 'CERTIFICATE_VERIFY_FAILED' in str(ssl_err.reason):
                        is_ssl = True
                    if not is_ssl:
                        raise
                    import warnings
                    warnings.warn(
                        "SSL certificate verification failed. Falling back to unverified connection.",
                        stacklevel=2
                    )
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    response = urllib.request.urlopen(req, timeout=120, context=ctx)
            else:
                response = urllib.request.urlopen(req, timeout=120)

            result = json.loads(response.read().decode())

            slug = result.get('slug', name)
            print(f"\n✓ Published HDA: {slug}")
            print(f"  Version: {result.get('version', '1.0.0')}")
            print(f"  Type: {hda_info.get('type_name', 'unknown')}")
            return result

        except HTTPError as e:
            error_body = e.read().decode()
            try:
                error_data = json.loads(error_body)
                message = error_data.get('error', error_body)
            except json.JSONDecodeError:
                message = error_body
            raise SopdropError(f"Publish failed: {message}")
        except URLError as e:
            raise SopdropError(f"Connection error: {e.reason}")

    # === Cache Management ===

    def cache_status(self):
        """Show cache status."""
        cache_dir = get_cache_dir()
        if not cache_dir.exists():
            print("Cache is empty")
            return

        sopdrop_files = list(cache_dir.glob("*.sopdrop"))
        hda_files = list(cache_dir.glob("*.hda"))
        all_files = sopdrop_files + hda_files
        total_size = sum(f.stat().st_size for f in all_files)

        print(f"Cache: {cache_dir}")
        print(f"Node packages: {len(sopdrop_files)}")
        print(f"HDAs: {len(hda_files)}")
        print(f"Total size: {total_size / 1024 / 1024:.1f} MB")

        if all_files:
            print("\nCached assets:")
            for f in sorted(all_files):
                size_kb = f.stat().st_size / 1024
                ext = f.suffix
                print(f"  {f.stem} ({ext}): {size_kb:.0f} KB")

    def cache_clear(self):
        """Clear the cache."""
        cache_dir = get_cache_dir()
        if cache_dir.exists():
            count = 0
            for ext in ["*.sopdrop", "*.hda"]:
                for f in cache_dir.glob(ext):
                    f.unlink()
                    count += 1
            print(f"Cache cleared ({count} files removed)")
        else:
            print("Cache is already empty")

    # === Code Review ===

    def show_code(self, asset_ref):
        """
        Show the Python code for an asset.

        Useful for reviewing assets before pasting.
        """
        result = self.install(asset_ref)

        if result["type"] == "hda":
            print("This is an HDA asset. Use Houdini's Type Properties to inspect.")
            return

        package = result["package"]
        fmt = package.get("format", "")

        if fmt.startswith("sopdrop-v2") or not fmt.startswith("sopdrop-v1"):
            # v2 format uses binary data, not code
            print("\n=== Package Info ===")
            print(f"Format: {fmt}")
            print("This package uses binary format (v2) - no viewable code.")
            print("\nTo see what's in the package:")
            meta = package.get("metadata", {})
            print(f"  Nodes: {meta.get('node_count', 'unknown')}")
            print(f"  Types: {', '.join(meta.get('node_types', []))}")
            print(f"  Names: {', '.join(meta.get('node_names', []))}")
            if package.get("checksum"):
                print(f"  Checksum: {package['checksum'][:16]}...")
        else:
            # v1 format has Python code
            code = package.get("code", "")
            if code:
                print("\n=== Package Code ===")
                print(code)
            else:
                print("No code in package")

    def show_info(self, asset_ref):
        """
        Show detailed information about an asset.
        """
        result = self.install(asset_ref)

        if result["type"] == "hda":
            print("This is an HDA asset.")
            print(f"Path: {result['path']}")
            return

        package = result["package"]
        importer = _get_import_module()
        importer.show_package_info(package)

        # Show additional v2-specific info
        if package.get("checksum"):
            print(f"\nIntegrity: SHA-256 verified")
