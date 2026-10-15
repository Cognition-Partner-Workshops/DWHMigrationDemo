from __future__ import annotations

import ctypes
import logging
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


class SecretProtector(ABC):
    @abstractmethod
    def protect(self, data: bytes) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def unprotect(self, data: bytes) -> bytes:
        raise NotImplementedError


class DpapiProtector(SecretProtector):
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("DPAPI protection is only available on Windows.")

    @staticmethod
    def _transform(data: bytes, *, protect: bool) -> bytes:
        class DataBlob(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.c_ulong),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        source = ctypes.create_string_buffer(data, max(1, len(data) + 1))
        source_blob = DataBlob(
            len(data),
            ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)),
        )
        output_blob = DataBlob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        description = ctypes.c_wchar_p()
        if protect:
            operation = crypt32.CryptProtectData
            operation.argtypes = [
                ctypes.POINTER(DataBlob),
                ctypes.c_wchar_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(DataBlob),
            ]
            operation.restype = ctypes.c_bool
            success = operation(
                ctypes.byref(source_blob),
                None,
                None,
                None,
                None,
                0x1,
                ctypes.byref(output_blob),
            )
        else:
            operation = crypt32.CryptUnprotectData
            operation.argtypes = [
                ctypes.POINTER(DataBlob),
                ctypes.POINTER(ctypes.c_wchar_p),
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(DataBlob),
            ]
            operation.restype = ctypes.c_bool
            success = operation(
                ctypes.byref(source_blob),
                ctypes.byref(description),
                None,
                None,
                None,
                0x1,
                ctypes.byref(output_blob),
            )
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
            if not protect and description.value is not None:
                kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))

    def protect(self, data: bytes) -> bytes:
        return self._transform(data, protect=True)

    def unprotect(self, data: bytes) -> bytes:
        return self._transform(data, protect=False)


class DevFileKeyProtector(SecretProtector):
    def __init__(self, data_root: Path) -> None:
        self.key_path = data_root / "dev-secret.key"
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        logger.warning("development-only secret protection")
        try:
            descriptor = os.open(
                self.key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            self._key = self.key_path.read_bytes()
        else:
            self._key = AESGCM.generate_key(bit_length=256)
            with os.fdopen(descriptor, "wb") as key_file:
                key_file.write(self._key)
        if os.name != "nt":
            os.chmod(self.key_path, 0o600)
        if len(self._key) != 32:
            raise ValueError("Development secret key must contain 32 bytes.")
        self._cipher = AESGCM(self._key)

    def protect(self, data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, data, None)

    def unprotect(self, data: bytes) -> bytes:
        if len(data) < 12:
            raise ValueError("Protected secret is shorter than the AES-GCM nonce.")
        return self._cipher.decrypt(data[:12], data[12:], None)


def get_protector(data_root: Path) -> SecretProtector:
    if sys.platform == "win32":
        return DpapiProtector()
    return DevFileKeyProtector(data_root)
