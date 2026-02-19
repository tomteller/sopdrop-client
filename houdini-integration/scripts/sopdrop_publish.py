"""
Sopdrop Publish Tool

Publishes selected nodes to Sopdrop registry using the hybrid workflow:
1. Export nodes and capture screenshot
2. Upload as draft to server
3. Open browser to complete listing (add name, description)
4. User publishes from web UI
"""

import hou
import webbrowser
import tempfile
import os
import base64
import ssl
import json

# Try to use modern PySide UI
USE_MODERN_UI = False
PYSIDE_VERSION = 0

try:
    from sopdrop_ui import show_publish_dialog, show_error, PYSIDE_VERSION
    if PYSIDE_VERSION > 0:
        USE_MODERN_UI = True
        print(f"Sopdrop: Using modern UI (PySide{PYSIDE_VERSION})")
    else:
        print("Sopdrop: PySide not available, using basic UI")
except ImportError as e:
    print(f"Sopdrop: Could not load modern UI: {e}")
    USE_MODERN_UI = False


def main():
    """Main entry point for the publish tool."""
    # Check for sopdrop module
    try:
        import sopdrop
    except ImportError:
        hou.ui.displayMessage(
            "Sopdrop client not installed.\n\n"
            "Install with:\n"
            "pip install sopdrop\n\n"
            "Or add sopdrop-client to your PYTHONPATH.",
            title="Sopdrop - Not Installed",
            severity=hou.severityType.Error,
        )
        return

    # Get selected items
    pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if not pane:
        hou.ui.displayMessage(
            "No network editor found.",
            title="Sopdrop",
            severity=hou.severityType.Error,
        )
        return

    parent = pane.pwd()
    items = list(parent.selectedItems())

    if not items:
        hou.ui.displayMessage(
            "No items selected.\n\n"
            "Select nodes, network boxes, or sticky notes to publish.",
            title="Sopdrop - Nothing Selected",
            severity=hou.severityType.Warning,
        )
        return

    # Count items by type
    nodes = [i for i in items if isinstance(i, hou.Node)]
    netboxes = [i for i in items if isinstance(i, hou.NetworkBox)]
    stickies = [i for i in items if isinstance(i, hou.StickyNote)]

    if not nodes:
        hou.ui.displayMessage(
            "No nodes selected.\n\n"
            "Select at least one node to publish.",
            title="Sopdrop - No Nodes",
            severity=hou.severityType.Warning,
        )
        return

    # Check if logged in
    from sopdrop.config import get_token
    if not get_token():
        result = hou.ui.displayMessage(
            "You need to log in to publish.\n\n"
            "Would you like to log in now?",
            buttons=("Login", "Cancel"),
            default_choice=0,
            close_choice=1,
            title="Sopdrop - Login Required",
        )
        if result == 0:
            login()
            if not get_token():
                return
        else:
            return

    # Show publish dialog - use modern UI if available
    if USE_MODERN_UI:
        try:
            result = show_publish_dialog(items, nodes, netboxes, stickies)
            if result:
                # Success - browser already opened by dialog
                pass
            return
        except Exception as e:
            print(f"Modern UI failed, falling back: {e}")

    # Fallback to basic dialog
    dialog = PublishDialog(items, nodes, netboxes, stickies, pane)
    dialog.show()


def login():
    """Show login dialog."""
    try:
        import sopdrop
    except ImportError:
        return

    from sopdrop.config import get_config, save_token

    config = get_config()
    auth_url = f"{config['server_url']}/auth/cli"

    # Open browser
    webbrowser.open(auth_url)

    # Prompt for token
    result = hou.ui.readInput(
        "After logging in on the website, copy the token and paste it here:",
        buttons=("Save", "Cancel"),
        default_choice=0,
        close_choice=1,
        title="Sopdrop - Enter Token",
    )

    if result[0] == 0 and result[1].strip():
        token = result[1].strip()
        save_token(token)

        # Verify token
        try:
            from sopdrop.api import SopdropClient
            client = SopdropClient()
            user = client._get("auth/me")
            hou.ui.displayMessage(
                f"Logged in as: {user.get('username', user.get('email'))}",
                title="Sopdrop - Login Success",
            )
        except Exception as e:
            from sopdrop.config import clear_token
            clear_token()
            hou.ui.displayMessage(
                f"Login failed: {e}",
                title="Sopdrop - Login Failed",
                severity=hou.severityType.Error,
            )


def capture_screenshot_from_clipboard():
    """
    Get screenshot from clipboard if available.

    Returns the screenshot as base64-encoded PNG data, or None if not available.
    """
    try:
        # Try to import Qt
        try:
            from PySide6 import QtWidgets, QtCore
        except ImportError:
            try:
                from PySide2 import QtWidgets, QtCore
            except ImportError:
                print("[Sopdrop] PySide not available for clipboard access")
                return None

        clipboard = QtWidgets.QApplication.clipboard()
        mime_data = clipboard.mimeData()

        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull() and image.width() > 50 and image.height() > 50:
                # Convert QImage to PNG bytes
                byte_array = QtCore.QByteArray()
                buffer = QtCore.QBuffer(byte_array)
                buffer.open(QtCore.QIODevice.WriteOnly)
                image.save(buffer, "PNG")
                buffer.close()

                image_data = bytes(byte_array)
                if len(image_data) > 100:
                    print(f"[Sopdrop] Screenshot from clipboard: {len(image_data)} bytes ({image.width()}x{image.height()})")
                    return base64.b64encode(image_data).decode('ascii')

        print("[Sopdrop] No screenshot in clipboard")
        return None

    except Exception as e:
        print(f"[Sopdrop] Clipboard screenshot failed: {e}")
        return None


class PublishDialog:
    """Dialog for publishing to Sopdrop."""

    def __init__(self, items, nodes, netboxes, stickies, pane):
        self.items = items
        self.nodes = nodes
        self.netboxes = netboxes
        self.stickies = stickies
        self.pane = pane

    def show(self):
        """Show the publish dialog."""
        from sopdrop.export import export_items, detect_hda_dependencies

        # Build summary
        total_nodes = len(self.nodes)
        for node in self.nodes:
            total_nodes += len(node.allSubChildren())

        summary_lines = [
            f"Nodes: {len(self.nodes)} selected ({total_nodes} total including children)",
        ]

        if self.netboxes:
            summary_lines.append(f"Network Boxes: {len(self.netboxes)}")

        if self.stickies:
            summary_lines.append(f"Sticky Notes: {len(self.stickies)}")

        # Get context
        context = self._get_context()
        summary_lines.append(f"Context: {context.upper()}")

        # Check for HDA dependencies
        deps = detect_hda_dependencies(self.nodes)
        if deps:
            summary_lines.append("")
            summary_lines.append(f"HDA Dependencies: {len(deps)}")
            for dep in deps[:3]:
                summary_lines.append(f"  - {dep['name']}")
            if len(deps) > 3:
                summary_lines.append(f"  ... and {len(deps) - 3} more")

        summary = "\n".join(summary_lines)

        # Confirm upload
        result = hou.ui.displayMessage(
            f"Ready to publish to Sopdrop\n\n{summary}\n\n"
            "TIP: Take a screenshot of your nodes (Cmd+Shift+4 on Mac, Win+Shift+S on Windows)\n"
            "and copy it to clipboard before clicking Upload. You can also add one in the browser.",
            buttons=("Upload & Continue in Browser", "Cancel"),
            default_choice=0,
            close_choice=1,
            title="Sopdrop - Publish",
        )

        if result != 0:
            return

        # Confirm HDA dependencies
        if deps:
            result = hou.ui.displayMessage(
                f"This asset uses {len(deps)} custom HDA(s).\n\n"
                "Users will need these HDAs installed to use this asset.\n\n"
                "Continue publishing?",
                buttons=("Continue", "Cancel"),
                default_choice=0,
                close_choice=1,
                title="Sopdrop - HDA Dependencies",
            )
            if result != 0:
                return

        # Export and upload
        self._upload_draft()

    def _get_context(self):
        """Get the current network context."""
        if not self.nodes:
            return "unknown"

        try:
            parent = self.nodes[0].parent()
            category = parent.childTypeCategory().name().lower()
            context_map = {
                'sop': 'sop', 'object': 'obj', 'vop': 'vop',
                'dop': 'dop', 'cop2': 'cop', 'top': 'top',
                'lop': 'lop', 'chop': 'chop', 'shop': 'shop',
                'rop': 'out', 'driver': 'out',
            }
            return context_map.get(category, category)
        except Exception:
            return 'unknown'

    def _upload_draft(self):
        """Export package, capture screenshot, and upload as draft."""
        from sopdrop.export import export_items
        from sopdrop.config import get_api_url, get_token

        try:
            with hou.InterruptableOperation(
                "Exporting nodes...",
                open_interrupt_dialog=True,
            ) as op:
                # Export the package
                package = export_items(self.items)

            # Capture screenshot
            print("Capturing screenshot...")
            screenshot_data = capture_screenshot_from_clipboard()
            if screenshot_data:
                print(f"Screenshot captured: {len(screenshot_data)} bytes")
            else:
                print("Screenshot capture failed, will need manual upload")

            with hou.InterruptableOperation(
                "Uploading to Sopdrop...",
                open_interrupt_dialog=True,
            ) as op:
                # Upload as draft with screenshot
                result = self._create_draft(package, screenshot_data)

            if result.get("error"):
                raise Exception(result["error"])

            draft_id = result.get("draftId")
            complete_url = result.get("completeUrl")

            if not complete_url:
                raise Exception("Server did not return a completion URL")

            # Success - open browser
            has_screenshot = "with screenshot" if screenshot_data else "without screenshot"
            hou.ui.displayMessage(
                f"Package uploaded {has_screenshot}!\n\n"
                f"Opening browser to complete your listing...\n\n"
                f"You'll need to:\n"
                f"• Enter a name and description\n"
                f"• Review/replace the thumbnail if needed\n"
                f"• Click Publish\n\n"
                f"Draft expires in 24 hours.",
                title="Sopdrop - Upload Complete",
            )

            webbrowser.open(complete_url)

        except hou.OperationInterrupted:
            hou.ui.displayMessage(
                "Upload cancelled.",
                title="Sopdrop",
            )
        except Exception as e:
            hou.ui.displayMessage(
                f"Upload failed:\n\n{e}",
                title="Sopdrop - Error",
                severity=hou.severityType.Error,
            )

    def _create_draft(self, package, screenshot_data=None):
        """Create a draft on the server."""
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError
        from sopdrop.config import get_api_url, get_token

        url = f"{get_api_url()}/drafts"
        token = get_token()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "sopdrop-houdini/0.1.0",
        }

        # Build request body
        body_data = {"package": package}
        if screenshot_data:
            body_data["screenshot"] = screenshot_data

        body = json.dumps(body_data).encode("utf-8")
        req = Request(url, data=body, headers=headers, method="POST")

        try:
            # Only use SSL context for HTTPS URLs
            if url.startswith("https://"):
                # Skip SSL verification - Houdini's Python often has cert issues
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                response = urlopen(req, timeout=120, context=ctx)
            else:
                # Plain HTTP - no SSL context needed
                response = urlopen(req, timeout=120)

            return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_data = json.loads(error_body)
                raise Exception(error_data.get("error", str(e)))
            except json.JSONDecodeError:
                raise Exception(f"HTTP {e.code}: {error_body}")
        except URLError as e:
            raise Exception(f"Connection error: {e.reason}")


def publish_from_library(package, name="", description="", tags=None, thumbnail_image=None, additional_images=None, library_asset_id=None):
    """
    Publish a library asset to the cloud with pre-filled data.

    Args:
        package: The exported package data (dict with 'code', 'context', etc.)
        name: Pre-filled name for the asset
        description: Pre-filled description
        tags: Pre-filled tags list
        thumbnail_image: QImage of the thumbnail (optional)
        additional_images: List of QImage objects for additional screenshots
        library_asset_id: ID of the local library asset (for marking as synced)
    """
    import webbrowser

    # Check if logged in
    from sopdrop.config import get_token, get_api_url
    if not get_token():
        result = hou.ui.displayMessage(
            "You need to log in to publish.\n\n"
            "Would you like to log in now?",
            buttons=("Login", "Cancel"),
            default_choice=0,
            close_choice=1,
            title="Sopdrop - Login Required",
        )
        if result == 0:
            login()
            if not get_token():
                return
        else:
            return

    # Convert thumbnail to base64
    screenshot_data = None
    if thumbnail_image is not None:
        try:
            try:
                from PySide6 import QtCore
            except ImportError:
                from PySide2 import QtCore

            if not thumbnail_image.isNull():
                byte_array = QtCore.QByteArray()
                buffer = QtCore.QBuffer(byte_array)
                buffer.open(QtCore.QIODevice.WriteOnly)
                thumbnail_image.save(buffer, "PNG")
                buffer.close()
                image_data = bytes(byte_array)
                if len(image_data) > 100:
                    screenshot_data = base64.b64encode(image_data).decode('ascii')
                    print(f"[Sopdrop] Using library thumbnail: {len(image_data)} bytes")
        except Exception as e:
            print(f"[Sopdrop] Failed to convert thumbnail: {e}")

    # Convert additional images to base64
    additional_images_data = []
    if additional_images:
        try:
            try:
                from PySide6 import QtCore
            except ImportError:
                from PySide2 import QtCore

            for img in additional_images:
                if img and not img.isNull():
                    byte_array = QtCore.QByteArray()
                    buffer = QtCore.QBuffer(byte_array)
                    buffer.open(QtCore.QIODevice.WriteOnly)
                    img.save(buffer, "PNG")
                    buffer.close()
                    image_data = bytes(byte_array)
                    if len(image_data) > 100:
                        additional_images_data.append(base64.b64encode(image_data).decode('ascii'))
            if additional_images_data:
                print(f"[Sopdrop] Including {len(additional_images_data)} additional images")
        except Exception as e:
            print(f"[Sopdrop] Failed to convert additional images: {e}")

    # If no thumbnail from library, check clipboard
    if not screenshot_data:
        screenshot_data = capture_screenshot_from_clipboard()

    try:
        with hou.InterruptableOperation(
            "Uploading to Sopdrop...",
            open_interrupt_dialog=True,
        ) as op:
            # Create draft with pre-filled data
            from urllib.request import Request, urlopen
            from urllib.error import HTTPError, URLError
            import ssl

            url = f"{get_api_url()}/drafts"
            token = get_token()

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "sopdrop-houdini/0.1.0",
            }

            # Include pre-filled metadata
            body_data = {
                "package": package,
                "prefill": {
                    "name": name,
                    "description": description,
                    "tags": tags or [],
                }
            }
            if screenshot_data:
                body_data["screenshot"] = screenshot_data
            if additional_images_data:
                body_data["additional_images"] = additional_images_data

            body = json.dumps(body_data).encode("utf-8")
            req = Request(url, data=body, headers=headers, method="POST")

            if url.startswith("https://"):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                response = urlopen(req, timeout=120, context=ctx)
            else:
                response = urlopen(req, timeout=120)

            result = json.loads(response.read().decode("utf-8"))

        if result.get("error"):
            raise Exception(result["error"])

        draft_id = result.get("draftId")
        complete_url = result.get("completeUrl")

        if not complete_url:
            raise Exception("Server did not return a completion URL")

        # Mark local asset as pending sync if we have the ID
        if library_asset_id:
            try:
                from sopdrop import library
                library.mark_asset_syncing(library_asset_id, draft_id)
            except Exception as e:
                print(f"[Sopdrop] Could not mark asset as syncing: {e}")

        has_screenshot = "with screenshot" if screenshot_data else "without screenshot"
        hou.ui.displayMessage(
            f"Package uploaded {has_screenshot}!\n\n"
            f"Opening browser to complete your listing...\n\n"
            f"The name, description, and tags have been pre-filled.\n"
            f"Review and click Publish when ready.\n\n"
            f"Draft expires in 24 hours.",
            title="Sopdrop - Upload Complete",
        )

        webbrowser.open(complete_url)

    except hou.OperationInterrupted:
        hou.ui.displayMessage("Upload cancelled.", title="Sopdrop")
    except HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_data = json.loads(error_body)
            msg = error_data.get("error", str(e))
        except:
            msg = f"HTTP {e.code}: {error_body}"
        hou.ui.displayMessage(f"Upload failed:\n\n{msg}", title="Sopdrop - Error", severity=hou.severityType.Error)
    except Exception as e:
        hou.ui.displayMessage(f"Upload failed:\n\n{e}", title="Sopdrop - Error", severity=hou.severityType.Error)


# Entry point - only when run directly, not when imported
if __name__ == "__main__":
    main()
