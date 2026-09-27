from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any

from mdict_utils.base.readmdict import MDX

from . import __version__
from .content import ParseStats, is_page_key, nfc, parse_record, redirect_target


ZIP_TIME = (1980, 1, 1, 0, 0, 0)
BANK_LIMIT = 10_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, expression TEXT NOT NULL,
            readings TEXT NOT NULL, glossary TEXT NOT NULL, aliases TEXT NOT NULL
        );
        CREATE TABLE redirects (source_key TEXT NOT NULL, target TEXT NOT NULL);
        CREATE TABLE emitted (signature BLOB PRIMARY KEY);
    """)
    return db


class BankWriter:
    def __init__(self, root: Path):
        self.root = root
        self.number = 0
        self.in_bank = 0
        self.total = 0
        self.stream = None

    def add(self, row: list[Any]) -> None:
        if self.stream is None or self.in_bank >= BANK_LIMIT:
            self._next()
        assert self.stream is not None
        if self.in_bank:
            self.stream.write(",\n")
        self.stream.write(_json(row))
        self.in_bank += 1
        self.total += 1

    def _next(self) -> None:
        self.close()
        self.number += 1
        self.in_bank = 0
        self.stream = (self.root / f"term_bank_{self.number}.json").open("w", encoding="utf-8", newline="\n")
        self.stream.write("[\n")

    def close(self) -> None:
        if self.stream is not None:
            self.stream.write("\n]\n")
            self.stream.close()
            self.stream = None


def _emit(db: sqlite3.Connection, banks: BankWriter, expression: str, reading: str,
          glossary: list[dict], sequence: int, counts: Counter[str], kind: str) -> None:
    expression = nfc(expression)
    if not expression:
        return
    signature = hashlib.sha256(_json([expression, reading, glossary]).encode("utf-8")).digest()
    if db.execute("INSERT OR IGNORE INTO emitted VALUES (?)", (signature,)).rowcount == 0:
        counts["duplicate_rows_elided"] += 1
        return
    banks.add([expression, reading, "", "", 0, glossary, sequence, ""])
    counts[f"{kind}_rows"] += 1


def _resolve(db: sqlite3.Connection, key: str, seen: frozenset[str] = frozenset()) -> list[int]:
    if key in seen or len(seen) >= 32:
        return []
    ids = [row[0] for row in db.execute("SELECT id FROM entries WHERE source_key=? ORDER BY id", (key,))]
    if ids:
        return ids
    targets = [row[0] for row in db.execute("SELECT target FROM redirects WHERE source_key=?", (key,))]
    result: list[int] = []
    for target in targets:
        for value in _resolve(db, target, seen | {key}):
            if value not in result:
                result.append(value)
    if result:
        return result
    return [row[0] for row in db.execute("SELECT id FROM entries WHERE expression=? ORDER BY id", (key,))]


def _row_payload(db: sqlite3.Connection, entry_id: int) -> tuple[str, list[str], list[dict]]:
    row = db.execute("SELECT expression, readings, glossary FROM entries WHERE id=?", (entry_id,)).fetchone()
    assert row is not None
    return row[0], json.loads(row[1]), json.loads(row[2])


def _write_zip(output: Path, stage: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for source in sorted(stage.iterdir(), key=lambda p: p.name):
            info = zipfile.ZipInfo(source.name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_dictionary(input_root: Path, output: Path) -> dict[str, Any]:
    input_root = input_root.resolve()
    output = output.resolve()
    mdx_files = sorted(input_root.glob("*.mdx"))
    if len(mdx_files) != 1:
        raise ValueError(f"Expected one MDX in {input_root}, found {len(mdx_files)}")
    mdx_path = mdx_files[0]
    source_sha = sha256_file(mdx_path)
    stats = ParseStats()
    counts: Counter[str] = stats.counts
    unresolved_redirects: list[dict[str, str]] = []
    unresolved_links: list[str] = []

    with tempfile.TemporaryDirectory(prefix="xhdcd-build-") as tmp:
        stage = Path(tmp)
        db = _connect(stage / "state.sqlite")
        mdx = MDX(str(mdx_path), "", False, None)
        for ordinal, (key_raw, value_raw) in enumerate(mdx.items(), start=1):
            key = nfc(key_raw.decode("utf-8", "replace"))
            raw = value_raw.decode("utf-8", "replace")
            counts["source_records"] += 1
            if is_page_key(key):
                counts["omitted_page_records"] += 1
                continue
            if target := redirect_target(raw):
                db.execute("INSERT INTO redirects VALUES (?,?)", (key, target))
                counts["source_redirects"] += 1
                continue
            parsed = parse_record(key, raw, stats)
            db.execute(
                "INSERT INTO entries VALUES (?,?,?,?,?,?)",
                (ordinal, parsed.source_key, parsed.expression, _json(parsed.readings),
                 _json(parsed.glossary), _json(parsed.aliases)),
            )
            counts["parsed_entries"] += 1
            if ordinal % 20_000 == 0:
                db.commit()
                print(f"Parsed {ordinal:,} MDX records", file=sys.stderr, flush=True)
        db.executescript("""
            CREATE INDEX entries_source_key ON entries(source_key);
            CREATE INDEX entries_expression ON entries(expression);
            CREATE INDEX redirects_source_key ON redirects(source_key);
        """)

        for target in sorted(stats.links):
            if not _resolve(db, target):
                counts["unresolved_internal_links"] += 1
                if len(unresolved_links) < 30:
                    unresolved_links.append(target)

        banks = BankWriter(stage)
        for entry_id, source_key, expression, readings_json, glossary_json, aliases_json in db.execute(
            "SELECT id, source_key, expression, readings, glossary, aliases FROM entries ORDER BY id"
        ):
            readings = json.loads(readings_json)
            glossary = json.loads(glossary_json)
            aliases = json.loads(aliases_json)
            for reading in readings:
                _emit(db, banks, expression, reading, glossary, entry_id, counts, "canonical")
                if source_key != expression:
                    _emit(db, banks, source_key, reading, glossary, entry_id, counts, "source_key_alias")
                for alias in aliases:
                    _emit(db, banks, alias, reading, glossary, entry_id, counts, "variant_alias")
        db.commit()

        for alias, target in db.execute("SELECT source_key, target FROM redirects ORDER BY rowid"):
            if is_page_key(target):
                counts["omitted_page_redirects"] += 1
                continue
            ids = _resolve(db, target)
            if not ids:
                counts["unresolved_redirects"] += 1
                if len(unresolved_redirects) < 30:
                    unresolved_redirects.append({"alias": alias, "target": target})
                continue
            counts["resolved_redirects"] += 1
            for entry_id in ids:
                _, readings, glossary = _row_payload(db, entry_id)
                for reading in readings:
                    _emit(db, banks, alias, reading, glossary, entry_id, counts, "redirect_alias")
        banks.close()
        db.close()

        revision = os.environ.get("XHDCD_RELEASE_REVISION", f"2023.7.11+converter-{__version__}+{source_sha[:12]}")
        index = {
            "title": "现代汉语大词典（文本版）",
            "revision": revision,
            "format": 3,
            "sequenced": True,
            "author": "上海辞书出版社；MDX 图文综合版由 AlexPeng 制作",
            "description": "从用户提供的图文综合版转换。保留释义、读音、例证、异体字和内部参见；不包含扫描页、图片或印刷页码。",
            "attribution": "原词典内容版权归原权利人所有；本转换器不授予重新分发原词典数据的权利。",
            "sourceLanguage": "zh",
            "targetLanguage": "zh",
        }
        update_repository = os.environ.get("XHDCD_UPDATE_REPOSITORY")
        if update_repository:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", update_repository):
                raise ValueError("XHDCD_UPDATE_REPOSITORY must be owner/repository")
            update_base = f"https://github.com/{update_repository}/releases/latest/download/"
            index.update(isUpdatable=True, indexUrl=update_base + "index.json",
                         downloadUrl=update_base + "xhdcd-yomitan-text.zip")
        (stage / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "styles.css").write_bytes(files("xhdcd_yomitan").joinpath("data/styles.css").read_bytes())
        (stage / "state.sqlite").unlink()
        _write_zip(output, stage)

    report = {
        "converter_version": __version__,
        "dictionary_revision": revision,
        "input": {"name": mdx_path.name, "bytes": mdx_path.stat().st_size, "sha256": source_sha},
        "output": {"path": str(output), "bytes": output.stat().st_size, "sha256": sha256_file(output),
                   "term_banks": banks.number, "term_rows": banks.total},
        "counts": dict(sorted(counts.items())),
        "invalid_reading_samples": stats.bad_readings,
        "unresolved_redirect_samples": unresolved_redirects,
        "unresolved_link_samples": unresolved_links,
        "warnings": stats.warnings,
    }
    report_path = output.with_name("xhdcd-conversion-report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report
