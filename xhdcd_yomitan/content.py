from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from typing import Any
from urllib.parse import quote, unquote

from lxml import etree, html
from opencc import OpenCC


IMAGE_NOTICE = "（原条目含图片，文本版未收录）"
SENSE_RE = re.compile(r"^\s*([\u2776-\u277f\u24eb-\u24f4])\s*")
REDIRECT_RE = re.compile(r"^\s*@@@LINK=(.*?)\s*$", re.S)
OMIT_TAGS = {"script", "style", "link", "iframe", "object", "embed", "form", "input", "button", "audio", "video"}
EXAMPLE_CLASSES = {"lj", "ru", "bzzz"}


@dataclass
class ParseStats:
    counts: Counter[str] = field(default_factory=Counter)
    bad_readings: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    links: set[str] = field(default_factory=set)

    def sample(self, collection: list, value: Any, limit: int = 30) -> None:
        if len(collection) < limit:
            collection.append(value)


@dataclass
class ParsedEntry:
    source_key: str
    expression: str
    readings: list[str]
    glossary: list[dict[str, Any]]
    aliases: list[str]


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\x00", "")).strip()


def redirect_target(raw: str) -> str | None:
    match = REDIRECT_RE.match(raw.replace("\x00", ""))
    return nfc(match.group(1)) if match else None


def is_page_key(key: str) -> bool:
    return bool(re.match(r"^XHDCD(?:\d{4}|_[A-Za-z0-9]+)$", key))


def has_class(el: etree._Element, name: str) -> bool:
    return name in el.get("class", "").split()


def find_class(root: etree._Element, name: str) -> etree._Element | None:
    for el in root.iter():
        if isinstance(el.tag, str) and has_class(el, name):
            return el
    return None


def node(tag: str, content: Any = None, kind: str | None = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"tag": tag}
    if content not in (None, [], ""):
        result["content"] = content
    if kind:
        result["data"] = {"content": kind}
    result.update(extra)
    return result


def append(out: list[Any], value: Any) -> None:
    if value is None or value == "":
        return
    if isinstance(value, list):
        for item in value:
            append(out, item)
    elif isinstance(value, str) and out and isinstance(out[-1], str):
        out[-1] += value
    else:
        out.append(value)


def _text_without_sup(el: etree._Element | None) -> str:
    if el is None:
        return ""
    copy = deepcopy(el)
    for sup in copy.xpath(".//sup"):
        sup.drop_tree()
    return nfc("".join(copy.itertext()))


def _valid_reading(value: str) -> bool:
    if not value:
        return False
    for char in value:
        if char in " '-·":
            continue
        category = unicodedata.category(char)
        if category.startswith("M"):
            continue
        if category.startswith("L") and "LATIN" in unicodedata.name(char, ""):
            continue
        return False
    return True


def parse_readings(original: str, expression: str, stats: ParseStats) -> list[str]:
    original = nfc(original)
    if not original:
        stats.counts["missing_readings"] += 1
        return [""]
    readings: list[str] = []
    for part in re.split(r"[、/]", original):
        candidate = nfc(re.sub(r"\s+", " ", part.replace("ɡ", "g").replace("ɑ", "a").replace("ｅ", "e").replace("ｋ", "k")))
        if _valid_reading(candidate):
            if candidate not in readings:
                readings.append(candidate)
        else:
            stats.counts["invalid_readings"] += 1
            stats.sample(stats.bad_readings, {"expression": expression, "source": original})
    return readings or [""]


def unambiguous_variant(value: str, expression: str) -> str | None:
    value = nfc(value)
    if value.startswith("〔") and value.endswith("〕"):
        value = value[1:-1]
    if not value or value == expression or len(value) != len(expression):
        return None
    if any(unicodedata.category(char)[0] not in {"L"} for char in value):
        return None
    return value


@lru_cache(maxsize=2)
def _opencc(config: str) -> OpenCC:
    return OpenCC(config)


def traditional_forms(expression: str, variants: list[str]) -> tuple[list[str], set[int]]:
    forms: list[str] = []
    used_indices: set[int] = set()
    to_simplified = _opencc("t2s.json")
    for index, variant in enumerate(variants):
        candidate = unambiguous_variant(variant, expression)
        if candidate and nfc(to_simplified.convert(candidate)) == expression:
            if candidate not in forms:
                forms.append(candidate)
            used_indices.add(index)
    if not forms:
        forms.append(nfc(_opencc("s2t.json").convert(expression)) or expression)
    return forms, used_indices


def _convert_children(el: etree._Element, stats: ParseStats) -> list[Any]:
    result: list[Any] = []
    if el.text:
        append(result, el.text)
    for child in el:
        append(result, _convert_element(child, stats))
        if child.tail:
            append(result, child.tail)
    return result


def _convert_element(el: etree._Element, stats: ParseStats) -> Any:
    if not isinstance(el.tag, str):
        return ""
    tag = el.tag.lower()
    if tag in OMIT_TAGS:
        stats.counts["discarded_executable_or_external_nodes"] += 1
        return ""
    if tag == "img":
        stats.counts["omitted_inline_images"] += 1
        return IMAGE_NOTICE
    if tag == "br":
        return node("br")
    content = _convert_children(el, stats)
    if tag == "a":
        href = el.get("href", "")
        if href.startswith("entry://"):
            target = nfc(unquote(href[len("entry://"):]))
            if target and not is_page_key(target):
                stats.links.add(target)
                return node("a", content, href="?query=" + quote(target, safe=""))
        return content
    if tag == "span":
        classes = set(el.get("class", "").split())
        for example_class in EXAMPLE_CLASSES:
            if example_class in classes:
                return node("span", content, "example")
        for source_class, kind in (
            ("yezhi", "usage-note"), ("yezuo", "usage-note"),
            ("shuzi2", "subnumber"), ("yitizi", "variant-inline"),
        ):
            if source_class in classes:
                return node("span", content, kind)
        return content
    if tag in {"books", "bookname"}:
        return node("span", content, "book-title")
    if tag == "yuchu":
        return node("div", content, "etymology")
    if tag in {"sup", "sub"}:
        return node("span", content, "superscript" if tag == "sup" else "subscript")
    if tag in {"strong", "b"}:
        return node("span", content, "emphasis")
    if tag in {"em", "i"}:
        return node("span", content, "italic")
    if tag in {"div", "p", "section", "blockquote"}:
        return node("div", content, "paragraph")
    if tag in {"ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "td", "th"}:
        return node(tag, content)
    return content


def _is_example(value: Any) -> bool:
    return isinstance(value, dict) and value.get("data", {}).get("content") == "example"


def _is_br(value: Any) -> bool:
    return isinstance(value, dict) and value.get("tag") == "br"


def _group_examples(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    pending: list[Any] = []

    def flush_examples() -> None:
        nonlocal pending
        if not pending:
            return
        while result and (_is_br(result[-1]) or
                          (isinstance(result[-1], str) and not result[-1].strip())):
            result.pop()
        result.append(node("details", [node("summary", "例证"), node("div", pending)], "examples", open=False))
        pending = []

    for item in values:
        if _is_example(item):
            pending.append(node("div", item.get("content", []), "example"))
            continue
        if pending and _is_br(item):
            continue
        flush_examples()
        append(result, item)
    flush_examples()
    while result and _is_br(result[-1]):
        result.pop()
    return result


def _split_senses(values: list[Any]) -> list[dict[str, Any]]:
    groups: list[tuple[str | None, list[Any]]] = []
    current: list[Any] = []
    number: str | None = None
    for value in values:
        if isinstance(value, str):
            match = SENSE_RE.match(value)
            if match:
                while current and _is_br(current[-1]):
                    current.pop()
                if current:
                    groups.append((number, current))
                number = match.group(1)
                current = []
                value = value[match.end():]
        append(current, value)
    if current:
        groups.append((number, current))
    result: list[dict[str, Any]] = []
    for number, content in groups:
        while content and _is_br(content[0]):
            content.pop(0)
        content = _group_examples(content)
        if not content:
            continue
        if number:
            content.insert(0, node("span", number, "sense-number"))
        result.append(node("div", content, "sense" if number else "paragraph"))
    return result


def _body_blocks(el: etree._Element, stats: ParseStats) -> list[Any]:
    blocks: list[Any] = []
    if el.text and el.text.strip():
        blocks.extend(_split_senses([el.text]))
    for child in el:
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()
        if tag == "p":
            blocks.extend(_split_senses(_convert_children(child, stats)))
        elif tag == "div":
            blocks.extend(_body_blocks(child, stats))
        elif tag in OMIT_TAGS:
            stats.counts["discarded_executable_or_external_nodes"] += 1
        else:
            converted = _convert_element(child, stats)
            if converted:
                blocks.extend(_split_senses(converted if isinstance(converted, list) else [converted]))
        if child.tail and child.tail.strip():
            blocks.extend(_split_senses([child.tail]))
    return blocks


def parse_record(source_key: str, raw: str, stats: ParseStats) -> ParsedEntry:
    try:
        root = html.fragment_fromstring(raw.replace("\x00", ""), create_parent="div")
    except (etree.ParserError, ValueError) as exc:
        stats.counts["malformed_records"] += 1
        stats.sample(stats.warnings, f"{source_key}: {exc}")
        text = nfc(re.sub(r"<[^>]*>", "", raw)) or "（无可显示释义）"
        traditional, _ = traditional_forms(source_key, [])
        header = node("div", [node("span", value, "traditional-term") for value in traditional] +
                      [node("span", source_key, "simplified-term")], "header")
        glossary = [{"type": "structured-content", "content": node("div", [header, node("div", text, "paragraph")], "xhdcd-entry", lang="zh-Hans")}]
        return ParsedEntry(source_key, source_key, [""], glossary, [])

    hw = find_class(root, "hw")
    expression = _text_without_sup(hw) or source_key
    sup = nfc("".join(hw.xpath(".//sup//text()"))) if hw is not None else ""
    pinyin_nodes = [el for el in root.iter() if isinstance(el.tag, str) and (has_class(el, "pinyin") or has_class(el, "pinyin2"))]
    original_pinyin = "、".join(nfc("".join(el.itertext())) for el in pinyin_nodes)
    readings = parse_readings(original_pinyin, expression, stats)
    variant_nodes = [el for el in root.iter() if isinstance(el.tag, str) and has_class(el, "yitizi")]
    variants = [nfc("".join(el.itertext())) for el in variant_nodes]
    aliases: list[str] = []
    for variant in variants:
        alias = unambiguous_variant(variant, expression)
        if alias and alias not in aliases:
            aliases.append(alias)
        elif variant and variant != expression:
            stats.counts["ambiguous_variants_displayed"] += 1

    traditional, traditional_variant_indices = traditional_forms(expression, variants)
    header: list[Any] = [node("span", value, "traditional-term") for value in traditional]
    header.append(node("span", expression, "simplified-term"))
    if sup:
        header.append(node("span", sup, "homograph-number"))
    for index, variant in enumerate(variants):
        if index not in traditional_variant_indices and variant:
            header.append(node("span", variant, "variant-term"))
    if original_pinyin and readings == [""]:
        header.append(node("span", original_pinyin, "pinyin"))
    content: list[Any] = [node("div", header, "header")]
    body = find_class(root, "contents")
    if body is not None:
        content.extend(_body_blocks(body, stats))
    else:
        for child in root:
            if not isinstance(child.tag, str) or child.tag.lower() in OMIT_TAGS | {"hr"}:
                continue
            if child is hw or has_class(child, "ohw") or has_class(child, "qh") or has_class(child, "ref"):
                continue
            content.extend(_body_blocks(child, stats) if child.tag.lower() == "div" else _split_senses(_convert_children(child, stats)))
    if len(content) == 1:
        content.append(node("div", IMAGE_NOTICE if "<img" in raw.lower() else "（无可显示释义）", "paragraph"))
        stats.counts["empty_definitions"] += 1
    glossary = [{"type": "structured-content", "content": node("div", content, "xhdcd-entry", lang="zh-Hans")}]
    return ParsedEntry(source_key, expression, readings, glossary, aliases)
