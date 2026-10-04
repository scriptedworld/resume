#!/usr/bin/env python3
"""Build resume.md into the web page.

Usage:  build-page.py SOURCE.md --css STYLE.css --out PAGE.html

The markdown is parsed with tree-sitter-markdown, whose block grammar already
nests the document into sections by heading level. That nesting is exactly
the structure the stylesheet needs: each h2 becomes a <section>, and each h3
inside one becomes an <article>, so the pointer can ask which section and
which position it is in with CSS alone.

Everything the page adds on top of the markdown is presentation the source
must not carry, because resume.md is itself published and is also read by
build-pdf.py: the profile links on the contact line, one row per labelled
skills entry, a class on each date line, typographic apostrophes, and a
reading-order index for the load-in sequence.
"""

import argparse
import itertools
import re
from pathlib import Path

import tree_sitter_markdown
from tree_sitter import Language, Node, Parser

BLOCK = Parser(Language(tree_sitter_markdown.language()))
INLINE = Parser(Language(tree_sitter_markdown.inline_language()))

HEAD = """\
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
<div id="resume">
"""

DOWNLOADS = """
<nav class="downloads">
<a href="resume.pdf" download>Download PDF</a>
<a href="resume.md" download>Download Markdown</a>
<a href="https://github.com/scriptedworld">Source on GitHub</a>
</nav>
"""

FOOT = """\
</div>
</body>
</html>
"""

PROFILE = re.compile(r"\b(linkedin\.com/in/[\w-]+|github\.com/[\w-]+)\b")

# A date line opens with a month and a year. Matching the text is the only
# honest test: position alone would class the prose under "Earlier: ..." as a
# date, since that heading has no dates beneath it.
DATE_LINE = re.compile(r"[A-Z][a-z]{2}\s+\d{4}\s*[–—-]")

WRAP = {"emphasis": "em", "strong_emphasis": "strong"}


# Text

def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def smarten(text: str, before: str) -> str:
    """
    Typographic quotes, dashes and ellipses for one run of plain text.

    `before` is the character preceding the run, so a quote at the edge of an
    emphasis span still reads its neighbour.
    """
    text = text.replace("---", "&mdash;").replace("--", "&ndash;")
    text = text.replace("...", "&hellip;")
    out = []
    for i, ch in enumerate(text):
        if ch not in "'\"":
            out.append(ch)
            continue
        prev = text[i - 1] if i else before
        opening = not prev or prev.isspace() or prev in "([{"
        if ch == "'":
            out.append("&lsquo;" if opening else "&rsquo;")
        else:
            out.append("&ldquo;" if opening else "&rdquo;")
    return "".join(out)


# Inline

def inline_pieces(source: bytes) -> list[tuple[str, str]]:
    """
    Render one inline run as (kind, html) pieces in order.

    The kind is the node type for a span and "text" between spans, which is
    what lets the skills paragraph be split at each bold label without
    re-parsing the HTML it became.
    """
    root = INLINE.parse(source).root_node
    return render_children(root, source, root.start_byte, root.end_byte)


def render_children(node: Node, source: bytes, start: int, end: int) -> list[tuple[str, str]]:
    pieces = []
    cursor = start
    spans = [c for c in node.children if c.type in WRAP or c.type == "code_span"]
    for span in spans:
        pieces.append(("text", plain(source, cursor, span.start_byte)))
        pieces.append((span.type, render_span(span, source)))
        cursor = span.end_byte
    pieces.append(("text", plain(source, cursor, end)))
    return [p for p in pieces if p[1]]


def render_span(span: Node, source: bytes) -> str:
    delimiters = [c for c in span.children if c.type.endswith("_delimiter")]
    inner_start = delimiters[len(delimiters) // 2 - 1].end_byte
    inner_end = delimiters[len(delimiters) // 2].start_byte
    if span.type == "code_span":
        return "<code>" + escape(source[inner_start:inner_end].decode()) + "</code>"
    inner = render_children(span, source, inner_start, inner_end)
    tag = WRAP[span.type]
    return "<" + tag + ">" + "".join(h for _, h in inner) + "</" + tag + ">"


def plain(source: bytes, start: int, end: int) -> str:
    before = source[:start].decode()[-1:]
    return smarten(escape(source[start:end].decode()), before)


def render_inline(node: Node, source: bytes) -> str:
    return "".join(h for _, h in inline_pieces(source[node.start_byte:node.end_byte]))


def label_rows(node: Node, source: bytes) -> str | None:
    """
    One row per bold label, where a paragraph holds two or more.

    Markdown joins consecutive lines into one paragraph, so a skills block
    written one label per line arrives as a single run-on. Hard breaks in the
    source would fix the page and cost the PDF a whole page, since a line
    break on screen is a forced break on paper. A row the stylesheet lays out
    is presentation, so it lives here.
    """
    pieces = inline_pieces(source[node.start_byte:node.end_byte])
    if sum(kind == "strong_emphasis" for kind, _ in pieces) < 2:
        return None
    rows: list[str] = []
    for kind, html in pieces:
        if kind == "strong_emphasis" or not rows:
            rows.append(html)
        else:
            rows[-1] += html
    return "".join('<span class="row">' + r.strip() + "</span>" for r in rows if r.strip())


def link_profiles(html: str) -> str:
    """
    Link the bare profile handles on the contact line.

    The line is plain text in the source because build-pdf.py prints link
    syntax literally and the downloadable markdown should read cleanly.
    """

    def anchor(match: re.Match[str]) -> str:
        handle = match.group(1)
        if handle.startswith("linkedin"):
            href = "https://www." + handle + "/"
        else:
            href = "https://" + handle
        return '<a href="' + href + '">' + handle + "</a>"

    return PROFILE.sub(anchor, html)


# Blocks

def child_inline(node: Node) -> Node:
    return next(c for c in node.children if c.type == "inline")


def heading_level(node: Node) -> int:
    marker = node.children[0].type
    return int(marker[len("atx_h")])


def render_block(node: Node, source: bytes) -> list[str]:
    """Render one block, or every block inside a nested section, flat."""
    if node.type == "atx_heading":
        n = heading_level(node)
        return ["<h%d>%s</h%d>" % (n, render_inline(child_inline(node), source), n)]
    if node.type == "paragraph":
        inline = child_inline(node)
        body = label_rows(inline, source) or render_inline(inline, source)
        return ["<p>" + body + "</p>"]
    if node.type == "list":
        return [render_list(node, source)]
    if node.type == "section":
        return [html for child in node.children for html in render_block(child, source)]
    return []


def render_list(node: Node, source: bytes) -> str:
    items = [c for c in node.children if c.type == "list_item"]
    ordered = items and items[0].children[0].type.startswith(("list_marker_dot", "list_marker_paren"))
    tag = "ol" if ordered else "ul"
    loose = any(b"\n\n" in source[i.start_byte:i.end_byte].rstrip() for i in items[:-1])
    lines = []
    for item in items:
        blocks = [b for c in item.children for b in render_block(c, source)]
        if not loose:
            blocks = [b[3:-4] if b.startswith("<p>") else b for b in blocks]
        lines.append("<li>" + "\n".join(blocks) + "</li>")
    return "<" + tag + ">\n" + "\n".join(lines) + "\n</" + tag + ">"


# Structure

def subsections(node: Node, level: int) -> list[Node]:
    return [
        c for c in node.children
        if c.type == "section" and heading_level(c.children[0]) == level
    ]


def render_article(section: Node, source: bytes, index: int) -> str:
    blocks = [html for child in section.children for html in render_block(child, source)]
    if len(blocks) > 1 and DATE_LINE.match(blocks[1], len("<p>")):
        blocks[1] = '<p class="dates">' + blocks[1][len("<p>"):]
    return '<article style="--i:%d">\n' % index + "\n".join(blocks) + "\n</article>\n"


def render_section(section: Node, source: bytes, counter: "itertools.count[int]") -> str:
    """
    One h2 and what follows it, classed by whether it holds positions.

    The class saves the stylesheet from asking :not(:has()) inside :has(),
    which is hard to read and easy to get silently wrong.
    """
    index = next(counter)
    positions = subsections(section, 3)
    if not positions:
        blocks = [html for child in section.children for html in render_block(child, source)]
        return '<section class="plain" style="--i:%d">\n' % index + "\n".join(blocks) + "\n</section>\n"
    head = "".join(
        html + "\n"
        for child in section.children
        if child.type != "section"
        for html in render_block(child, source)
    )
    articles = "".join(render_article(p, source, next(counter)) for p in positions)
    return '<section class="positions" style="--i:%d">\n' % index + head + articles + "\n</section>\n"


def render_body(source: bytes) -> tuple[str, str]:
    """
    The page body, and the document title taken from its h1.

    Anything before the first h2, the name and the contact line, belongs to
    no section and is left outside one.
    """
    document = BLOCK.parse(source).root_node
    top = next(c for c in document.children if c.type == "section")
    heading = top.children[0]
    title = source[child_inline(heading).start_byte:child_inline(heading).end_byte].decode()
    head = []
    for child in top.children:
        if child.type == "section":
            continue
        head.extend(render_block(child, source))
    if len(head) > 1:
        head[1] = link_profiles(head[1])
    counter = itertools.count()
    sections = "".join(render_section(s, source, counter) for s in subsections(top, 2))
    return "".join(h + "\n" for h in head) + sections, title


def build(markdown: str, css: str) -> str:
    body, title = render_body(markdown.encode())
    return HEAD.format(title=title, css=css) + body + DOWNLOADS + FOOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the resume web page.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--css", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    page = build(args.source.read_text(encoding="utf-8"), args.css.read_text(encoding="utf-8"))
    args.out.write_text(page, encoding="utf-8")
    print("wrote " + str(args.out))


if __name__ == "__main__":
    main()
