"""Tiny JSON-file-backed KV store. One file per project (plus a test copy).

Used both by the backend (KV viewing/editing endpoints) and by worker
processes (framework kv_* functions). Writers use atomic tmp+rename so the
file stays valid JSON at all times, and the whole store is plain JSON for
easy git tracking / import / export.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class KVStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write({})

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text() or "{}")
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            backup = self.path.with_suffix(self.path.suffix + ".corrupt")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            return {}

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))
        os.replace(tmp, self.path)

    # -- dict-like API -----------------------------------------------------
    def get(self, key: str, default=None):
        with self._lock:
            return self._read().get(str(key), default)

    def set(self, key: str, value) -> None:
        json.dumps(value)  # validate JSON-serializable before touching disk
        with self._lock:
            data = self._read()
            data[str(key)] = value
            self._write(data)

    def delete(self, key: str) -> bool:
        with self._lock:
            data = self._read()
            if str(key) in data:
                del data[str(key)]
                self._write(data)
                return True
            return False

    def keys(self) -> list:
        with self._lock:
            return list(self._read().keys())

    def items(self) -> dict:
        with self._lock:
            return dict(self._read())

    def replace_all(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise ValueError("KV store must be a JSON object")
        json.dumps(data)  # validate
        with self._lock:
            self._write(dict(data))

    def clone_from(self, other_path: Path) -> None:
        """Overwrite this store with the contents of another file (test mode)."""
        try:
            data = json.loads(Path(other_path).read_text() or "{}")
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        with self._lock:
            self._write(data)
