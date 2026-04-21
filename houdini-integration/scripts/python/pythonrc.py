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

import sys
import os

# Ensure sopdrop directories are on sys.path before importing.
# Without this, `import sopdrop` fails because Houdini's HOUDINI_PATH
# only adds scripts/python/ — not the client package directory.
# This matches the path setup done by the pypanel and shelf tools.
_sopdrop_houdini = os.environ.get("SOPDROP_HOUDINI_PATH", "")
if _sopdrop_houdini:
    for _subdir in ("scripts", "client"):
        _p = os.path.join(_sopdrop_houdini, _subdir)
        if _p not in sys.path:
            sys.path.insert(0, _p)


def _init_sopdrop_menu():
    """Initialize the Sopdrop TAB menu on startup."""
    try:
        from sopdrop import menu
        from sopdrop.config import get_active_library, get_team_library_path

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
        # skip_team=True avoids NAS/mirror access on the main thread.
        menu.regenerate_menu(quiet=True, skip_team=True)
        print("[Sopdrop] TAB menu ready (type 'sopdrop' in TAB)")

        # If a team library is configured, kick off a background mirror
        # refresh + menu regen so team assets show up in the TAB menu
        # without requiring the user to open the library panel first.
        if get_team_library_path():
            import threading
            threading.Thread(target=_deferred_team_sync, daemon=True).start()

    except ImportError:
        # sopdrop not installed
        pass
    except Exception as e:
        print(f"[Sopdrop] Menu init: {e}")


def _deferred_team_sync():
    """Background: refresh team mirror, then schedule menu regen on main thread.

    Mirror refresh is NAS I/O and must stay off the main thread.  Menu
    regeneration calls hou.shelves.loadFile which must run on the main thread,
    so it is posted via hou.ui.addEventLoopCallback as a one-shot.
    """
    try:
        from sopdrop import library
        try:
            library.refresh_team_mirror()
        except Exception as e:
            print(f"[Sopdrop] Team mirror refresh failed: {e}")
            return

        try:
            import hou
        except ImportError:
            return

        def _regen_on_main():
            try:
                from sopdrop import menu
                menu.regenerate_menu(quiet=True, skip_team=False)
                print("[Sopdrop] TAB menu updated with team assets")
            except Exception as e:
                print(f"[Sopdrop] Team menu regen failed: {e}")
            finally:
                try:
                    hou.ui.removeEventLoopCallback(_regen_on_main)
                except Exception:
                    pass

        hou.ui.addEventLoopCallback(_regen_on_main)
    except Exception as e:
        print(f"[Sopdrop] Team sync error: {e}")


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
