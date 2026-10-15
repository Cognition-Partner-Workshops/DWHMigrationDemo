from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_STREAM_MAGIC = b"MIGRAAI-STREAM\x01"
_STREAM_RECORD = struct.Struct(">I")


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

    def write_stream(
        self,
        rel_path: str | Path,
        source: BinaryIO,
        *,
        chunk_size: int = 64 * 1024,
    ) -> int:
        target = self._resolve(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.urandom(8).hex()}.tmp")
        aad = str(target.relative_to(self.directory)).encode()
        total = 0
        index = 0
        try:
            with temporary.open("wb") as output:
                output.write(_STREAM_MAGIC)
                while chunk := source.read(chunk_size):
                    total += len(chunk)
                    output.write(_STREAM_RECORD.pack(len(chunk)))
                    output.write(self.cipher.encrypt(chunk, aad + b":" + str(index).encode()))
                    index += 1
                output.write(_STREAM_RECORD.pack(0))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return total

    def read(self, rel_path: str | Path) -> bytes:
        target = self._resolve(rel_path)
        aad = str(target.relative_to(self.directory)).encode()
        with target.open("rb") as source:
            if source.read(len(_STREAM_MAGIC)) != _STREAM_MAGIC:
                source.seek(0)
                return self.cipher.decrypt(source.read(), aad=aad)
            output = bytearray()
            index = 0
            while True:
                record = source.read(_STREAM_RECORD.size)
                if len(record) != _STREAM_RECORD.size:
                    raise ValueError("Encrypted stream is truncated.")
                size = _STREAM_RECORD.unpack(record)[0]
                if size == 0:
                    break
                encrypted = source.read(size + 28)
                if len(encrypted) != size + 28:
                    raise ValueError("Encrypted stream is truncated.")
                chunk = self.cipher.decrypt(encrypted, aad + b":" + str(index).encode())
                if len(chunk) != size:
                    raise ValueError("Encrypted stream record length is invalid.")
                output.extend(chunk)
                index += 1
            return bytes(output)

    def delete(self, rel_path: str | Path) -> None:
        self._resolve(rel_path).unlink(missing_ok=True)
