import re
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from unicodedata import normalize

from markdown_it import MarkdownIt


_markdown = MarkdownIt("commonmark").enable("table")


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"br", "p", "div", "tr", "td", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "tr", "td", "li"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _inline(tokens) -> str:
    parts = []
    for token in tokens or []:
        if token.type in {"text", "code_inline"}:
            parts.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
        elif token.type == "html_inline":
            parts.append(token.content)
        elif token.type == "image":
            parts.append(token.content)
    parser = _HTMLText()
    parser.feed("".join(parts))
    return "".join(parser.parts)


def normalize_text(text: str) -> str:
    return " ".join(normalize("NFC", unescape(text)).split()).casefold()


def visible(text: str) -> str:
    return normalize_text(_inline(_markdown.parseInline(text)[0].children))


@dataclass(frozen=True)
class Passage:
    variants: tuple[str, ...]
    context: str
    headings: tuple[str, ...] = ()
    table_header: str = ""
    raw: str = ""
    table_index: int = 0
    is_heading: bool = False


@lru_cache(maxsize=128)
def passages(markdown: str) -> tuple[Passage, ...]:
    lines = markdown.splitlines()
    result = []
    headings = []
    heading_level = 0
    table_header = ""
    in_header = False
    row = None
    row_map = None
    table_index = 0

    def context(line_map):
        raw = "\n".join(lines[line_map[0]:line_map[1]]) if line_map else ""
        return "\n\n".join([*(text for _, text in headings), table_header, raw]).strip()

    def add(variants, line_map, *, is_heading=False):
        raw = "\n".join(lines[line_map[0]:line_map[1]]) if line_map else ""
        result.append(Passage(variants, context(line_map), tuple(text for _, text in headings),
                              table_header, raw, table_index if table_header else 0, is_heading))

    for token in _markdown.parse(markdown):
        if token.type == "heading_open":
            heading_level = int(token.tag[1:])
        elif token.type == "heading_close":
            heading_level = 0
        elif token.type == "table_open":
            table_header = ""
            table_index += 1
        elif token.type == "table_close":
            table_header = ""
        elif token.type == "thead_open":
            in_header = True
        elif token.type == "thead_close":
            in_header = False
        elif token.type == "tr_open":
            row, row_map = [], token.map
        elif token.type == "tr_close":
            if in_header:
                table_header = " | ".join(row)
            variants = tuple(normalize_text(separator.join(row)) for separator in (" | ", ". ", ": ", " "))
            add(variants, row_map)
            row = None
        elif token.type == "inline":
            text = _inline(token.children)
            if row is not None:
                row.append(text.strip().removesuffix("."))
                continue
            if heading_level:
                headings = [(level, value) for level, value in headings if level < heading_level]
                headings.append((heading_level, text))
            # Older extracts sometimes contain a lone pipe row without the table delimiter.
            variants = [normalize_text(text)]
            if token.map and token.map[1] == token.map[0] + 1:
                raw_line = lines[token.map[0]].strip()
                if re.match(r"^(?:[-+*]|\d+[.)])\s+", raw_line):
                    variants.append(visible(raw_line))
            if "|" in token.content:
                cells = re.split(r"(?<!\\)\|", token.content.strip().strip("|"))
                variants.append(". ".join(visible(cell.strip().removesuffix(".")) for cell in cells))
            add(tuple(variants), token.map, is_heading=bool(heading_level))
        elif token.type in {"fence", "code_block", "html_block"}:
            parser = _HTMLText()
            parser.feed(token.content)
            add((normalize_text("".join(parser.parts)),), token.map)
    return tuple(result)


def find_passage(quote: str, text: str, passage_id: str | None = None, source_url: str | None = None) -> Passage | None:
    expected = visible(quote).removesuffix(".")
    if not expected:
        return None
    start = r"(?<![\w+\-−€$£.,])" if expected[:1].isdigit() else r"(?<!\w)"
    end = r"(?![\w%])" + (r"(?![.,]\d)" if expected[-1:].isdigit() else "")
    pattern = re.compile(start + re.escape(expected) + end)
    if passage_id is not None:
        if not source_url:
            return None
        entry = citation_passages({"url": source_url, "text": text}).get(passage_id)
        if not entry or not pattern.search(entry[0]):
            return None
        return Passage((entry[0],), entry[1])
    return next((passage for passage in passages(text) if any(pattern.search(value) for value in passage.variants)), None)


def citation_passages(source: dict) -> dict[str, tuple[str, str]]:
    return _citation_passages(source["url"], source["text"])


def indexed_passages(source: dict) -> dict[str, Passage]:
    catalog = {(quote, normalize_text(context)): identifier
               for identifier, (quote, context) in citation_passages(source).items()}
    result = {}
    for passage in passages(source["text"]):
        identifier = catalog.get((passage.variants[0], normalize_text(passage.context)))
        if identifier:
            result.setdefault(identifier, passage)
    return result


def render_source_passages(source: dict, passage_ids: set[str] | None = None) -> str:
    lines = []
    previous_headings = ()
    previous_table = 0
    for identifier, passage in indexed_passages(source).items():
        if passage_ids is not None and identifier not in passage_ids:
            continue
        common = 0
        for left, right in zip(previous_headings, passage.headings):
            if left != right:
                break
            common += 1
        is_heading = passage.is_heading
        lines.extend(passage.headings[common:-1] if is_heading else passage.headings[common:])
        if passage.table_header and passage.table_index != previous_table:
            if visible(passage.raw.strip().strip("|")) != visible(passage.table_header):
                lines.append(passage.table_header)
        lines.append(f"[{identifier}] {passage.raw}")
        previous_headings, previous_table = passage.headings, passage.table_index
    return "\n\n".join(lines)


@lru_cache(maxsize=128)
def _citation_passages(url: str, text: str) -> dict[str, tuple[str, str]]:
    entries = {}
    seen = set()
    for passage in passages(text):
        quote = passage.variants[0]
        identity = (quote, normalize_text(passage.context))
        if not quote or identity in seen:
            continue
        seen.add(identity)
        identifier = "P_" + hashlib.sha256((url + "\n" + "\n".join(identity)).encode()).hexdigest()[:16]
        entries[identifier] = (quote, passage.context)
    return entries
