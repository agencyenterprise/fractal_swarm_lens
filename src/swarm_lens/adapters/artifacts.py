import hashlib
from pathlib import Path


class FileArtifacts:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / digest
        if not path.exists():
            path.write_bytes(content)
        return digest

    def get(self, digest: str) -> bytes:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Invalid artifact digest")
        return (self.root / digest).read_bytes()
