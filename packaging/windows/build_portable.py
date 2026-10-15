from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.1.0"
PYTHON_VERSION = "3.12.10"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
)
OUT_DIR = Path(__file__).resolve().parent / "out"
APP_DIR = OUT_DIR / "MigraAI"


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, cwd=ROOT)


def install_pip() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        run([sys.executable, "-m", "ensurepip", "--upgrade"])


def download_python(destination: Path) -> None:
    request = urllib.request.Request(PYTHON_EMBED_URL, method="HEAD")
    with urllib.request.urlopen(request, timeout=60):
        pass
    archive = destination / "python-embed-amd64.zip"
    urllib.request.urlretrieve(PYTHON_EMBED_URL, archive)
    with zipfile.ZipFile(archive) as embedded:
        embedded.extractall(destination)
    archive.unlink()


def configure_embedded_python(python_dir: Path) -> None:
    path_file = python_dir / "python312._pth"
    lines = path_file.read_text(encoding="utf-8").splitlines()
    for entry in (r"..\app", r"..\app\site-packages"):
        if entry not in lines:
            lines.append(entry)
    lines = ["import site" if line.strip() == "#import site" else line for line in lines]
    if "import site" not in lines:
        lines.append("import site")
    path_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_backend_wheel(destination: Path) -> Path:
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(destination),
            str(ROOT / "backend"),
        ]
    )
    wheels = list(destination.glob("migraai-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one MigraAI wheel, found {len(wheels)}.")
    return wheels[0]


def install_windows_dependencies(wheel: Path, site_packages: Path) -> None:
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(site_packages),
            "--platform",
            "win_amd64",
            "--python-version",
            "3.12",
            "--implementation",
            "cp",
            "--only-binary=:all:",
            "--no-compile",
            str(wheel),
        ]
    )


def write_launch_files() -> None:
    (APP_DIR / "start.cmd").write_text(
        '"%~dp0python\\python.exe" -m migraai --web-dir "%~dp0web"\r\n',
        encoding="utf-8",
    )
    (APP_DIR / "README.txt").write_text(
        "MigraAI portable for Windows x64\n"
        "\n"
        "Run start.cmd as a standard user. The local app binds to 127.0.0.1.\n"
        "Application data is stored under %LOCALAPPDATA%\\MigraAI.\n",
        encoding="utf-8",
    )


def create_archive() -> Path:
    archive_path = OUT_DIR / f"MigraAI-portable-{VERSION}-win64.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in APP_DIR.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(OUT_DIR))
    return archive_path


def main() -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError("Build with Python 3.12 or newer.")
    if os.name == "nt" and sys.maxsize <= 2**32:
        raise RuntimeError("Build requires 64-bit Python on Windows.")
    frontend_dist = ROOT / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        raise FileNotFoundError("Build frontend/dist before creating the portable package.")

    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    (APP_DIR / "python").mkdir(parents=True)
    site_packages = APP_DIR / "app" / "site-packages"
    site_packages.mkdir(parents=True)
    (APP_DIR / "web").mkdir()

    download_python(APP_DIR / "python")
    configure_embedded_python(APP_DIR / "python")
    shutil.copytree(frontend_dist, APP_DIR / "web", dirs_exist_ok=True)
    install_pip()
    with TemporaryDirectory(prefix="migraai-build-", dir=OUT_DIR) as scratch:
        wheel = build_backend_wheel(Path(scratch))
        install_windows_dependencies(wheel, site_packages)
    write_launch_files()
    archive = create_archive()
    print(f"Created {archive} ({archive.stat().st_size / 1024**2:.2f} MiB)")


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    main()
