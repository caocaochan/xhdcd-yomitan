"""Verify the built ZIP and create release assets from its exact index."""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs"
ZIP = OUTPUT / "xhdcd-yomitan-text.zip"


def main() -> None:
    report = json.loads((OUTPUT / "xhdcd-conversion-report.json").read_text(encoding="utf-8"))
    validation = json.loads((OUTPUT / "xhdcd-validation-report.json").read_text(encoding="utf-8"))
    if not validation.get("valid"):
        raise ValueError("Validation report is not valid")
    with ZIP.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != report["output"]["sha256"]:
        raise ValueError("Conversion report checksum does not match ZIP")
    if report["output"]["term_rows"] != validation["counts"]["term_rows"]:
        raise ValueError("Conversion and validation row counts differ")
    with zipfile.ZipFile(ZIP) as archive:
        index_bytes = archive.read("index.json")
        uncompressed_bytes = sum(item.file_size for item in archive.infolist())
    index = json.loads(index_bytes)
    repository = os.environ["GITHUB_REPOSITORY"]
    base = f"https://github.com/{repository}/releases/latest/download/"
    for key, expected in {
        "revision": report["dictionary_revision"],
        "isUpdatable": True,
        "indexUrl": base + "index.json",
        "downloadUrl": base + ZIP.name,
    }.items():
        if index.get(key) != expected:
            raise ValueError(f"Incorrect index {key}: {index.get(key)!r}")
    (OUTPUT / "index.json").write_bytes(index_bytes)
    (OUTPUT / "SHA256SUMS").write_text(f"{digest}  {ZIP.name}\n", encoding="utf-8")
    provenance = {key: os.environ[key] for key in (
        "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
    ) if key in os.environ}
    provenance.update(dictionary_revision=index["revision"], input=report["input"],
                      output_sha256=digest, term_rows=report["output"]["term_rows"])
    (OUTPUT / "build-info.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    notes = [
        f"现代汉语大词典（文本版） {index['revision']}", "",
        f"Import `{ZIP.name}` into Yomitan. The ZIP is {ZIP.stat().st_size / 1_000_000:.2f} MB; "
        f"its uncompressed contents are {uncompressed_bytes / 1_000_000:.2f} MB.", "",
        f"- {report['output']['term_rows']:,} term rows in {report['output']['term_banks']} banks",
        "- Definitions, Yomitan readings, examples, variants, and term links retained",
        "- Scanned pages, inline images, and printed page citations omitted",
        "- Source, ZIP, and validation checksums recorded in the attached reports", "",
        "This public build uses the pinned MDX asset in the source release. "
        "Original dictionary content remains subject to its rights holders.",
    ]
    if "GITHUB_SHA" in os.environ:
        notes.extend(["", f"Converter commit: `{os.environ['GITHUB_SHA']}`"])
    (OUTPUT / "release-notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
