from __future__ import annotations

from xhdcd_yomitan.content import IMAGE_NOTICE, ParseStats, is_page_key, parse_record, redirect_target


def walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def test_numbered_headword_reading_variant_and_citation():
    raw = ('<span class="hw">万<sup>1</sup></span><div class="ohw">'
           '<span class="pinyin">wàn</span><span class="yitizi">萬</span></div>'
           '<div class="contents"><p>❶ 数词。<br><span class="ru">如：一万。</span>'
           '<br><br>❷ 很多。</p></div><div class="ref">《<books>现代汉语大词典上册</books>》'
           '第<pgnum><a href="entry://XHDCD0060">0060</a></pgnum>页（127字）</div>')
    entry = parse_record("万1", raw, ParseStats())
    assert entry.expression == "万"
    assert entry.readings == ["wàn"]
    assert entry.aliases == ["萬"]
    nodes = list(walk(entry.glossary))
    assert sum(isinstance(x, dict) and x.get("data", {}).get("content") == "sense" for x in nodes) == 2
    assert any(isinstance(x, dict) and x.get("tag") == "details" and x.get("open") is False for x in nodes)
    assert "《现代汉语大词典上册》第0060页" in nodes
    assert not any(isinstance(x, dict) and x.get("tag") == "a" for x in nodes)


def test_internal_links_are_queries_and_images_are_omitted():
    stats = ParseStats()
    raw = ('<span class="hw">上弦</span><span class="pinyin">shàng xián</span>'
           '<div class="contents"><p>见<a href="entry://下弦">下弦</a>。'
           '<img src="moon.gif"></p></div>')
    entry = parse_record("上弦", raw, stats)
    nodes = list(walk(entry.glossary))
    assert "下弦" in stats.links
    assert any(isinstance(x, dict) and x.get("href") == "?query=%E4%B8%8B%E5%BC%A6" for x in nodes)
    assert IMAGE_NOTICE in nodes or any(isinstance(x, str) and IMAGE_NOTICE in x for x in nodes)
    assert not any(isinstance(x, dict) and x.get("tag") == "img" for x in nodes)


def test_malformed_markup_keeps_text_and_invalid_pinyin_is_visible():
    stats = ParseStats()
    raw = ('<span class="hw">疑词</span><span class="pinyin">〈口〉</span>'
           '<div class="contents"><p><p>释义<img src="x.jpg"></span></p></div>'
           '<script>alert(1)</script>')
    entry = parse_record("疑词", raw, stats)
    assert entry.readings == [""]
    nodes = list(walk(entry.glossary))
    assert any(isinstance(x, str) and "释义" in x for x in nodes)
    assert any(isinstance(x, str) and "〈口〉" in x for x in nodes)
    assert not any(isinstance(x, str) and "alert(1)" in x for x in nodes)
    assert stats.counts["omitted_inline_images"] == 1


def test_ambiguous_variants_remain_display_only():
    entry = parse_record("亩", '<span class="hw">亩</span><span class="yitizi">畝𤰜晦</span>'
                         '<div class="contents"><p>土地面积单位。</p></div>', ParseStats())
    assert entry.aliases == []
    assert any(isinstance(x, str) and x == "畝𤰜晦" for x in walk(entry.glossary))


def test_source_redirects_and_page_keys():
    assert redirect_target("@@@LINK=万2\r\n") == "万2"
    assert is_page_key("XHDCD3236")
    assert is_page_key("XHDCD_sy01")
    assert not is_page_key("现代汉语大词典")
