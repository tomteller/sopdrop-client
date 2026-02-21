"""
Sopdrop UI Components

Modern PySide2/6 dialogs for Houdini integration.
Styled to match Houdini's dark theme.
"""

import hou

# Try PySide6 first (Houdini 20+), fall back to PySide2
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    PYSIDE_VERSION = 6
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
        PYSIDE_VERSION = 2
    except ImportError:
        QtCore = None
        QtGui = None
        QtWidgets = None
        PYSIDE_VERSION = 0


# Houdini-matching dark theme colors
COLORS = {
    'bg_dark': '#1a1a1a',
    'bg_medium': '#252525',
    'bg_light': '#2d2d2d',
    'bg_hover': '#363636',
    'border': '#3d3d3d',
    'border_light': '#4a4a4a',
    'text': '#e0e0e0',
    'text_dim': '#808080',
    'text_bright': '#ffffff',
    'accent': '#4a9eff',
    'accent_hover': '#5aafff',
    'accent_pressed': '#3a8eef',
    'success': '#4ade80',
    'warning': '#fbbf24',
    'error': '#f87171',
    'sop': '#4a9eff',
    'lop': '#f97316',
    'obj': '#fbbf24',
    'vop': '#a855f7',
    'dop': '#ef4444',
    'cop': '#06b6d4',
    'top': '#22c55e',
    'chop': '#ec4899',
    'rop': '#6366f1',
}

STYLESHEET = f"""
QDialog {{
    background-color: {COLORS['bg_dark']};
    color: {COLORS['text']};
    font-family: "Segoe UI", "SF Pro Display", sans-serif;
}}

QLabel {{
    color: {COLORS['text']};
    font-size: 12px;
}}

QLabel[class="title"] {{
    font-size: 18px;
    font-weight: 600;
    color: {COLORS['text_bright']};
}}

QLabel[class="subtitle"] {{
    font-size: 12px;
    color: {COLORS['text_dim']};
}}

QLabel[class="section"] {{
    font-size: 11px;
    font-weight: 600;
    color: {COLORS['text_dim']};
    text-transform: uppercase;
    letter-spacing: 1px;
}}

QLabel[class="value"] {{
    font-size: 13px;
    color: {COLORS['text_bright']};
}}

QLabel[class="context-badge"] {{
    font-size: 10px;
    font-weight: 600;
    color: white;
    padding: 2px 8px;
    border-radius: 3px;
}}

QPushButton {{
    background-color: {COLORS['bg_light']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {COLORS['bg_hover']};
    border-color: {COLORS['border_light']};
}}

QPushButton:pressed {{
    background-color: {COLORS['bg_medium']};
}}

QPushButton[class="primary"] {{
    background-color: {COLORS['accent']};
    color: white;
    border: none;
}}

QPushButton[class="primary"]:hover {{
    background-color: {COLORS['accent_hover']};
}}

QPushButton[class="primary"]:pressed {{
    background-color: {COLORS['accent_pressed']};
}}

QPushButton:disabled {{
    background-color: {COLORS['bg_medium']};
    color: {COLORS['text_dim']};
    border-color: {COLORS['border']};
}}

QFrame[class="card"] {{
    background-color: {COLORS['bg_medium']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
}}

QFrame[class="divider"] {{
    background-color: {COLORS['border']};
    max-height: 1px;
}}

QProgressBar {{
    background-color: {COLORS['bg_medium']};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {COLORS['accent']};
    border-radius: 4px;
}}

QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background-color: {COLORS['bg_dark']};
    width: 8px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical {{
    background-color: {COLORS['border_light']};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {COLORS['text_dim']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""


def get_context_color(context):
    """Get the color for a Houdini context."""
    return COLORS.get(context.lower(), COLORS['text_dim'])


class PublishDialog(QtWidgets.QDialog):
    """Modern publish dialog for Sopdrop."""

    def __init__(self, items, nodes, netboxes, stickies, parent=None):
        if parent is None:
            parent = hou.qt.mainWindow()
        super().__init__(parent)

        self.items = items
        self.nodes = nodes
        self.netboxes = netboxes
        self.stickies = stickies
        self.result_data = None
        self.upload_mode = None  # Will be set to "new" or "update" when clicked

        # HDA detection
        self.hda_info = None  # Will be set if publishing an HDA
        self.is_hda_mode = False

        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        """Set up the dialog UI."""
        self.setWindowTitle("Publish to Sopdrop")
        self.setMinimumWidth(480)
        self.setStyleSheet(STYLESHEET)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # Header
        header_layout = QtWidgets.QHBoxLayout()

        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setSpacing(4)

        self.title_label = QtWidgets.QLabel("Publish to Sopdrop")
        self.title_label.setProperty("class", "title")
        title_layout.addWidget(self.title_label)

        self.subtitle_label = QtWidgets.QLabel("Share your nodes with the community")
        self.subtitle_label.setProperty("class", "subtitle")
        title_layout.addWidget(self.subtitle_label)

        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        # Context badge
        self.context_badge = QtWidgets.QLabel("SOP")
        self.context_badge.setProperty("class", "context-badge")
        self.context_badge.setFixedHeight(24)
        header_layout.addWidget(self.context_badge)

        layout.addLayout(header_layout)

        # Divider
        divider = QtWidgets.QFrame()
        divider.setProperty("class", "divider")
        divider.setFrameShape(QtWidgets.QFrame.HLine)
        layout.addWidget(divider)

        # Package info card
        card = QtWidgets.QFrame()
        card.setProperty("class", "card")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        # Section label
        section_label = QtWidgets.QLabel("PACKAGE CONTENTS")
        section_label.setProperty("class", "section")
        card_layout.addWidget(section_label)

        # Stats grid
        stats_layout = QtWidgets.QGridLayout()
        stats_layout.setSpacing(16)

        # Nodes
        self.nodes_label = self._create_stat_row("Nodes", "0")
        stats_layout.addWidget(self.nodes_label[0], 0, 0)
        stats_layout.addWidget(self.nodes_label[1], 0, 1)

        # Total (including children)
        self.total_label = self._create_stat_row("Total (with children)", "0")
        stats_layout.addWidget(self.total_label[0], 0, 2)
        stats_layout.addWidget(self.total_label[1], 0, 3)

        # Network boxes
        self.netbox_label = self._create_stat_row("Network Boxes", "0")
        stats_layout.addWidget(self.netbox_label[0], 1, 0)
        stats_layout.addWidget(self.netbox_label[1], 1, 1)

        # Sticky notes
        self.sticky_label = self._create_stat_row("Sticky Notes", "0")
        stats_layout.addWidget(self.sticky_label[0], 1, 2)
        stats_layout.addWidget(self.sticky_label[1], 1, 3)

        stats_layout.setColumnStretch(1, 1)
        stats_layout.setColumnStretch(3, 1)
        card_layout.addLayout(stats_layout)

        # Node types
        self.types_label = QtWidgets.QLabel("")
        self.types_label.setProperty("class", "subtitle")
        self.types_label.setWordWrap(True)
        card_layout.addWidget(self.types_label)

        layout.addWidget(card)

        # HDA Dependencies warning (if any)
        self.deps_card = QtWidgets.QFrame()
        self.deps_card.setProperty("class", "card")
        self.deps_card.setStyleSheet(f"QFrame {{ border-color: {COLORS['warning']}; }}")
        deps_layout = QtWidgets.QVBoxLayout(self.deps_card)
        deps_layout.setContentsMargins(16, 12, 16, 12)
        deps_layout.setSpacing(8)

        deps_header = QtWidgets.QHBoxLayout()
        deps_icon = QtWidgets.QLabel("\u26A0")  # Warning symbol
        deps_icon.setStyleSheet(f"color: {COLORS['warning']}; font-size: 16px;")
        deps_header.addWidget(deps_icon)

        deps_title = QtWidgets.QLabel("HDA Dependencies")
        deps_title.setStyleSheet(f"color: {COLORS['warning']}; font-weight: 600;")
        deps_header.addWidget(deps_title)
        deps_header.addStretch()

        deps_layout.addLayout(deps_header)

        self.deps_list = QtWidgets.QLabel("")
        self.deps_list.setProperty("class", "subtitle")
        self.deps_list.setWordWrap(True)
        deps_layout.addWidget(self.deps_list)

        self.deps_card.hide()
        layout.addWidget(self.deps_card)

        # Screenshot section
        screenshot_card = QtWidgets.QFrame()
        screenshot_card.setProperty("class", "card")
        screenshot_layout = QtWidgets.QVBoxLayout(screenshot_card)
        screenshot_layout.setContentsMargins(16, 12, 16, 12)
        screenshot_layout.setSpacing(8)

        screenshot_header = QtWidgets.QHBoxLayout()
        screenshot_label = QtWidgets.QLabel("SCREENSHOT")
        screenshot_label.setProperty("class", "section")
        screenshot_header.addWidget(screenshot_label)
        screenshot_header.addStretch()

        self.screenshot_status = QtWidgets.QLabel("")
        self.screenshot_status.setProperty("class", "subtitle")
        screenshot_header.addWidget(self.screenshot_status)

        screenshot_layout.addLayout(screenshot_header)

        screenshot_hint = QtWidgets.QLabel(
            "Click 'Take Screenshot' to select a region of your screen, or use an image from clipboard."
        )
        screenshot_hint.setProperty("class", "subtitle")
        screenshot_hint.setWordWrap(True)
        screenshot_layout.addWidget(screenshot_hint)

        # Screenshot buttons
        screenshot_btn_layout = QtWidgets.QHBoxLayout()

        self.snip_btn = QtWidgets.QPushButton("Take Screenshot")
        self.snip_btn.setProperty("class", "primary")
        self.snip_btn.clicked.connect(self._take_screenshot)
        screenshot_btn_layout.addWidget(self.snip_btn)

        self.check_clipboard_btn = QtWidgets.QPushButton("Use from Clipboard")
        self.check_clipboard_btn.clicked.connect(self._check_clipboard_screenshot)
        screenshot_btn_layout.addWidget(self.check_clipboard_btn)

        screenshot_btn_layout.addStretch()
        screenshot_layout.addLayout(screenshot_btn_layout)

        # Preview area for captured screenshot
        self.screenshot_preview = QtWidgets.QLabel()
        self.screenshot_preview.setFixedHeight(120)
        self.screenshot_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.screenshot_preview.setStyleSheet(
            f"background-color: {COLORS['bg_dark']}; "
            f"border: 1px dashed {COLORS['border']}; "
            f"border-radius: 4px;"
        )
        self.screenshot_preview.setText("No screenshot")
        self.screenshot_preview.setProperty("class", "subtitle")
        screenshot_layout.addWidget(self.screenshot_preview)

        layout.addWidget(screenshot_card)

        # Store captured screenshot
        self._captured_screenshot = None

        # Check clipboard on load
        self._check_clipboard_screenshot()

        # Info message
        info_frame = QtWidgets.QFrame()
        info_layout = QtWidgets.QHBoxLayout(info_frame)
        info_layout.setContentsMargins(0, 0, 0, 0)

        info_icon = QtWidgets.QLabel("\u2139")  # Info symbol
        info_icon.setStyleSheet(f"color: {COLORS['accent']}; font-size: 14px;")
        info_layout.addWidget(info_icon)

        info_text = QtWidgets.QLabel(
            "Your browser will open to complete the listing with a name and description."
        )
        info_text.setProperty("class", "subtitle")
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text, 1)

        layout.addWidget(info_frame)

        layout.addStretch()

        # Progress bar (hidden initially)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setProperty("class", "subtitle")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(12)

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        button_layout.addStretch()

        # Update Existing button
        self.update_btn = QtWidgets.QPushButton("Update Existing")
        self.update_btn.setMinimumWidth(130)
        self.update_btn.clicked.connect(lambda: self._on_publish("update"))
        button_layout.addWidget(self.update_btn)

        # New Asset button (primary)
        self.publish_btn = QtWidgets.QPushButton("New Asset")
        self.publish_btn.setProperty("class", "primary")
        self.publish_btn.setMinimumWidth(130)
        self.publish_btn.clicked.connect(lambda: self._on_publish("new"))
        button_layout.addWidget(self.publish_btn)

        layout.addLayout(button_layout)

    def _create_stat_row(self, label, value):
        """Create a label/value pair for stats."""
        label_widget = QtWidgets.QLabel(label)
        label_widget.setProperty("class", "subtitle")

        value_widget = QtWidgets.QLabel(value)
        value_widget.setProperty("class", "value")

        return (label_widget, value_widget)

    def _load_data(self):
        """Load and display package data."""
        from sopdrop.export import detect_hda_dependencies, detect_publishable_hda

        # Check if this is a publishable HDA (single custom HDA node selected)
        self.hda_info = detect_publishable_hda(self.nodes)

        if self.hda_info:
            self._setup_hda_mode()
            return

        # Standard snippet mode
        self._setup_snippet_mode()

    def _setup_hda_mode(self):
        """Configure UI for HDA publishing mode."""
        self.is_hda_mode = True

        # Update header
        self.title_label.setText("Publish HDA")
        self.subtitle_label.setText("Share your Digital Asset with the community")

        # Get context from HDA
        context = self.hda_info['category'].lower()
        context_map = {
            'sop': 'sop', 'object': 'obj', 'vop': 'vop',
            'dop': 'dop', 'cop2': 'cop', 'top': 'top',
            'lop': 'lop', 'chop': 'chop', 'shop': 'shop',
            'rop': 'rop', 'driver': 'rop',
        }
        context = context_map.get(context, context)

        self.context_badge.setText(context.upper())
        self.context_badge.setStyleSheet(
            f"background-color: {get_context_color(context)}; "
            f"color: white; font-size: 10px; font-weight: 600; "
            f"padding: 4px 10px; border-radius: 4px;"
        )

        # Update stats for HDA
        self.nodes_label[0].setText("HDA Type")
        self.nodes_label[1].setText(self.hda_info['type_name'])

        self.total_label[0].setText("Label")
        self.total_label[1].setText(self.hda_info['type_label'] or "—")

        self.netbox_label[0].setText("Version")
        version = self.hda_info.get('version') or "—"
        self.netbox_label[1].setText(str(version))

        self.sticky_label[0].setText("File")
        import os
        file_name = os.path.basename(self.hda_info['library_path'])
        self.sticky_label[1].setText(file_name)

        # Update types label to show library path
        self.types_label.setText(f"Library: {self.hda_info['library_path']}")

        # Hide HDA dependencies card (not relevant for HDA publish)
        self.deps_card.hide()

        # Update button text
        self.publish_btn.setText("Publish HDA")

    def _setup_snippet_mode(self):
        """Configure UI for snippet publishing mode."""
        from sopdrop.export import detect_hda_dependencies

        self.is_hda_mode = False

        # Count items
        total_nodes = len(self.nodes)
        for node in self.nodes:
            total_nodes += len(node.allSubChildren())

        self.nodes_label[1].setText(str(len(self.nodes)))
        self.total_label[1].setText(str(total_nodes))
        self.netbox_label[1].setText(str(len(self.netboxes)))
        self.sticky_label[1].setText(str(len(self.stickies)))

        # Get context
        context = self._get_context()
        self.context_badge.setText(context.upper())
        self.context_badge.setStyleSheet(
            f"background-color: {get_context_color(context)}; "
            f"color: white; font-size: 10px; font-weight: 600; "
            f"padding: 4px 10px; border-radius: 4px;"
        )

        # Get node types
        node_types = set()
        for node in self.nodes:
            node_types.add(node.type().name())

        if node_types:
            types_str = ", ".join(sorted(node_types)[:10])
            if len(node_types) > 10:
                types_str += f" +{len(node_types) - 10} more"
            self.types_label.setText(f"Types: {types_str}")

        # Check HDA dependencies
        deps = detect_hda_dependencies(self.nodes)
        if deps:
            self.deps_card.show()
            deps_names = [d['name'] for d in deps[:5]]
            deps_str = ", ".join(deps_names)
            if len(deps) > 5:
                deps_str += f" +{len(deps) - 5} more"
            self.deps_list.setText(
                f"{len(deps)} custom HDA(s) required: {deps_str}\n"
                "Users will need these installed to use your asset."
            )

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
                'rop': 'rop', 'driver': 'rop',
            }
            return context_map.get(category, category)
        except Exception:
            return 'unknown'

    def _take_screenshot(self):
        """Launch snipping tool to capture a region."""
        # Use setWindowOpacity instead of hide() — hiding a modal dialog on
        # Windows exits the exec_() event loop which closes the dialog entirely.
        self.setWindowOpacity(0)

        # Process events to ensure dialog is visually gone
        QtWidgets.QApplication.processEvents()

        # Small delay to let the screen settle
        QtCore.QTimer.singleShot(300, self._show_snipping_tool)

    def _show_snipping_tool(self):
        """Show the snipping tool overlay."""
        # Process any pending events to ensure screen is stable
        QtWidgets.QApplication.processEvents()

        self.snipping_tool = SnippingTool()
        self.snipping_tool.captured.connect(self._on_screenshot_captured)
        self.snipping_tool.show()
        self.snipping_tool.raise_()
        self.snipping_tool.activateWindow()

    def _on_screenshot_captured(self, image):
        """Handle captured screenshot from snipping tool."""
        # Restore dialog visibility
        self.setWindowOpacity(1)
        self.raise_()
        self.activateWindow()

        if image and not image.isNull():
            self._set_screenshot(image)
        else:
            print("[Sopdrop] Screenshot cancelled or failed")

    def _set_screenshot(self, image):
        """Set the captured screenshot and update preview."""
        self._captured_screenshot = image

        # Update status
        self.screenshot_status.setText(f"✓ Captured ({image.width()}x{image.height()})")
        self.screenshot_status.setStyleSheet(f"color: {COLORS['success']};")

        # Update preview
        pixmap = QtGui.QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            self.screenshot_preview.width() - 10,
            self.screenshot_preview.height() - 10,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation
        )
        self.screenshot_preview.setPixmap(scaled)

    def _check_clipboard_screenshot(self):
        """Check if clipboard has a screenshot and use it."""
        try:
            clipboard = QtWidgets.QApplication.clipboard()
            mime_data = clipboard.mimeData()

            if mime_data.hasImage():
                image = clipboard.image()
                if not image.isNull() and image.width() > 50 and image.height() > 50:
                    self._set_screenshot(image)
                    return
        except Exception as e:
            print(f"[Sopdrop] Clipboard check error: {e}")

        # No valid image in clipboard
        if self._captured_screenshot is None:
            self.screenshot_status.setText("No screenshot")
            self.screenshot_status.setStyleSheet(f"color: {COLORS['text_dim']};")
            self.screenshot_preview.setText("No screenshot")
            self.screenshot_preview.setPixmap(QtGui.QPixmap())  # Clear any existing pixmap

    def _on_publish(self, mode="new"):
        """Handle publish button click."""
        self.upload_mode = mode

        self.publish_btn.setEnabled(False)
        self.update_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.show()
        self.status_label.show()
        self.status_label.setText("Exporting nodes...")

        # Use QTimer to allow UI to update
        QtCore.QTimer.singleShot(100, self._do_export)

    def _do_export(self):
        """Export and upload the package."""
        import json
        import ssl
        import webbrowser
        from sopdrop.export import export_items, export_hda
        from sopdrop.config import get_api_url, get_token
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError

        try:
            # Export - different path for HDA vs snippet
            if self.is_hda_mode and self.hda_info:
                self.status_label.setText("Exporting HDA...")
                package = export_hda(self.hda_info)
            else:
                self.status_label.setText("Exporting nodes...")
                package = export_items(self.items)

            self.status_label.setText("Preparing screenshot...")

            # Capture screenshot
            screenshot_data = self._capture_screenshot()

            self.status_label.setText("Uploading to Sopdrop...")

            token = get_token()
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "sopdrop-houdini/0.1.2",
            }

            # Choose endpoint based on mode
            if self.upload_mode == "update":
                # Version update mode - upload draft, pick asset in browser
                url = f"{get_api_url()}/drafts?mode=version"
                print(f"[Sopdrop] Uploading for version update to: {url}")
            else:
                # New asset mode
                url = f"{get_api_url()}/drafts"
                print(f"[Sopdrop] Uploading new asset to: {url}")

            body_data = {"package": package}
            if screenshot_data:
                body_data["screenshot"] = screenshot_data

            body = json.dumps(body_data).encode("utf-8")
            req = Request(url, data=body, headers=headers, method="POST")

            # Handle SSL - try with verification first, fallback without
            result = self._make_request(req, url)

            if result.get("error"):
                raise Exception(result["error"])

            complete_url = result.get("completeUrl")
            if not complete_url:
                raise Exception("Server did not return a completion URL")

            # Success!
            self.result_data = result
            self.status_label.setText("Opening browser...")

            # Open browser and close dialog
            webbrowser.open(complete_url)
            self.accept()

        except HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_data = json.loads(error_body)
                error_msg = error_data.get("error", str(e))
            except:
                error_msg = f"HTTP {e.code}: {error_body}"
            self._show_error(error_msg)
        except URLError as e:
            self._show_error(f"Connection error: {e.reason}")
        except Exception as e:
            self._show_error(str(e))

    def _capture_screenshot(self):
        """Get the captured screenshot as base64."""
        import base64

        try:
            # Use captured screenshot if available
            image = self._captured_screenshot
            if image is None or image.isNull():
                print("[Sopdrop] No screenshot captured - user can add one in browser")
                return None

            # Convert QImage to PNG bytes
            byte_array = QtCore.QByteArray()
            buffer = QtCore.QBuffer(byte_array)
            buffer.open(QtCore.QIODevice.WriteOnly)
            image.save(buffer, "PNG")
            buffer.close()

            image_data = bytes(byte_array)
            if len(image_data) > 100:
                print(f"[Sopdrop] Screenshot: {len(image_data)} bytes ({image.width()}x{image.height()})")
                return base64.b64encode(image_data).decode('ascii')

            return None

        except Exception as e:
            print(f"[Sopdrop] Screenshot encoding failed: {e}")
            return None

    def _make_request(self, req, url):
        """Make HTTP request with appropriate SSL handling."""
        import json
        import ssl
        from urllib.request import urlopen

        print(f"[Sopdrop] _make_request called with url: {url}")

        # Only use SSL context for HTTPS URLs
        if url.startswith("https://"):
            print("[Sopdrop] Using HTTPS with disabled cert verification")
            # Skip SSL verification - Houdini's Python often has cert issues
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            response = urlopen(req, timeout=120, context=ctx)
        else:
            print("[Sopdrop] Using plain HTTP (no SSL)")
            # Plain HTTP - no SSL context needed
            response = urlopen(req, timeout=120)

        return json.loads(response.read().decode("utf-8"))

    def _show_error(self, message):
        """Show an error state."""
        self.progress_bar.hide()
        self.status_label.setStyleSheet(f"color: {COLORS['error']};")
        self.status_label.setText(f"Error: {message}")
        self.publish_btn.setEnabled(True)
        self.update_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)


class SuccessDialog(QtWidgets.QDialog):
    """Success confirmation dialog."""

    def __init__(self, message, parent=None):
        if parent is None:
            parent = hou.qt.mainWindow()
        super().__init__(parent)

        self.setWindowTitle("Success")
        self.setFixedWidth(400)
        self.setStyleSheet(STYLESHEET)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Success icon
        icon = QtWidgets.QLabel("\u2713")  # Checkmark
        icon.setStyleSheet(f"color: {COLORS['success']}; font-size: 48px;")
        icon.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(icon)

        # Message
        msg = QtWidgets.QLabel(message)
        msg.setProperty("class", "value")
        msg.setAlignment(QtCore.Qt.AlignCenter)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        # OK button
        btn = QtWidgets.QPushButton("OK")
        btn.setProperty("class", "primary")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


class SnippingTool(QtWidgets.QWidget):
    """Fullscreen overlay for selecting a screen region to capture."""

    captured = QtCore.Signal(object)  # Emits QImage or None

    def __init__(self):
        super().__init__()

        # Selection state
        self.start_pos = None
        self.end_pos = None
        self.is_selecting = False
        self.screen_pixmap = None
        self.screen_offset = QtCore.QPoint(0, 0)
        self.device_pixel_ratio = 1.0
        self.screen_geom = None

        # Take a screenshot FIRST, before setting up the window
        self._capture_screen()

        # Make fullscreen transparent overlay
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

        # Position window to cover the screen
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            geom = screen.geometry()
            self.setGeometry(geom)
            print(f"[Sopdrop] Snipping tool geometry: {geom.x()}, {geom.y()}, {geom.width()}x{geom.height()}")
        elif self.screen_pixmap:
            self.setGeometry(
                self.screen_offset.x(),
                self.screen_offset.y(),
                self.screen_pixmap.width(),
                self.screen_pixmap.height()
            )
        else:
            # Fallback
            self.setGeometry(0, 0, 1920, 1080)

        self.setCursor(QtCore.Qt.CrossCursor)

    def _capture_screen(self):
        """Capture the entire screen before showing overlay."""
        try:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen:
                # Get the screen geometry (logical pixels)
                geom = screen.geometry()
                self.screen_offset = geom.topLeft()
                self.screen_geom = geom

                # Get device pixel ratio for HiDPI/Retina displays
                self.device_pixel_ratio = screen.devicePixelRatio()

                # Capture the screen (returns pixmap in device pixels on HiDPI)
                self.screen_pixmap = screen.grabWindow(0)

                print(f"[Sopdrop] Captured screen: pixmap {self.screen_pixmap.width()}x{self.screen_pixmap.height()}, "
                      f"window will be {geom.width()}x{geom.height()}, DPR: {self.device_pixel_ratio}")
            else:
                print("[Sopdrop] No primary screen found")
                self.screen_pixmap = None
                self.device_pixel_ratio = 1.0
                self.screen_geom = None
        except Exception as e:
            print(f"[Sopdrop] Screen capture error: {e}")
            import traceback
            traceback.print_exc()
            self.screen_pixmap = None
            self.device_pixel_ratio = 1.0
            self.screen_geom = None

    def paintEvent(self, event):
        """Draw the overlay and selection rectangle."""
        painter = QtGui.QPainter(self)
        dpr = self.device_pixel_ratio

        # Draw the captured screen as background
        if self.screen_pixmap:
            # Draw pixmap scaled to fill window (handles HiDPI)
            target_rect = self.rect()
            source_rect = self.screen_pixmap.rect()
            painter.drawPixmap(target_rect, self.screen_pixmap, source_rect)
        else:
            # Fallback: fill with dark color
            painter.fillRect(self.rect(), QtGui.QColor(30, 30, 30))

        # Draw semi-transparent dark overlay on top
        overlay = QtGui.QColor(0, 0, 0, 120)
        painter.fillRect(self.rect(), overlay)

        # If selecting, cut out the selection area to show original screen
        if self.start_pos and self.end_pos:
            selection = QtCore.QRect(self.start_pos, self.end_pos).normalized()

            # Draw the original screen content in the selection area
            if self.screen_pixmap and selection.width() > 0 and selection.height() > 0:
                # Scale selection to pixmap coordinates (for HiDPI)
                source_rect = QtCore.QRect(
                    int(selection.x() * dpr),
                    int(selection.y() * dpr),
                    int(selection.width() * dpr),
                    int(selection.height() * dpr)
                )
                painter.drawPixmap(selection, self.screen_pixmap, source_rect)

            # Draw selection border
            pen = QtGui.QPen(QtGui.QColor(COLORS['accent']), 2)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRect(selection)

            # Draw size indicator with background for readability
            size_text = f"{selection.width()} x {selection.height()}"

            # Position text below selection or above if near bottom
            text_x = selection.center().x() - 35
            text_y = selection.bottom() + 25
            if text_y > self.height() - 30:
                text_y = selection.top() - 10

            # Draw text background
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(0, 0, 0, 180))
            painter.drawRoundedRect(text_x - 5, text_y - 15, 80, 22, 4, 4)

            # Draw text
            painter.setPen(QtGui.QColor(255, 255, 255))
            painter.setFont(QtGui.QFont("Arial", 11))
            painter.drawText(text_x, text_y, size_text)

        # Draw instructions at top
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 180))
        painter.drawRoundedRect(10, 10, 350, 30, 6, 6)

        painter.setPen(QtGui.QColor(255, 255, 255))
        painter.setFont(QtGui.QFont("Arial", 12))
        painter.drawText(20, 30, "Drag to select region  •  Press Escape to cancel")

    def mousePressEvent(self, event):
        """Start selection."""
        if event.button() == QtCore.Qt.LeftButton:
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        """Update selection."""
        if self.is_selecting:
            self.end_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        """Complete selection and capture."""
        if event.button() == QtCore.Qt.LeftButton and self.is_selecting:
            self.is_selecting = False
            self.end_pos = event.pos()

            # Get the selection rectangle (in window/logical coordinates)
            selection = QtCore.QRect(self.start_pos, self.end_pos).normalized()

            # Minimum size check
            if selection.width() > 10 and selection.height() > 10:
                # Capture the selected region from our screen pixmap
                if self.screen_pixmap:
                    dpr = self.device_pixel_ratio

                    # Scale selection to pixmap coordinates (for HiDPI)
                    source_rect = QtCore.QRect(
                        int(selection.x() * dpr),
                        int(selection.y() * dpr),
                        int(selection.width() * dpr),
                        int(selection.height() * dpr)
                    )

                    cropped = self.screen_pixmap.copy(source_rect)
                    self.captured.emit(cropped.toImage())
                else:
                    self.captured.emit(None)
            else:
                self.captured.emit(None)

            self.close()

    def keyPressEvent(self, event):
        """Handle escape key."""
        if event.key() == QtCore.Qt.Key_Escape:
            self.captured.emit(None)
            self.close()


def show_publish_dialog(items, nodes, netboxes, stickies):
    """Show the publish dialog and return result."""
    if QtWidgets is None:
        raise ImportError("PySide2/6 not available")

    dialog = PublishDialog(items, nodes, netboxes, stickies)
    result = dialog.exec_()

    if result == QtWidgets.QDialog.Accepted:
        return dialog.result_data
    return None


def show_success(message):
    """Show a success message."""
    if QtWidgets is None:
        hou.ui.displayMessage(message, title="Success")
        return

    dialog = SuccessDialog(message)
    dialog.exec_()


def show_error(message):
    """Show an error message with modern styling."""
    if QtWidgets is None:
        hou.ui.displayMessage(message, title="Error", severity=hou.severityType.Error)
        return

    QtWidgets.QMessageBox.critical(
        hou.qt.mainWindow(),
        "Error",
        message
    )
