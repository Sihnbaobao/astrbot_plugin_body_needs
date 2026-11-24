from __future__ import annotations

import os
import tempfile
from pathlib import Path


def get_astrbot_plugin_data_path() -> str:
    configured = os.environ.get("ASTRBOT_PLUGIN_DATA_PATH")
    if configured:
        return configured
    return str(Path(tempfile.gettempdir()) / "astrbot_stub_data")
