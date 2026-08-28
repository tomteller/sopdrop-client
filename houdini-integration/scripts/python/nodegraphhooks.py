"""
Sopdrop network editor event hooks.

Houdini's network editor imports a module named `nodegraphhooks` (first
one found on the Python path) and calls createEventHandler() for every
UI event before its default handling. This file lives in
$SOPDROP_HOUDINI_PATH/scripts/python, which Houdini puts on sys.path via
the sopdrop package's HOUDINI_PATH entry.

Sopdrop uses it for exactly one binding:

    Shift+Tab  →  open the Sopdrop recipe menu (sopdrop/tabmenu.py), a
                  searchable popup of all personal + team recipes for the
                  current network context. This is Sopdrop's own TAB menu,
                  so recipes no longer need to pollute the native TAB menu.

IMPORTANT — file placement: Houdini's network editor resolves
`import nodegraphhooks` against sys.path, which reliably includes the
`pythonX.Ylibs` directories of every HOUDINI_PATH entry but NOT
necessarily `scripts/python` (Houdini *executes* startup scripts from
here without guaranteeing the directory is importable). Loader shims
therefore live in python3.9libs/ .. python3.14libs/ at the package root
— one per Houdini Python version (H19.5=3.9, H20.0=3.10, H20.5/21=3.11,
H22=3.13) — and those shims run THIS file. Supporting a new Houdini
Python version means copying a shim into a new pythonX.Ylibs directory;
the handler itself only exists here.

NOTE for studios with their own nodegraphhooks.py: Houdini only imports
ONE nodegraphhooks module (first on the path). If you already ship one,
merge this handler into yours — it's just the Shift+Tab branch below
calling sopdrop.tabmenu.show_tab_menu(editor).

Everything is wrapped defensively: any failure falls through to
(None, False) so Houdini's default event handling is never broken by a
Sopdrop problem.
"""

import os
import sys
import traceback

# Shift+Tab arrives as 'Shift+Tab' on most platforms; some Qt platforms
# report the shifted Tab key as Backtab. Compared case-insensitively.
_SOPDROP_MENU_KEYS = ("shift+tab", "shift+backtab", "backtab")


def _ensure_sopdrop_on_path():
    """Same path setup as the shelf tools/pypanel — makes `import sopdrop`
    work even if pythonrc hasn't run (e.g. stripped-down launch configs)."""
    sopdrop_path = os.environ.get("SOPDROP_HOUDINI_PATH", "")
    if sopdrop_path:
        for subdir in ("scripts", "client"):
            p = os.path.join(sopdrop_path, subdir)
            if p not in sys.path:
                sys.path.insert(0, p)


def createEventHandler(uievent, pending_actions):
    """Houdini nodegraph hook entry point.

    Returns (handler, handled). We never install a persistent handler —
    the Shift+Tab popup is fire-and-forget — so handler is always None.
    """
    try:
        key = getattr(uievent, "key", None)
        if (
            getattr(uievent, "eventtype", None) == "keyhit"
            and key
            and key.lower() in _SOPDROP_MENU_KEYS
        ):
            _ensure_sopdrop_on_path()
            from sopdrop import tabmenu
            tabmenu.show_tab_menu(editor=getattr(uievent, "editor", None))
            return None, True
    except Exception:
        # Never break the network editor over a Sopdrop error.
        traceback.print_exc()
    return None, False
