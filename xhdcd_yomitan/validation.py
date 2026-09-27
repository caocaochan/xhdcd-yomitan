from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlsplit

from jsonschema import Draft7Validator

from .content import is_page_key


def _walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _schema(name: str) -> dict:
    source = files("xhdcd_yomitan").joinpath("schemas", name)
    return json.loads(source.read_text(encoding="utf-8"))


def _allowed_content_tags(schema: dict) -> dict[str, tuple[set[str], set[str]]]:
    """Read valid tag/property combinations from Yomitan's format-3 schema."""
    variants = schema["definitions"]["structuredContent"]["oneOf"][2]["oneOf"]
    result: dict[str, tuple[set[str], set[str]]] = {}
    for variant in variants:
        tag_rule = variant["properties"]["tag"]
        tags = [tag_rule["const"]] if "const" in tag_rule else tag_rule["enum"]
        for tag in tags:
            result[tag] = (set(variant.get("required", [])), set(variant["properties"]))
    return result


def _check_content(value: Any, location: str, allowed: dict[str, tuple[set[str], set[str]]],
                   errors: list[str], counts: Counter[str]) -> None:
    if len(errors) >= 100:
        return
    if isinstance(value, str):
        if re.search(r"<\s*(?:script|iframe|object|embed)\b", value, re.I):
            errors.append(f"{location}: raw executable markup retained")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _check_content(child, f"{location}/{index}", allowed, errors, counts)
        return
    if not isinstance(value, dict):
        errors.append(f"{location}: invalid structured-content value")
        return
    counts["structured_nodes_checked"] += 1
    tag = value.get("tag")
    if not isinstance(tag, str) or tag not in allowed:
        errors.append(f"{location}: tag {tag!r} is not in the Yomitan schema")
        return
    required, properties = allowed[tag]
    if not required <= value.keys() or not value.keys() <= properties:
        errors.append(f"{location}: invalid properties for {tag}")
    if tag == "img" or "path" in value:
        errors.append(f"{location}: media node retained")
    if tag == "br" and "content" in value:
        errors.append(f"{location}: br cannot have content")
    if "data" in value and (not isinstance(value["data"], dict) or
                            any(not isinstance(k, str) or not isinstance(v, str)
                                for k, v in value["data"].items())):
        errors.append(f"{location}: invalid data attributes")
    if "lang" in value and not isinstance(value["lang"], str):
        errors.append(f"{location}: invalid lang")
    if "open" in value and not isinstance(value["open"], bool):
        errors.append(f"{location}: invalid open value")
    if "title" in value and not isinstance(value["title"], str):
        errors.append(f"{location}: invalid title")
    if "style" in value and not isinstance(value["style"], dict):
        errors.append(f"{location}: invalid style")
    if tag == "a":
        href = value.get("href")
        if not isinstance(href, str) or not href.startswith("?query="):
            errors.append(f"{location}: non-internal link retained")
    if "content" in value:
        _check_content(value["content"], f"{location}/content", allowed, errors, counts)


def validate_dictionary(path: Path) -> dict[str, Any]:
    path = path.resolve()
    errors: list[str] = []
    counts: Counter[str] = Counter()
    expressions: set[str] = set()
    links: set[str] = set()
    index_schema = Draft7Validator(_schema("dictionary-index-schema.json"))
    term_schema_data = _schema("dictionary-term-bank-v3-schema.json")
    term_schema = Draft7Validator(term_schema_data)
    allowed_tags = _allowed_content_tags(term_schema_data)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            errors.append("Duplicate ZIP member names")
        banks = sorted((name for name in names if re.fullmatch(r"term_bank_\d+\.json", name)),
                       key=lambda name: int(re.search(r"\d+", name).group()))
        expected = [f"term_bank_{number}.json" for number in range(1, len(banks) + 1)]
        if banks != expected:
            errors.append("Term banks are missing or not numbered consecutively")
        if not banks:
            errors.append("No term banks")
        allowed = set(banks) | {"index.json", "styles.css"}
        for name in names:
            if name not in allowed:
                errors.append(f"Unexpected ZIP member: {name}")
        if "index.json" not in names:
            errors.append("Missing index.json")
        else:
            index = json.loads(archive.read("index.json"))
            for issue in index_schema.iter_errors(index):
                errors.append(f"index.json: {issue.message}")
            if index.get("format") != 3:
                errors.append("index.json format must be 3")
        if "styles.css" not in names or not archive.read("styles.css"):
            errors.append("Missing or empty styles.css")
        for bank_name in banks:
            bank = json.loads(archive.read(bank_name))
            counts["term_banks"] += 1
            if not isinstance(bank, list) or len(bank) > 10_000:
                errors.append(f"{bank_name}: must be an array of at most 10,000 rows")
                continue
            if not bank:
                errors.append(f"{bank_name}: empty bank")
                continue
            counts["term_rows"] += len(bank)
            for sample_index in sorted({0, len(bank) // 2, len(bank) - 1} - {-1}):
                for issue in term_schema.iter_errors([bank[sample_index]]):
                    if len(errors) < 100:
                        errors.append(f"{bank_name}[{sample_index}] official schema: {issue.message}")
                counts["official_schema_sampled_rows"] += 1
            for row_number, row in enumerate(bank):
                if not isinstance(row, list) or len(row) != 8:
                    errors.append(f"{bank_name}[{row_number}]: invalid row shape")
                    continue
                if not (isinstance(row[0], str) and isinstance(row[1], str) and
                        (isinstance(row[2], str) or row[2] is None) and isinstance(row[3], str) and
                        isinstance(row[4], (int, float)) and not isinstance(row[4], bool) and
                        isinstance(row[6], int) and not isinstance(row[6], bool) and isinstance(row[7], str)):
                    errors.append(f"{bank_name}[{row_number}]: field types violate Yomitan term schema")
                expression = row[0]
                if not isinstance(expression, str):
                    continue
                expressions.add(expression)
                if is_page_key(expression):
                    errors.append(f"{bank_name}[{row_number}]: scanned-page lookup retained")
                glossary = row[5]
                if not isinstance(glossary, list) or not glossary:
                    errors.append(f"{bank_name}[{row_number}]: empty glossary")
                    continue
                for item in glossary:
                    if not isinstance(item, dict) or item.get("type") != "structured-content":
                        errors.append(f"{bank_name}[{row_number}]: non-structured glossary")
                        continue
                    if set(item) != {"type", "content"}:
                        errors.append(f"{bank_name}[{row_number}]: invalid glossary properties")
                    root = item.get("content")
                    if not isinstance(root, dict) or root.get("lang") != "zh-Hans":
                        errors.append(f"{bank_name}[{row_number}]: missing zh-Hans root")
                    _check_content(root, f"{bank_name}[{row_number}]", allowed_tags, errors, counts)
                    for value in _walk(root):
                        if isinstance(value, dict):
                            tag = value.get("tag")
                            if tag == "a":
                                href = value.get("href", "")
                                if isinstance(href, str) and href.startswith("?query="):
                                    target = parse_qs(urlsplit(href).query).get("query", [""])[0]
                                    if target:
                                        links.add(target)
                if len(errors) >= 100:
                    break
            if len(errors) >= 100:
                break
    missing_links = sorted(links - expressions)
    counts["internal_link_targets"] = len(links)
    counts["unresolved_internal_link_targets"] = len(missing_links)
    return {"valid": not errors, "dictionary": str(path), "counts": dict(sorted(counts.items())),
            "errors": errors[:100], "unresolved_link_samples": missing_links[:30]}
