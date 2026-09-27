from __future__ import annotations

import argparse
import json
from pathlib import Path

from .builder import build_dictionary
from .validation import validate_dictionary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xhdcd_yomitan")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build the text-only Yomitan ZIP")
    build.add_argument("--input", type=Path, default=Path("."))
    build.add_argument("--output", type=Path, default=Path("outputs/xhdcd-yomitan-text.zip"))
    validate = commands.add_parser("validate", help="Validate every bank against Yomitan format 3")
    validate.add_argument("dictionary", type=Path)
    validate.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.command == "build":
        print(json.dumps(build_dictionary(args.input, args.output), ensure_ascii=False, indent=2))
        return 0
    result = validate_dictionary(args.dictionary)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["valid"] else 1
