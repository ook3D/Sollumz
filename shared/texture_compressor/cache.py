"""Persistent cache of converted textures, shared by every export that uses the same cache directory.

An entry is a converted DDS file named by a hash of everything that determines its contents: the compressor (and its
executable), the compressor arguments and the input pixels. So an entry is reused across exports, assets, games and
.blend files, and anything that would change the output simply misses.

The directory is chosen by the user, so the cache only writes into a directory marked as a texture cache (it marks
new and empty directories), and pruning only deletes the cache's own files.
"""

import hashlib
import os
import re
import time
import uuid
from pathlib import Path

from ... import logger

CACHE_VERSION = 1
"""Bump when the conversion of a given input and arguments changes, to invalidate existing entries."""

MARKER_NAME = ".sollumz_texture_cache"

_DIGEST_SIZE = 20
_SUBDIR_RE = re.compile(r"[0-9a-f]{2}")
_ENTRY_RE = re.compile(rf"[0-9a-f]{{{_DIGEST_SIZE * 2}}}\.dds")
_TEMP_RE = re.compile(rf"[0-9a-f]{{{_DIGEST_SIZE * 2}}}\.dds\.[0-9a-f]+\.tmp")
_TEMP_FILE_MAX_AGE_SECONDS = 3600


def _hasher():
    return hashlib.blake2b(digest_size=_DIGEST_SIZE)


def digest_bytes(data: bytes, *extra: str) -> str:
    h = _hasher()
    for e in extra:
        h.update(e.encode("utf-8") + b"\0")
    h.update(data)
    return h.hexdigest()


def digest_file(path: Path) -> str | None:
    """Hash of a file's contents, or `None` if it cannot be read."""
    h = _hasher()
    buffer = bytearray(1 << 20)
    view = memoryview(buffer)
    try:
        with open(path, "rb", buffering=0) as f:
            while n := f.readinto(buffer):
                h.update(view[:n])
    except OSError:
        return None
    return h.hexdigest()


def executable_fingerprint(backend_id: str, executable: Path) -> str:
    """Identifies the compressor build: reinstalling or updating it changes the size or modification time."""
    try:
        st = executable.stat()
        return f"{backend_id}|{executable}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        return f"{backend_id}|{executable}"


class TextureCache:
    def __init__(self, directory: Path, salt: str):
        self.directory = directory
        self.salt = salt

    @classmethod
    def open(cls, directory: Path, salt: str) -> "TextureCache | None":
        """Opens the cache in `directory`, creating and marking it if it doesn't exist or is empty. Returns `None`
        (after a warning) if it is unusable: not writable, or a non-empty directory that is not a texture cache.
        """
        try:
            if not (directory / MARKER_NAME).is_file():
                if directory.is_dir() and any(directory.iterdir()):
                    logger.warning(
                        f"Texture cache disabled: '{directory}' is not empty and is not a texture cache. Choose an "
                        f"empty or new directory in Sollumz preferences > Import / Export > Texture Compression."
                    )
                    return None
                directory.mkdir(parents=True, exist_ok=True)
                (directory / MARKER_NAME).write_text(
                    f"Sollumz texture cache (version {CACHE_VERSION}). Safe to delete.\n",
                    encoding="utf-8",
                )
        except OSError as e:
            logger.warning(f"Texture cache disabled: cannot use '{directory}': {e}")
            return None
        return cls(directory, salt)

    def key(self, args: str, input_digest: str) -> str:
        return digest_bytes(input_digest.encode("ascii"), str(CACHE_VERSION), self.salt, args)

    def entry_path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.dds"

    def get(self, key: str) -> Path | None:
        """The entry for `key`, or `None`. Marks it as recently used."""
        path = self.entry_path(key)
        try:
            os.utime(path)
        except OSError:
            return None  # missing (or not writable, in which case the cache is unusable anyway)
        return path

    def put(self, key: str, source: Path, move: bool = False) -> Path:
        """Stores `source` as the entry for `key` and returns its path. Atomic: concurrent readers and writers
        never see a partial file. With `move`, `source` is moved instead of copied when it is on the same
        volume. Raises `OSError`."""
        path = self.entry_path(key)
        path.parent.mkdir(exist_ok=True)
        if move:
            try:
                os.replace(source, path)
                return path
            except OSError:
                pass  # e.g. another volume: copy
        temp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:12]}.tmp")
        try:
            temp.write_bytes(source.read_bytes())
            os.replace(temp, path)
        except OSError:
            temp.unlink(missing_ok=True)
            raise
        return path

    def prune(self, size_limit: int):
        """Deletes stale temporary files, and the least recently used entries while the cache is over
        `size_limit`. Only touches the cache's own files, and only in a marked directory."""
        if not (self.directory / MARKER_NAME).is_file():
            return

        now = time.time()
        entries = []  # (mtime, size, path)
        subdirs = []
        try:
            for subdir in self.directory.iterdir():
                if not (_SUBDIR_RE.fullmatch(subdir.name) and subdir.is_dir()):
                    continue
                subdirs.append(subdir)
                for f in subdir.iterdir():
                    try:
                        st = f.stat()
                        if _ENTRY_RE.fullmatch(f.name):
                            entries.append((st.st_mtime, st.st_size, f))
                        elif _TEMP_RE.fullmatch(f.name) and now - st.st_mtime > _TEMP_FILE_MAX_AGE_SECONDS:
                            f.unlink()
                    except OSError:
                        pass
        except OSError:
            return

        total = sum(size for _, size, _ in entries)
        if total > size_limit:
            PRUNE_TARGET_RATIO = 0.8
            target = size_limit * PRUNE_TARGET_RATIO
            for _, size, f in sorted(entries, key=lambda e: e[0]):
                if total <= target:
                    break
                try:
                    f.unlink()
                    total -= size
                except OSError:
                    pass

        for subdir in subdirs:
            try:
                subdir.rmdir()  # only succeeds when empty
            except OSError:
                pass
