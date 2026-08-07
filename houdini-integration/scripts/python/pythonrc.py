"""
Sopdrop Houdini Startup Script

Initializes the Sopdrop TAB menu with your library assets on startup.

Why this runs synchronously and never calls hou.shelves.loadFile() at startup
----------------------------------------------------------------------------
The shelf file lives in toolbar/ under a HOUDINI_PATH entry (see sopdrop.json),
so Houdini's own startup toolbar scan loads it natively and binds the tools as
first-class, droppable TAB-menu tools. We just have to make sure the file is
written to disk *before* that scan runs — hence this regenerates the file
synchronously at interpreter init (skip_reload=True → content only, no live
shelf manipulation, no UI calls).

We deliberately do NOT call hou.shelves.loadFile() here. The previous version
deferred a loadFile() (plus a destroy()+reload) to the first event-loop tick,
before the shelf subsystem had finished initializing. That registered the tools
as searchable but left them *unbound* — they showed up in the TAB menu but
dropping them did nothing until opening the Library panel forced a second, late
reload. Letting the native scan own the binding fixes that at the source.

Personal assets (local SQLite) are written synchronously. Team assets (NAS) are
folded in later by a background mirror refresh, which does the only in-session
loadFile — safely, after the UI has fully settled.
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
    """Write the TAB-menu shelf file so Houdini's native toolbar scan binds it.

    Content-only regeneration (skip_reload=True): reads the local SQLite
    personal library and writes the shelf file. No hou.shelves calls, no UI
    access — safe to run synchronously at interpreter init, which is what lets
    the file land on disk before Houdini scans the toolbar/ directory.
    """
    try:
        from sopdrop import menu
        from sopdrop.config import is_team_library_configured

        # Regenerate CONTENT ONLY with personal library (fast, local SQLite).
        # skip_reload=True → do not touch the live shelf; the native toolbar
        # scan loads + binds the file. skip_team=True avoids NAS/network here.
        # (When native TAB recipes are disabled — the default, Shift+Tab menu
        # instead — this writes a browse-only shelf.)
        menu.regenerate_menu(quiet=True, skip_reload=True, skip_team=True)

        # If a team library is configured (NAS path OR HTTP mode + slug),
        # kick off a background sync + menu regen so team assets are
        # available without requiring the user to open the Library panel
        # first. Previously this was gated on get_team_library_path() only,
        # so HTTP-mode teams never synced at startup — team recipes didn't
        # load/work until the Library panel was opened. The regen does the
        # only in-session loadFile — after the UI has fully settled, so the
        # tools bind correctly (the same path the Library panel uses).
        if is_team_library_configured():
            import threading
            threading.Thread(target=_deferred_team_sync, daemon=True).start()

    except ImportError:
        # sopdrop not installed
        pass
    except Exception as e:
        print(f"[Sopdrop] Menu init: {e}")


def _deferred_team_sync():
    """Background: warm the team library, then schedule menu regen on main thread.

    NAS mode: mirror refresh is NAS I/O and must stay off the main thread.
    HTTP mode: fetch the team snapshot over HTTP (network I/O, also off the
    main thread) — this primes both the in-process ETag cache and the
    persistent disk mirror, so the later main-thread menu regen (and the
    Shift+Tab menu) are served from cache instead of blocking on the network.

    Menu regeneration calls hou.shelves.loadFile which must run on the main
    thread, so it is posted via hou.ui.addEventLoopCallback as a one-shot.
    """
    try:
        from sopdrop.config import get_team_library_mode
        if get_team_library_mode() == 'http':
            try:
                # Direct fetch — does NOT touch the active-library global,
                # so it can't race user actions on the main thread.
                from sopdrop import _team_http
                _team_http.get_all_assets_cached()
            except Exception as e:
                print(f"[Sopdrop] Team library warm-up failed: {e}")
                return
        else:
            from sopdrop import library
            try:
                library.refresh_team_mirror()
            except Exception as e:
                print(f"[Sopdrop] Team mirror refresh failed: {e}")
                return

        # Nothing to fold into the native TAB menu when recipes are disabled
        # there — the Shift+Tab menu reads the (now warm) caches directly.
        from sopdrop.config import get_tab_menu_enabled
        if not get_tab_menu_enabled():
            return

        try:
            import hou
        except ImportError:
            return

        def _regen_on_main():
            try:
                from sopdrop import menu
                menu.regenerate_menu(quiet=True, skip_team=False)
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


# The TAB menu is a UI-only feature. In hython / `houdini -b` (render farm,
# scene-processing tools, headless pipelines) there is no TAB menu, and running
# the startup work there is pure liability: it touches the library DB, can spawn
# a NAS team-sync thread, and prints to stdout — any of which can hang, error,
# or corrupt output that a wrapping tool parses, breaking the headless launch.
# hou.isUIAvailable() reflects the *launch mode* (True only for graphical
# Houdini) and is valid this early in startup, so it's the right gate.
#
# When the UI is available, run synchronously at interpreter init so the shelf
# file lands on disk BEFORE Houdini's toolbar scan — the native scan then loads
# and binds the tools as droppable TAB-menu tools. skip_reload=True means no
# UI/shelf work, so it's safe to run here without waiting for the event loop.
try:
    import hou
    _ui_available = hou.isUIAvailable()
except Exception:
    _ui_available = False

if _ui_available:
    _init_sopdrop_menu()
