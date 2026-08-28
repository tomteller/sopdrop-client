"""
Sopdrop network editor hook — loader shim.

Houdini's network editor resolves `import nodegraphhooks` against sys.path,
which reliably includes the `pythonX.Ylibs` directory of every HOUDINI_PATH
entry but NOT `scripts/python` (Houdini *executes* startup scripts from there
without guaranteeing the directory is importable). So a copy has to exist per
Houdini Python version:

    H19.5 = 3.9    H20.0 = 3.10    H20.5/21 = 3.11    H22 = 3.13

These files are loader shims rather than copies of the handler, so there is
only ever one implementation to change — supporting a new Houdini Python
version means dropping this same shim into a new pythonX.Ylibs directory.

The implementation lives in ../scripts/python/nodegraphhooks.py, located
relative to this file so no environment variable has to be set for the lookup
to work.
"""

import os
import runpy
import traceback

_CANONICAL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "python", "nodegraphhooks.py",
)

try:
    createEventHandler = runpy.run_path(_CANONICAL)["createEventHandler"]
except Exception:
    # Never break the network editor over a Sopdrop problem.
    traceback.print_exc()

    def createEventHandler(uievent, pending_actions):
        return None, False
