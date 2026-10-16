from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

CHECK_SCRIPT = Path(__file__).resolve().parents[4] / "packaging" / "windows" / "check_dll_deps.py"
_SPEC = importlib.util.spec_from_file_location("migraai_check_dll_deps", CHECK_SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
dll_checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dll_checker)


class FakePE:
    imports_by_name: dict[str, list[bytes]] = {}

    def __init__(self, path: str) -> None:
        self.DIRECTORY_ENTRY_IMPORT = [
            SimpleNamespace(dll=name)
            for name in self.imports_by_name.get(Path(path).name.casefold(), [])
        ]
        self.DIRECTORY_ENTRY_DELAY_IMPORT = []


def test_scan_bundle_accepts_bundled_and_windows_system_imports(tmp_path: Path) -> None:
    capi = tmp_path / "app" / "site-packages" / "onnxruntime" / "capi"
    capi.mkdir(parents=True)
    (capi / "onnxruntime_pybind11_state.pyd").write_bytes(b"fake PE")
    (capi / "MSVCP140_1.DLL").write_bytes(b"bundled runtime")
    FakePE.imports_by_name = {
        "onnxruntime_pybind11_state.pyd": [
            b"kernel32.dll",
            b"MSVCP140_1.DLL",
            b"api-ms-win-core-libraryloader-l1-2-0.dll",
            b"ext-ms-win-kernel32-file-l1-1-0.dll",
        ]
    }

    failures = dll_checker.scan_bundle(
        tmp_path, SimpleNamespace(PE=FakePE, PEFormatError=ValueError)
    )

    assert failures == []


def test_scan_bundle_rejects_missing_msvc_runtime_imports(tmp_path: Path) -> None:
    binary = tmp_path / "onnxruntime.dll"
    binary.write_bytes(b"fake PE")
    FakePE.imports_by_name = {"onnxruntime.dll": [b"msvcp140_1.dll", b"vcruntime140_1.dll"]}

    failures = dll_checker.scan_bundle(
        tmp_path, SimpleNamespace(PE=FakePE, PEFormatError=ValueError)
    )

    assert failures == [
        "onnxruntime.dll: missing imported DLL msvcp140_1.dll",
        "onnxruntime.dll: missing imported DLL vcruntime140_1.dll",
    ]


def test_msvc_runtime_families_are_not_system_allowlisted() -> None:
    assert not dll_checker.is_windows_system_dll("msvcp140.dll")
    assert not dll_checker.is_windows_system_dll("vcruntime140_1.dll")
    assert not dll_checker.is_windows_system_dll("concrt140.dll")
