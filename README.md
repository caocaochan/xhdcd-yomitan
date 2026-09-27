# 现代汉语大词典 → Yomitan（文本版）

This project converts `现代汉语大词典（图文综合版）.mdx` into an
importable, text-only Yomitan dictionary. It does not read the MDD archives.
The conversion keeps definitions, Yomitan readings, examples, variant headwords,
and internal term links. Scanned-page entries, inline images, and printed
book/page citations are omitted; inline image positions show a short notice.
Each entry displays traditional and simplified headword badges, including when
both forms have the same spelling. Explicit source variants take precedence when
they unambiguously map back to the simplified headword; otherwise OpenCC supplies
the traditional form. The badges sit beside Yomitan's dictionary name, as in
HYDCD, with the definition below them.

The stylesheet follows the badge, sense-number, and collapsible-example
design of [hydcd-yomitan](https://github.com/caocaochan/hydcd-yomitan). It is packaged in the ZIP and
does not depend on that project at build or import time.

## Download and automatic builds

- [Latest Yomitan ZIP](https://github.com/caocaochan/xhdcd-yomitan/releases/latest/download/xhdcd-yomitan-text.zip)
- [Latest release and validation reports](https://github.com/caocaochan/xhdcd-yomitan/releases/latest)
- [Yomitan update index](https://github.com/caocaochan/xhdcd-yomitan/releases/latest/download/index.json)

Every push to any branch and every manual workflow run tests the converter,
downloads the pinned `xhdcd.mdx` from this repository's public
`source-xhdcd-2023.7.11` release, verifies its size and SHA-256 from
`.github/source-inputs.json`, builds the ZIP, validates it, and uploads a
seven-day Actions artifact. Successful builds on `main` publish a GitHub
Release with the ZIP, its exact `index.json`, conversion and validation
reports, provenance, and checksums. The ZIP includes update URLs so Yomitan
can discover later public releases. Source MDX and generated ZIPs are excluded
from Git history.

## Build and validate

Requires Python 3.12 or newer. From this directory in PowerShell:

```powershell
python -m pip install -e '.[test]'
python -m pytest
python -m xhdcd_yomitan build --input . --output outputs/xhdcd-yomitan-text.zip
python -m xhdcd_yomitan validate outputs/xhdcd-yomitan-text.zip --report outputs/xhdcd-validation-report.json
```

If `python` is not on `PATH`, replace it with the full path to a Python 3.12+
executable. The local `hydcd-yomitan` virtual environment already has the
required packages, but the converter imports none of that project's code.

`outputs/xhdcd-yomitan-text.zip` is the file to import in Yomitan. The build
also writes `outputs/xhdcd-conversion-report.json` with source/output hashes,
row counts, omitted-content counts, and unresolved-reading/link samples.

## Conversion rules

- Source headwords with numbered homographs, such as `万1` and `万2`, index
  under the displayed spelling `万` with separate readings. Their numbered
  source keys remain searchable aliases.
- `@@@LINK=` redirects resolve to the linked definition. A single clearly
  delimited variant form becomes a lookup alias; combined or ambiguous variant
  strings remain visible without speculative aliases.
- Every displayed traditional form is also a lookup alias. OpenCC's conversion
  can be context-sensitive, so some generated aliases may produce additional
  matches. Ambiguous source variant strings remain display-only.
- Source pinyin is normalized to NFC and `ɡ` is mapped to `g` for readings.
  Valid pinyin appears only as Yomitan's reading, not again in the definition.
  Invalid pinyin remains visible in the header while its Yomitan reading is
  empty and recorded in the report.
- The original MDX contains some dead term references. These remain visible as
  links and are listed in the report; page-scan links are removed.

The ZIP uses Yomitan format 3, with `index.json`, `styles.css`, and
`term_bank_*.json` at the archive root. Validation checks every bank and
structured-content node against schema-derived rules, and checks three rows per bank with
the official JSON Schema implementation. It also verifies that every displayed
traditional form has a lookup row for each reading of its source entry. The
format schemas are copied from
the [Yomitan project](https://github.com/yomidevs/yomitan/tree/master/ext/data/schemas).

The public source release and generated dictionary contain material from the
original dictionary. This converter does not grant redistribution rights to
the dictionary content.
