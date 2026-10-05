#!/usr/bin/env python3
"""Build resume.md into an ATS-friendly .pdf with Chrome, and a .docx where
LibreOffice exists.

Usage:  python3 build.py [source.md] [--name BASENAME] [--outdir DIR] [--runin]

Renders a deliberately plain document: no tables, no columns, no text boxes,
no headers/footers. Applicant tracking systems mangle all of those.

Three stages, so presentation decisions stop happening inside the parser:

    parse()     markdown -> generic blocks. Knows nothing about resumes.
    classify()  blocks   -> roles, using structure rather than pattern-guessing.
                An H3 is a position under EXPERIENCE and a project under
                PROJECTS; the same regex cannot tell those apart, but the
                enclosing H2 can.
    render()    roles    -> per-role div layouts. Line sharing is a property
                of the role, not a special case in the scanner.

The stage that earns its keep is classify(). The old single-pass scanner had
to fold date lines onto headings, sniff the contact line by position, and
guess a blurb from surrounding asterisks -- all presentation decisions taken
while parsing, each one a separate special case. Roles replace all three.
"""

import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

CSS = """
@page { margin: 0.55in 0.6in; }
body { font-family: Calibri, Carlito, sans-serif; font-size: 10pt;
       color: #000; line-height: 1.12; }

h1 { font-size: 20pt; margin: 0 0 2pt 0; font-weight: bold;
     letter-spacing: 0.5pt; }
h2 { font-size: 10.5pt; margin: 9pt 0 3pt 0; font-weight: bold;
     text-transform: uppercase; letter-spacing: 1pt;
     border-bottom: 1px solid #000; padding-bottom: 1pt;
     break-after: avoid; }
h3 { font-size: 11pt; margin: 8pt 0 1pt 0; font-weight: bold;
     break-after: avoid; }
h4 { font-size: 10pt; margin: 6pt 0 1pt 0; font-weight: bold;
     font-style: italic; break-after: avoid; }
p  { margin: 0 0 3pt 0; }
/* Résumé paragraphs are deliberately tight. A cover letter is continuous prose, so the same
   rhythm collapses distinct paragraphs into one grey block. Keep the contact line compact, then
   give each body paragraph enough separation to remain visible in the rendered PDF. */
body.cover-letter p { margin-bottom: 9pt; }
body.cover-letter p.contact { margin-bottom: 2pt; }
ul { margin: 0 0 3pt 0; padding-left: 14pt; }
/* 2.5pt, raised from 1.5pt on 2026-08-24. Bullets here run long, and at 1.5pt
   a block of them reads as one grey mass -- the "wordy" complaint from a cold
   read. The extra point also buys back the page-one fill that keeping the
   "Also at ServiceNow" heading with its bullets cost. */
li { margin: 0 0 2.5pt 0; break-inside: avoid; }

/* A paragraph immediately followed by a list is introducing it -- a subgroup
   heading, or the framing line above a set of bullets. Keep them together.
   The h4 rule below does not cover this: a subgroup heading renders as
   <p><b>…</b></p> rather than an <h4>, so `h4 { break-after: avoid }` never
   applied to it and "Also at ServiceNow" stranded alone at the foot of a page
   with all five of its bullets overleaf. */
p + ul { break-before: avoid; }

/* The mirror case: a paragraph that FOLLOWS a list is starting something new,
   not continuing the list. Both instances are run-in entries -- "Also at
   ServiceNow" and the "Earlier:" line -- and with only the default 0 top margin
   they butt straight up against the last bullet and read as part of it. */
ul + p { margin-top: 7pt; }

span.when { font-weight: normal; font-size: 9.5pt; }
span.tag  { font-weight: normal; font-style: italic; }
p.contact { margin: 0 0 2pt 0; font-size: 9.5pt; }
p.blurb   { margin: 0 0 2pt 0; font-size: 9pt; color: #333; }

/* Run-in roles, used only with --runin. The wrapper div is load-bearing:
   vertical margins do not apply to inline boxes, so the div is the only
   element left that can carry the rhythm. A run-in heading also has to drop
   to the body font size, or the taller line box eats most of what sharing
   the line saved. CSS `display: run-in` is not an option -- Blink removed
   it, so inline children of a block wrapper is the working equivalent. */
.position { margin: 8pt 0 1pt 0; break-inside: avoid; }
.position > h3, .position > .when { display: inline; margin: 0; }
.position > .when::before { content: " \\00b7  "; }

.subgroup { margin: 5pt 0 3pt 0; }
.subgroup > h4 { display: inline; margin: 0; font-size: 10pt;
                 font-style: normal; }
.subgroup > p  { display: inline; }
.subgroup > h4::after { content: " \\2014  "; }

.project { margin: 4pt 0 0 0; }
.project > h3, .project > p { display: inline; margin: 0; }
.project > h3 { font-size: 10pt; }
.project > h3::after { content: " \\2014  "; }
"""

INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"<b>\1</b>"),
    (re.compile(r"(?<![\w*])\*([^*]+?)\*(?![\w*])"), r"<i>\1</i>"),
    # Tool names read fine unadorned; backticks would render literally.
    (re.compile(r"`([^`]+?)`"), r"\1"),
]

# "Aug 2021 – Present · Seattle, WA" / "Dec 2019 – Aug 2021" / "2015 – 2017".
DATE_LINE = re.compile(
    r"^[A-Z][a-z]{2,8}\.?\s+\d{4}\s*[–—-]\s*"
    r"(Present|[A-Z][a-z]{2,8}\.?\s+\d{4})\b"
    r"|^\d{4}\s*[–—-]\s*(\d{4}|Present)\b"
)

# "kharma *(open source, Ruby)*" -> the parenthetical is a tag, not a title.
TRAILING_TAG = re.compile(r"^(.*?)\s*\*\(([^)]+)\)\*$")

# The H2 is the only thing that tells a project apart from a job, so renaming that
# heading silently reclassifies every entry under it. On 2026-08-28 the page moved
# PROJECTS to SELECTED ENGINEERING and career.json went from 7 roles and 3 projects
# to 10 roles and 0, while the claim count stayed at 27 and every gate stayed green.
PROJECT_SECTIONS = {"PROJECTS", "SELECTED ENGINEERING"}


def inline(text):
    out = html.escape(text)
    for pattern, repl in INLINE:
        out = pattern.sub(repl, out)
    return out


def is_blurb(text):
    """A whole-line italic paragraph, e.g. the org line under a job title."""
    return (text.startswith("*") and text.endswith("*")
            and not text.startswith("**"))


# ---- stage 1: markdown -> generic blocks ------------------------------------

def parse(md):
    """Markdown subset -> ('heading', level, text, line) | ('para', text, line)
    | ('list', [(line, text), ...], line). No resume knowledge lives here.

    Every block carries its 1-based source line. Rendering never needs it, but
    check_trace.py reads the same AST and has to name the line a claim sits on.
    """
    blocks, items = [], []

    def flush():
        if items:
            blocks.append(("list", list(items), items[0][0]))
            items.clear()

    for lineno, raw in enumerate(md.splitlines(), start=1):
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("- "):
            items.append((lineno, line[2:]))
            continue
        flush()
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            blocks.append(
                ("heading", len(heading.group(1)), heading.group(2), lineno))
        else:
            blocks.append(("para", line, lineno))
    flush()
    return blocks


# ---- stage 2: blocks -> roles -----------------------------------------------

def classify(blocks):
    """Attach a resume role to each block. Structure decides: the enclosing
    H2 is what separates a job title from a project name.

    Every role carries its source line as its last element, so consumers slice
    the fields they want rather than unpacking the whole tuple."""
    roles, section, i = [], None, 0

    def para_at(j):
        return blocks[j][1] if j < len(blocks) and blocks[j][0] == "para" else None

    while i < len(blocks):
        kind, line = blocks[i][0], blocks[i][-1]

        if kind == "heading":
            level, text = blocks[i][1], blocks[i][2]

            if level == 1:
                roles.append(("name", text, line))
                # The contact line is the paragraph right after the name.
                contact = para_at(i + 1)
                if contact:
                    roles.append(("contact", contact, blocks[i + 1][-1]))
                    i += 2
                    continue
                i += 1
                continue

            if level == 2:
                section = text.upper()
                roles.append(("section", text, line))
                i += 1
                continue

            if level == 3 and section in PROJECT_SECTIONS:
                body = para_at(i + 1) or ""
                roles.append(("project", text, body, line))
                i += 2 if body else 1
                continue

            if level == 3:
                # A position absorbs its optional date line and optional
                # italic org blurb; anything else stays a block of its own.
                date, blurb, j = "", "", i + 1
                nxt = para_at(j)
                if nxt and DATE_LINE.match(nxt):
                    date, j = nxt, j + 1
                nxt = para_at(j)
                if nxt and is_blurb(nxt):
                    blurb, j = nxt, j + 1
                # A position whose work is one prose paragraph rather than a
                # bullet list is structurally a project: Gladesoft and the
                # pre-2004 line both are. Run its body in, or each costs a
                # line and a heading margin that the bulleted jobs earn and
                # these do not. The paragraph must be the whole entry -- a
                # paragraph that introduces a bullet list belongs to the
                # list, not to the heading, and merging it puts a job title
                # and a framing sentence on the same line.
                body = ""
                if para_at(j) and not (j + 1 < len(blocks)
                                       and blocks[j + 1][0] == "list"):
                    body, j = para_at(j), j + 1
                roles.append(("position", text, date, blurb, body, line))
                i = j
                continue

            if level == 4:
                # An H4 followed by prose is a run-in subgroup; one followed
                # straight by a list is just a label.
                body = para_at(i + 1) or ""
                roles.append(("subgroup", text, body, line))
                i += 2 if body else 1
                continue

        roles.append(("list" if kind == "list" else "para", blocks[i][1], line))
        i += 1
    return roles


# ---- stage 3: roles -> div layouts ------------------------------------------

def split_tag(text):
    """'kharma *(open source, Ruby)*' -> ('kharma', 'open source, Ruby')."""
    m = TRAILING_TAG.match(text)
    return (m.group(1), m.group(2)) if m else (text, "")


def render(roles, runin=False, document_class=""):
    """Default output merges run-in roles at emit time. That is what actually
    holds two pages -- CSS run-in reclaims the same lines but spends them back
    on wrapper margins, and it does not survive LibreOffice's HTML import at
    all. Note what flattening does and does not touch: positions keep their
    real <h3>, so the EXPERIENCE structure an ATS parses is unchanged. Only
    PROJECTS entries and H4 sub-groupings flatten, and neither is a job title.
    """
    out = []
    for role in roles:
        kind = role[0]

        if kind == "name":
            out.append(f"<h1>{inline(role[1])}</h1>")

        elif kind == "contact":
            out.append(f'<p class="contact">{inline(role[1])}</p>')

        elif kind == "section":
            out.append(f"<h2>{inline(role[1])}</h2>")

        elif kind == "para":
            out.append(f"<p>{inline(role[1])}</p>")

        elif kind == "list":
            items = "".join(f"<li>{inline(x)}</li>" for _, x in role[1])
            out.append(f"<ul>{items}</ul>")

        elif kind == "position":
            title, date, blurb, body = role[1:5]
            head, tag = split_tag(title)
            head = inline(head) + (
                f' <span class="tag">({inline(tag)})</span>' if tag else "")
            when_inline = (f' <span class="when">· {inline(date)}</span>'
                           if date else "")
            if body and not runin:
                # Brief position: heading, date and body share one line, the
                # shape these entries had before they became headings. The
                # date joins the tag parenthetical rather than adding a third
                # separator to a line that already carries two.
                paren = ", ".join(x for x in (tag, date) if x)
                lead = inline(split_tag(title)[0])
                lead = f"<b>{lead}</b>" + (
                    f' <span class="tag">({inline(paren)})</span>'
                    if paren else "")
                # Middle dot, not an em-dash: content-rules 4d bans em-dashes in
                # every outward-facing document, and this joiner was putting them
                # into the PDF while the markdown and check_content stayed clean.
                # Not an arrow either -- the page already uses -> for migrations
                # ("Python 3.6 -> 3.12"), and the dot already separates fields in
                # the position line ("Aug 2021 - Present . Seattle, WA").
                out.append(f"<p>{lead} · {inline(body)}</p>")
                body = ""
            elif runin:
                inner = f"<h3>{head}</h3>"
                if date:
                    inner += f'<span class="when">{inline(date)}</span>'
                if body:
                    inner += f"<p>{inline(body)}</p>"
                out.append(f'<div class="position">{inner}</div>')
                body = ""
            else:
                out.append(f"<h3>{head}{when_inline}</h3>")
            if blurb:
                out.append(f'<p class="blurb">{inline(blurb)}</p>')
            if body:
                out.append(f"<p>{inline(body)}</p>")

        elif kind in ("project", "subgroup"):
            title, body = role[1:3]
            tag_level = "h3" if kind == "project" else "h4"
            head, tag = split_tag(title)
            head = inline(head) + (
                f' <span class="tag">({inline(tag)})</span>' if tag else "")
            if runin:
                inner = f"<{tag_level}>{head}</{tag_level}>"
                if body:
                    inner += f"<p>{inline(body)}</p>"
                out.append(f'<div class="{kind}">{inner}</div>')
            else:
                # The tag sits outside the bold: it is a qualifier, not part
                # of the name, and bold-italic reads as a second emphasis.
                lead = f"<b>{inline(split_tag(title)[0])}</b>" + (
                    f' <span class="tag">({inline(tag)})</span>' if tag else "")
                sep = " · " if body else ""   # see the note on the position joiner
                out.append(f"<p>{lead}{sep}{inline(body)}</p>")

    body_class = f' class="{document_class}"' if document_class else ""
    return ("<html><head><meta charset='utf-8'>"
            f"<style>{CSS}</style></head><body{body_class}>\n"
            + "\n".join(out) + "\n</body></html>")


def to_html(md, runin=False, document_class=""):
    return render(classify(parse(md)), runin, document_class)


# ---- output -----------------------------------------------------------------

def convert(html_path, outdir, fmt):
    """The .docx renderer. LibreOffice needs the Writer import filter named explicitly, or it loads
    HTML into Writer/Web, which has no .docx export filter."""
    subprocess.run(
        ["soffice", "--headless", "--infilter=HTML (StarWriter)",
         "--convert-to", fmt, "--outdir", str(outdir), str(html_path)],
        check=True, capture_output=True,
    )


def find(*names):
    for name in names:
        if shutil.which(name):
            return name
    return None


def stamp_continued(pdf_path, text="continued..."):
    """Put a continuation marker in the bottom margin of every page but the last.

    This is done after rendering rather than in CSS because neither engine can
    target one page: Blink implements no `@page` margin boxes, and a
    `position: fixed` element repeats on all of them. Stamping the produced PDF
    is the only place the page count is known.

    Silent no-op on a single-page document, which is what a cover letter is.
    """
    try:
        import pymupdf
    except ImportError:
        print("note: pymupdf not installed, so no 'continued' marker was added.")
        return

    doc = pymupdf.open(pdf_path)
    if doc.page_count < 2:
        doc.close()
        return

    for page in doc.pages(0, doc.page_count - 1):
        rect = page.rect
        # 0.30in up from the trim edge sits inside the 0.55in @page margin,
        # so the marker cannot collide with body text at any fill level.
        page.insert_text(
            pymupdf.Point(rect.width - 72 * 0.6 - 46, rect.height - 72 * 0.30),
            text, fontname="helv", fontsize=8, color=(0.4, 0.4, 0.4),
        )

    doc.saveIncr()
    doc.close()


def convert_pdf_chrome(html_path, out_path, browser):
    """The PDF renderer. Chrome honours the @page margins in CSS;
    --no-pdf-header-footer drops the URL and date it would otherwise stamp on
    every page."""
    subprocess.run(
        [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={out_path}", html_path.resolve().as_uri()],
        check=True, capture_output=True,
    )


def main():
    args = list(sys.argv[1:])
    runin = "--runin" in args
    if runin:
        args.remove("--runin")

    def take(flag, default=None):
        if flag in args:
            i = args.index(flag)
            value = args[i + 1]
            del args[i:i + 2]
            return value
        return default

    name = take("--name", "Jeff_Wood_resume_2026_08")
    outdir_arg = take("--outdir")

    src = Path(args[0]) if args else Path(__file__).parent / "resume.md"
    outdir = Path(outdir_arg) if outdir_arg else src.parent
    md = src.read_text(encoding="utf-8")

    if "‹" in md:
        stubs = len(re.findall(r"‹[^›]*›", md))
        print(f"note: {stubs} unfilled ‹placeholder›(s) still in {src.name}")

    staging = outdir / f"{name}.html"
    document_class = "cover-letter" if "cover-letter" in src.stem else ""
    staging.write_text(to_html(md, runin, document_class), encoding="utf-8")

    # Chrome renders the PDF, always. It is what the published copy was built
    # with and what fits two pages; LibreOffice renders the same source at
    # three, so it writes only the .docx.
    browser = find("google-chrome", "google-chrome-stable", "chromium",
                   "chromium-browser")
    if not browser:
        staging.unlink()
        sys.exit("error: need Chrome or Chromium to render the PDF.")

    pdf = outdir / f"{name}.pdf"
    convert_pdf_chrome(staging, pdf, browser)
    stamp_continued(pdf)
    print(f"wrote {pdf}")

    if find("soffice"):
        convert(staging, outdir, "docx:MS Word 2007 XML")
        print(f"wrote {outdir / (name + '.docx')}")
    else:
        print("note: no soffice, so no .docx was written. Install\n"
              "      libreoffice-writer if a .docx is needed for an ATS upload.")
    staging.unlink()


if __name__ == "__main__":
    main()
