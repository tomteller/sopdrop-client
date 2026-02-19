"""
Sopdrop Houdini Startup Script

Initializes the Sopdrop TAB menu with your library assets on startup.
"""


def _init_sopdrop_menu():
    """Initialize the Sopdrop TAB menu on startup."""
    try:
        from sopdrop.library import get_library_stats
        from sopdrop import menu

        stats = get_library_stats()
        asset_count = stats.get('asset_count', 0)

        if asset_count > 0:
            menu.regenerate_menu(quiet=True)
            print(f"[Sopdrop] TAB menu ready: {asset_count} assets (type 'sopdrop' in TAB)")
        else:
            # Still create browse tools
            menu.regenerate_menu(quiet=True)
            print("[Sopdrop] TAB menu ready (no assets yet - save some to your library!)")

    except ImportError as e:
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
