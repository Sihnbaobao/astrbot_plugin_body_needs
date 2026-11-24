"""Make the plugin root importable for the core tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Keep AstrBot's runtime data out of the plugin directory when tests run from
# inside data/plugins/.... The real service sets its own root.
_here = Path(__file__).resolve()
# Only the in-AstrBot layout (<root>/data/plugins/<plugin>) has that ancestor;
# a standalone checkout does not, and must not fail before the suite starts.
if len(_here.parents) > 4:
    os.environ.setdefault("ASTRBOT_ROOT", str(_here.parents[4]))

sys.path.insert(0, str(_here.parents[1]))
sys.path.insert(0, str(_here.parents[2]))
