"""Atomic whole-document JSON storage; in-process writes are serialized."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time

_REPLACE_ATTEMPTS = 6   # Windows refuses os.replace while another process briefly holds the target

_write_lock = threading.Lock()


def read_json(path: Path):
    # Windows can refuse replacement while another thread has an open reader.
    with _write_lock:
        return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data):
    # Serialize before touching disk, including rejection of NaN and Infinity.
    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with _write_lock:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=f".{path.stem}_", suffix=".tmp", delete=False) as staging:
                temporary_path = Path(staging.name)
                staging.write(payload)
                staging.flush()
                os.fsync(staging.fileno())
            _replace_with_retry(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _replace_with_retry(source: Path, target: Path):
    """os.replace, retried briefly on PermissionError (WinError 5: the target is
    open in another process, e.g. a virus scanner or indexer). The last error
    is raised; the caller's staging cleanup still runs."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.02 * (attempt + 1))
