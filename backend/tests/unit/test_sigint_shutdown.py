from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT subprocess behavior is POSIX-specific")
def test_sigint_exits_cleanly_and_cleans_sessions(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    data_dir = tmp_path / "data"
    env = os.environ.copy()
    env["MIGRAAI_DATA_DIR"] = str(data_dir)
    backend_dir = Path(__file__).resolve().parents[2]
    process = subprocess.Popen(
        [sys.executable, "-m", "migraai", "--no-browser", "--port", str(port)],
        cwd=backend_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(
                    f"App exited before becoming ready.\nstdout:\n{stdout}\nstderr:\n{stderr}"
                )
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            process.kill()
            stdout, stderr = process.communicate()
            pytest.fail(f"App did not become ready.\nstdout:\n{stdout}\nstderr:\n{stderr}")

        leftover = data_dir / "sessions" / "sigint-cleanup-probe"
        leftover.mkdir(parents=True)
        (leftover / "marker.txt").write_text("cleanup probe", encoding="utf-8")
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=20)

        assert process.returncode == 0, f"stdout:\n{stdout}\nstderr:\n{stderr}"
        assert "Traceback" not in stderr
        assert list((data_dir / "sessions").iterdir()) == []
        actions = [
            json.loads(line)["action"]
            for line in (data_dir / "logs" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert "app.stop" in actions
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
