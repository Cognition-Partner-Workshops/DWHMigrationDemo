from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SessionCipher:
    def __init__(self) -> None:
        self._key = AESGCM.generate_key(bit_length=256)
        self._cipher = AESGCM(self._key)

    def encrypt(self, data: bytes, aad: bytes = b"") -> bytes:
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, data, aad)

    def decrypt(self, data: bytes, aad: bytes = b"") -> bytes:
        if len(data) < 12:
            raise ValueError("Encrypted data is shorter than the AES-GCM nonce.")
        return self._cipher.decrypt(data[:12], data[12:], aad)


class EncryptedFileStore:
    def __init__(self, directory: Path, cipher: SessionCipher) -> None:
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cipher = cipher

    def _resolve(self, rel_path: str | Path) -> Path:
        relative = Path(rel_path)
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise ValueError("Path must be relative to the encrypted file store.")
        target = (self.directory / relative).resolve()
        if target != self.directory and self.directory not in target.parents:
            raise ValueError("Path must stay inside the encrypted file store.")
        return target

    def write(self, rel_path: str | Path, data: bytes) -> None:
        target = self._resolve(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            self.cipher.encrypt(data, aad=str(target.relative_to(self.directory)).encode())
        )

    def read(self, rel_path: str | Path) -> bytes:
        target = self._resolve(rel_path)
        data = target.read_bytes()
        return self.cipher.decrypt(data, aad=str(target.relative_to(self.directory)).encode())
