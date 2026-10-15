from __future__ import annotations

import os
from pathlib import Path

APP_VERSION = "0.1.0"


def get_data_root() -> Path:
    configured = os.environ.get("MIGRAAI_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local_app_data / "MigraAI"
    return Path.home() / ".local" / "share" / "MigraAI"
