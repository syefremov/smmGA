"""D-009: private shared server volume, opaque immutable keys, never client paths."""

import hashlib
import os
import sys
from pathlib import Path
from typing import Protocol
from uuid import UUID

from smm_gpt.domain.knowledge_files import MAX_FILE_BYTES
from smm_gpt.domain.operations import OperationError


class FileStore(Protocol):
    def put(self, identifier: UUID, data: bytes) -> None: ...
    def get(self, identifier: UUID, expected_hash: str) -> bytes: ...


class VolumeFileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, identifier: UUID) -> Path:
        folder = self.root / "knowledge-originals"
        if folder.is_symlink() or not folder.resolve().is_relative_to(self.root):
            raise OperationError("original_storage_unavailable", 503)
        return folder / (identifier.hex + ".blob")

    def put(self, identifier: UUID, data: bytes) -> None:
        if not 0 < len(data) <= MAX_FILE_BYTES:
            raise OperationError("file_size_invalid", 422)
        path = self._path(identifier)
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if sys.platform == "linux":
                # Persist the new directory entry before committing its database reference.
                for directory in (path.parent, self.root):
                    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
        except OSError:
            # An interrupted upload can leave an inaccessible orphan. Never overwrite/delete it.
            raise OperationError("original_storage_unavailable", 503) from None

    def get(self, identifier: UUID, expected_hash: str) -> bytes:
        path = self._path(identifier)
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        except OSError:
            raise OperationError("original_storage_unavailable", 503) from None
        if len(data) > MAX_FILE_BYTES or hashlib.sha256(data).hexdigest() != expected_hash:
            raise OperationError("original_hash_mismatch")
        return data
