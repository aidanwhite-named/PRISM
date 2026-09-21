"""Prepare the optional local tokenizer cache during installation, not preflight."""
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.providers.model_limits import ENCODING_URL, ENCODING_SHA256, encoding_cache_path


def main():
    try:
        import truststore
        truststore.inject_into_ssl()
        import requests
        path = encoding_cache_path()
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == ENCODING_SHA256:
            return
        response = requests.get(ENCODING_URL, timeout=30)
        response.raise_for_status()
        content = response.content
        if hashlib.sha256(content).hexdigest() != ENCODING_SHA256:
            raise ValueError("Tokenizer checksum mismatch")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        print("Local tokenizer cache ready.")
    except Exception as exc:
        print(f"Tokenizer unavailable; conservative estimates remain active: {exc}")


if __name__ == "__main__":
    main()
