"""Atomic local result storage, independent of search strategy."""
import hashlib
import json
import os
import uuid
import time
from pathlib import Path
def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                # Windows readers briefly deny replacement while opening the
                # counter/checkpoint. Keep the old complete JSON until retry.
                if getattr(exc, 'winerror', None) not in (5, 32) or attempt == 5:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        if temporary.exists():
            temporary.unlink()

def identifier(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]
