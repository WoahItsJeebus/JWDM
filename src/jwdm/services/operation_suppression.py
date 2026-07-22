"""Short-lived suppression for filesystem events caused by JWDM itself."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path


def _identity(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


class OperationSuppressor:
    def __init__(self, retention_seconds: float = 15.0) -> None:
        self._retention_seconds = retention_seconds
        self._expires: dict[str, float] = {}
        self._lock = threading.Lock()

    def suppress(self, source: Path, destination: Path) -> None:
        expires_at = time.monotonic() + self._retention_seconds
        with self._lock:
            self._expires[_identity(source)] = expires_at
            self._expires[_identity(destination)] = expires_at

    def contains(self, path: Path) -> bool:
        now = time.monotonic()
        identity = _identity(path)
        with self._lock:
            expired = [key for key, expires_at in self._expires.items() if expires_at <= now]
            for key in expired:
                self._expires.pop(key, None)
            return self._expires.get(identity, 0.0) > now
