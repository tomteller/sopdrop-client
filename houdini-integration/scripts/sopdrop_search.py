"""
Sopdrop Search Tool

Search the Sopdrop registry for assets.
"""

import hou


def main():
    """Main entry point for the search tool."""
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

    # Show search dialog
    dialog = SearchDialog()
    dialog.show()


class SearchDialog:
    """Dialog for searching Sopdrop."""

    def __init__(self):
        self.results = []
        self.current_query = ""

    def show(self):
        """Show the search dialog."""
        # Get current context for filtering
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        current_context = None
        if pane:
            current_context = self._get_current_context(pane)

        # Build context options
        contexts = ["All Contexts", "SOP", "VOP", "DOP", "LOP", "TOP", "CHOP", "COP", "OBJ"]
        context_values = [None, "sop", "vop", "dop", "lop", "top", "chop", "cop", "obj"]

        # Default to current context
        default_context = 0
        if current_context:
            ctx_upper = current_context.upper()
            if ctx_upper in contexts:
                default_context = contexts.index(ctx_upper)

        # Show search input
        result = hou.ui.readMultiInput(
            "Search the Sopdrop registry:",
            input_labels=("Search:", "Context:"),
            buttons=("Search", "Browse Popular", "Cancel"),
            default_choice=0,
            close_choice=2,
            title="Sopdrop - Search",
            initial_contents=("", contexts[default_context]),
        )

        if result[0] == 2:
            return

        query = result[1][0].strip()
        context_input = result[1][1].strip()

        # Parse context
        context = None
        if context_input and context_input != "All Contexts":
            context = context_input.lower()

        if result[0] == 1:
            # Browse popular
            self._browse_popular(context)
        elif query:
            self._search(query, context)
        else:
            hou.ui.displayMessage(
                "Enter a search term.",
                title="Sopdrop",
            )

    def _search(self, query, context=None):
        """Search for assets."""
        import sopdrop

        try:
            with hou.InterruptableOperation("Searching Sopdrop..."):
                self.results = sopdrop.search(query, context=context)
                self.current_query = query
        except Exception as e:
            hou.ui.displayMessage(
                f"Search failed: {e}",
                title="Sopdrop - Error",
                severity=hou.severityType.Error,
            )
            return

        self._show_results(f"Results for '{query}'")

    def _browse_popular(self, context=None):
        """Browse popular assets."""
        import sopdrop
        from sopdrop.api import SopdropClient

        try:
            client = SopdropClient()
            with hou.InterruptableOperation("Loading popular assets..."):
                params = {"sort": "downloads", "limit": 50}
                if context:
                    params["context"] = context

                from urllib.parse import urlencode
                response = client._get(f"assets?{urlencode(params)}", auth=False)
                self.results = response.get("assets", [])
                self.current_query = "Popular"
        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to load: {e}",
                title="Sopdrop - Error",
                severity=hou.severityType.Error,
            )
            return

        self._show_results("Popular Assets")

    def _show_results(self, title):
        """Show search results."""
        if not self.results:
            hou.ui.displayMessage(
                f"No results found.",
                title="Sopdrop - No Results",
            )
            return

        # Build result list with details
        choices = []
        for asset in self.results:
            slug = asset.get("slug", "")
            name = asset.get("name", slug)
            owner = asset.get("owner", "")
            context = asset.get("context", "").upper()
            downloads = asset.get("downloadCount", 0)
            version = asset.get("latestVersion", "")

            # Format: Name (CONTEXT) by owner - v1.0.0 - 123 downloads
            label = f"{name} ({context}) by {owner}"
            if version:
                label += f" - v{version}"
            label += f" - {downloads} downloads"

            choices.append(label)

        # Show results
        selected = hou.ui.selectFromList(
            choices,
            exclusive=True,
            title=f"Sopdrop - {title}",
            message=f"Found {len(self.results)} assets. Select to view details:",
            width=600,
            height=500,
        )

        if selected:
            asset = self.results[selected[0]]
            self._show_asset_details(asset)

    def _show_asset_details(self, asset):
        """Show details for an asset with action buttons."""
        slug = asset.get("slug", "")
        name = asset.get("name", slug)
        owner = asset.get("owner", "")
        context = asset.get("context", "").upper()
        description = asset.get("description", "No description")
        downloads = asset.get("downloadCount", 0)
        version = asset.get("latestVersion", "1.0.0")
        license = asset.get("license", "unknown")
        tags = asset.get("tags", [])

        details = f"""Asset: {name}
By: {owner}
Context: {context}
Version: {version}
License: {license}
Downloads: {downloads}

{description}"""

        if tags:
            details += f"\n\nTags: {', '.join(tags)}"

        result = hou.ui.displayMessage(
            details,
            buttons=("Paste", "View Code", "Copy Slug", "Back", "Close"),
            default_choice=0,
            close_choice=4,
            title=f"Sopdrop - {name}",
        )

        if result == 0:
            # Paste
            self._paste_asset(slug)
        elif result == 1:
            # View code
            self._view_code(slug)
        elif result == 2:
            # Copy slug
            self._copy_to_clipboard(slug)
            hou.ui.displayMessage(
                f"Copied: {slug}",
                title="Sopdrop",
            )
        elif result == 3:
            # Back to results
            self._show_results(f"Results for '{self.current_query}'")

    def _paste_asset(self, slug):
        """Paste an asset."""
        import sopdrop

        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if not pane:
            hou.ui.displayMessage(
                "No network editor found.",
                title="Sopdrop",
                severity=hou.severityType.Error,
            )
            return

        try:
            with hou.InterruptableOperation(f"Pasting {slug}..."):
                sopdrop.paste(slug, trust=True)

            hou.ui.displayMessage(
                f"Pasted: {slug}",
                title="Sopdrop - Success",
            )
        except Exception as e:
            hou.ui.displayMessage(
                f"Paste failed: {e}",
                title="Sopdrop - Error",
                severity=hou.severityType.Error,
            )

    def _view_code(self, slug):
        """View asset code."""
        import sopdrop

        try:
            result = sopdrop.install(slug)
            if result["type"] == "hda":
                hou.ui.displayMessage(
                    "This is an HDA. Use Type Properties to inspect.",
                    title="Sopdrop",
                )
                return

            package = result["package"]
            code = package.get("code", "")
            meta = package.get("metadata", {})

            # Build info header
            header = f"""Nodes: {meta.get('node_count', '?')}
Node Types: {', '.join(meta.get('node_types', []))}
Has Expressions: {meta.get('has_expressions', False)}
Has Python SOPs: {meta.get('has_python_sops', False)}

--- Code ---
"""
            lines = code.split("\n")
            preview = "\n".join(lines[:80])
            if len(lines) > 80:
                preview += f"\n\n... ({len(lines) - 80} more lines)"

            hou.ui.displayMessage(
                header + preview,
                title=f"Code: {slug}",
            )
        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to load: {e}",
                title="Sopdrop - Error",
                severity=hou.severityType.Error,
            )

    def _copy_to_clipboard(self, text):
        """Copy text to system clipboard."""
        try:
            import subprocess
            import sys

            if sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text.encode(), check=True)
            elif sys.platform == "win32":
                subprocess.run(["clip"], input=text.encode(), check=True)
            else:
                subprocess.run(["xclip", "-selection", "clipboard"],
                             input=text.encode(), check=True)
        except Exception:
            pass

    def _get_current_context(self, pane):
        """Get current network context."""
        try:
            parent = pane.pwd()
            category = parent.childTypeCategory().name().lower()
            context_map = {
                'sop': 'sop',
                'object': 'obj',
                'vop': 'vop',
                'dop': 'dop',
                'cop2': 'cop',
                'top': 'top',
                'lop': 'lop',
                'chop': 'chop',
            }
            return context_map.get(category, category)
        except Exception:
            return None


# Entry point - only when run directly, not when imported
if __name__ == "__main__":
    main()
