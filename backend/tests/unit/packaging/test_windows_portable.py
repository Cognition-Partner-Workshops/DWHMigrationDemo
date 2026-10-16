from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

BUILD_SCRIPT = Path(__file__).resolve().parents[4] / "packaging" / "windows" / "build_portable.py"
_SPEC = importlib.util.spec_from_file_location("migraai_portable_build", BUILD_SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
portable_build = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(portable_build)


def test_download_msvc_runtime_verifies_sha256(tmp_path: Path, monkeypatch) -> None:
    wheel_bytes = b"pinned-wheel-content"
    monkeypatch.setattr(
        portable_build.urllib.request,
        "urlretrieve",
        lambda _url, target: Path(target).write_bytes(wheel_bytes),
    )
    monkeypatch.setattr(
        portable_build, "MSVC_RUNTIME_SHA256", hashlib.sha256(wheel_bytes).hexdigest()
    )

    wheel = portable_build.download_msvc_runtime(tmp_path)

    assert wheel.name == portable_build.MSVC_RUNTIME_WHEEL
    assert wheel.read_bytes() == wheel_bytes


def test_download_msvc_runtime_rejects_a_digest_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        portable_build.urllib.request,
        "urlretrieve",
        lambda _url, target: Path(target).write_bytes(b"unexpected-wheel-content"),
    )

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        portable_build.download_msvc_runtime(tmp_path)


def test_install_msvc_runtime_bundles_dlls_and_preserves_existing_python_dll(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / portable_build.MSVC_RUNTIME_WHEEL
    license_path = f"msvc_runtime-{portable_build.MSVC_RUNTIME_VERSION}.dist-info/licenses/LICENSE"
    runtime_prefix = f"msvc_runtime-{portable_build.MSVC_RUNTIME_VERSION}.data/data/Scripts/"
    with zipfile.ZipFile(wheel, "w") as archive:
        for filename in portable_build.MSVC_RUNTIME_DLLS:
            archive.writestr(runtime_prefix + filename, filename.encode())
        archive.writestr(license_path, b"redistribution license")

    site_packages = tmp_path / "app" / "site-packages"
    python_dir = tmp_path / "python"
    python_dir.mkdir()
    preexisting_runtime = python_dir / "vcruntime140.dll"
    preexisting_runtime.write_bytes(b"existing")

    portable_build.install_msvc_runtime(wheel, site_packages, python_dir)

    capi_dir = site_packages / "onnxruntime" / "capi"
    for filename in portable_build.MSVC_RUNTIME_DLLS:
        assert (capi_dir / filename).read_bytes() == filename.encode()
        python_runtime = python_dir / filename
        if filename == "vcruntime140.dll":
            assert python_runtime.read_bytes() == b"existing"
        else:
            assert python_runtime.read_bytes() == filename.encode()
    assert (
        site_packages.parent / "third-party-notices" / "msvc-runtime-LICENSE.txt"
    ).read_bytes() == b"redistribution license"
