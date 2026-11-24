"""Make the plugin root importable for the core tests."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# Keep AstrBot's runtime data out of the plugin directory when tests run from
# inside data/plugins/.... The real service sets its own root.
_here = Path(__file__).resolve()
_parts = _here.parents
# Only the in-AstrBot layout (<root>/data/plugins/<plugin>/tests) sets the
# runtime root; a standalone checkout must not point ASTRBOT_ROOT at /home.
if len(_parts) > 4 and _parts[2].name == "plugins" and _parts[3].name == "data":
    os.environ.setdefault("ASTRBOT_ROOT", str(_parts[4]))

sys.path.insert(0, str(_here.parents[1]))
sys.path.insert(0, str(_here.parents[2]))

# A standalone checkout has no AstrBot installed, and main.py imports a few
# runtime names at module load. Fall back to the tiny stub in _astrbot_stub so
# the integration tests still collect; an installed AstrBot always wins.
if importlib.util.find_spec("astrbot") is None:
    sys.path.insert(0, str(_here.parent / "_astrbot_stub"))
