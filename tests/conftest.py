"""Make the plugin root importable for the core tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Keep AstrBot's runtime data out of the plugin directory when tests run from
# inside data/plugins/.... The real service sets its own root.
os.environ.setdefault(
    "ASTRBOT_ROOT", str(Path(__file__).resolve().parents[4])
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
