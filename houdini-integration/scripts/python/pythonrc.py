"""
Sopdrop Houdini Startup Script

Initializes the Sopdrop TAB menu with your library assets on startup.

On fresh Houdini:
  - If a shelf file already exists from a previous session, load it immediately
    (fast — no DB or NAS access).
  - Regenerate using personal library only (fast, local SQLite).
  - Team assets are added later when the Library panel opens and the
    background mirror refresh completes.
"""


def _init_sopdrop_menu():
    """Initialize the Sopdrop TAB menu on startup."""
    try:
        from sopdrop import menu
        from sopdrop.config import get_active_library

        shelf_file = menu.get_shelf_file()

        # If a shelf file already exists from a previous session, load it
        # immediately — no DB or NAS access needed.  This covers the common
        # "restart Houdini" case instantly.
        if shelf_file.exists():
            try:
                import hou
                hou.shelves.loadFile(str(shelf_file))
                print(f"[Sopdrop] TAB menu loaded from previous session")
            except Exception:
                pass

        # Regenerate with personal library only (fast, local SQLite).
        # Team assets get added when the Library panel opens.
        is_team = get_active_library() == "team"
        if is_team:
            print("[Sopdrop] Team library active — TAB menu will include team assets after panel opens")

        # skip_team=True avoids NAS/mirror access on the main thread.
        # Team assets get added when the Library panel opens.
        menu.regenerate_menu(quiet=True, skip_team=True)
        print("[Sopdrop] TAB menu ready (type 'sopdrop' in TAB)")

    except ImportError:
        # sopdrop not installed
        pass
    except Exception as e:
        print(f"[Sopdrop] Menu init: {e}")


# Defer initialization until UI is ready
try:
    import hou

    def _deferred_init():
        _init_sopdrop_menu()
        try:
            hou.ui.removeEventLoopCallback(_deferred_init)
        except:
            pass

    hou.ui.addEventLoopCallback(_deferred_init)

except ImportError:
    _init_sopdrop_menu()
except Exception:
    _init_sopdrop_menu()
