"""Download and verify the pinned source-release asset used by CI."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "input")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((ROOT / ".github/source-inputs.json").read_text(encoding="utf-8"))
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    repository = os.environ.get("GITHUB_REPOSITORY", "caocaochan/xhdcd-yomitan")
    for item in manifest["files"]:
        path = destination / item["name"]
        if not args.verify_only and not path.exists():
            subprocess.run([
                "gh", "release", "download", manifest["release"], "--repo", repository,
                "--pattern", item["name"], "--dir", str(destination),
            ], check=True)
        if path.stat().st_size != item["size"]:
            raise ValueError(f"Input size mismatch: {item['name']}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"Input checksum mismatch: {item['name']}")
        print(f"Verified {item['name']}: {digest}", flush=True)


if __name__ == "__main__":
    main()
