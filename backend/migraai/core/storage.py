from __future__ import annotations

import getpass
import logging
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

from migraai.core.config import get_data_root

logger = logging.getLogger(__name__)


def _restrict_to_current_user(path: Path) -> None:
    if os.name == "nt":
        username = os.environ.get("USERNAME") or getpass.getuser()
        try:
            subprocess.run(
                [
                    "icacls",
                    str(path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{username}:(OI)(CI)F",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            logger.warning("Could not restrict session directory ACL for %s: %s", path, exc)
    else:
        try:
            path.chmod(0o700)
        except OSError as exc:
            logger.warning("Could not restrict session directory permissions for %s: %s", path, exc)


def create_session_dir(data_root: Path | None = None) -> Path:
    root = (data_root or get_data_root()).expanduser()
    sessions_dir = root / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_dir = sessions_dir / str(uuid.uuid4())
    session_dir.mkdir()
    _restrict_to_current_user(session_dir)
    return session_dir


def _make_writable(function: object, path: str, _: object) -> None:
    target = Path(path)
    try:
        target.chmod(target.stat().st_mode | stat.S_IWRITE)
        function(target)
    except OSError as exc:
        logger.warning("Could not delete session file %s: %s", target, exc)


def wipe_sessions(data_root: Path | None = None) -> None:
    sessions_dir = (data_root or get_data_root()).expanduser() / "sessions"
    if not sessions_dir.exists():
        return
    for path in sessions_dir.iterdir():
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, onerror=_make_writable)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not wipe session path %s: %s", path, exc)
