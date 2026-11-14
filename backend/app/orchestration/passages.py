import re
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

    def context(line_map):
        raw = "\n".join(lines[line_map[0]:line_map[1]]) if line_map else ""
        return "\n\n".join([*headings, table_header, raw]).strip()

    for token in _markdown.parse(markdown):
        if token.type == "heading_open":
            heading_level = int(token.tag[1:])
        elif token.type == "heading_close":
            heading_level = 0
        elif token.type == "table_open":
            table_header = ""
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
            result.append(Passage(variants, context(row_map)))
            row = None
        elif token.type == "inline":
            text = _inline(token.children)
            if row is not None:
                row.append(text.strip().removesuffix("."))
                continue
            if heading_level:
                headings = headings[:heading_level - 1] + [text]
            # Older extracts sometimes contain a lone pipe row without the table delimiter.
            variants = [normalize_text(text)]
            if "|" in token.content:
                cells = re.split(r"(?<!\\)\|", token.content.strip().strip("|"))
                variants.append(". ".join(visible(cell.strip().removesuffix(".")) for cell in cells))
            result.append(Passage(tuple(variants), context(token.map)))
        elif token.type in {"fence", "code_block", "html_block"}:
            parser = _HTMLText()
            parser.feed(token.content)
            result.append(Passage((normalize_text("".join(parser.parts)),), context(token.map)))
    return tuple(result)


def find_passage(quote: str, text: str) -> Passage | None:
    expected = visible(quote).removesuffix(".")
    if not expected:
        return None
    start = r"(?<![\w+\-−€$£.,])" if expected[:1].isdigit() else r"(?<!\w)"
    end = r"(?![\w%])" + (r"(?![.,]\d)" if expected[-1:].isdigit() else "")
    pattern = re.compile(start + re.escape(expected) + end)
    return next((passage for passage in passages(text) if any(pattern.search(value) for value in passage.variants)), None)
