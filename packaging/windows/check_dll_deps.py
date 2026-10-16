from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

SYSTEM_DLLS = frozenset(
    {
        "advapi32.dll",
        "bcrypt.dll",
        "bcryptprimitives.dll",
        "cfgmgr32.dll",
        "cabinet.dll",
        "clbcatq.dll",
        "combase.dll",
        "comctl32.dll",
        "comdlg32.dll",
        "crypt32.dll",
        "cryptbase.dll",
        "dbghelp.dll",
        "dnsapi.dll",
        "dwmapi.dll",
        "dxgi.dll",
        "gdi32.dll",
        "gdi32full.dll",
        "imm32.dll",
        "iphlpapi.dll",
        "kernel32.dll",
        "kernelbase.dll",
        "mpr.dll",
        "msi.dll",
        "msctf.dll",
        "msvcp_win.dll",
        "msvcrt.dll",
        "mswsock.dll",
        "ncrypt.dll",
        "netapi32.dll",
        "normaliz.dll",
        "nsi.dll",
        "ntdll.dll",
        "ole32.dll",
        "oleacc.dll",
        "oleaut32.dll",
        "opengl32.dll",
        "pdh.dll",
        "powrprof.dll",
        "profapi.dll",
        "psapi.dll",
        "propsys.dll",
        "rpcrt4.dll",
        "sechost.dll",
        "secur32.dll",
        "setupapi.dll",
        "shell32.dll",
        "shcore.dll",
        "shlwapi.dll",
        "uxtheme.dll",
        "user32.dll",
        "userenv.dll",
        "version.dll",
        "win32u.dll",
        "winhttp.dll",
        "wininet.dll",
        "winmm.dll",
        "winspool.drv",
        "wintrust.dll",
        "ucrtbase.dll",
        "wldap32.dll",
        "ws2_32.dll",
        "wtsapi32.dll",
    }
)
SYSTEM_DLL_PREFIXES = ("api-ms-win-", "ext-ms-")


def _dll_name(value: str | bytes) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    return value.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def is_windows_system_dll(name: str | bytes) -> bool:
    normalized = _dll_name(name)
    return normalized.startswith(SYSTEM_DLL_PREFIXES) or normalized in SYSTEM_DLLS


def scan_bundle(bundle_root: Path, pefile_module: Any = None) -> list[str]:
    if pefile_module is None:
        try:
            import pefile as pefile_module
        except ImportError as error:
            raise RuntimeError(
                "Install the pinned build-only dependency: pefile==2024.8.26"
            ) from error

    parse_errors = (OSError, pefile_module.PEFormatError)
    binary_paths = sorted(
        path
        for path in bundle_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".dll", ".pyd"}
    )
    available = {path.name.casefold() for path in binary_paths}
    failures: list[str] = []

    for binary_path in binary_paths:
        relative_path = binary_path.relative_to(bundle_root)
        try:
            pe = pefile_module.PE(str(binary_path))
            imports = [
                entry.dll
                for table_name in (
                    "DIRECTORY_ENTRY_IMPORT",
                    "DIRECTORY_ENTRY_DELAY_IMPORT",
                )
                for entry in (getattr(pe, table_name, None) or [])
            ]
            if hasattr(pe, "close"):
                pe.close()
        except parse_errors as error:
            failures.append(
                f"{relative_path}: could not read PE imports ({type(error).__name__}: {error})"
            )
            continue

        for imported_dll in imports:
            name = _dll_name(imported_dll)
            if name not in available and not is_windows_system_dll(name):
                failures.append(f"{relative_path}: missing imported DLL {name}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that Windows portable-bundle DLL imports are available."
    )
    parser.add_argument("bundle", type=Path, help="Path to the built MigraAI bundle")
    args = parser.parse_args()
    if not args.bundle.is_dir():
        parser.error(f"bundle directory does not exist: {args.bundle}")

    failures = scan_bundle(args.bundle)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"All DLL imports resolve in {args.bundle}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
