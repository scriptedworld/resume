# One resume, two renderers, one command.
#
# `resume.md` is the only source. The Download Markdown link on the page serves
# this exact file, so it is also a published artefact and carries no
# presentation. Both renderers read it:
#
#     build-pdf.py            the ATS document. Plain layout by design: no
#                             tables, no columns, no headers. Two pages.
#     build-page.py           the web page, parsed with tree-sitter-markdown.
#
# This tree deliberately does NOT import toolbox's just/base.just. A public
# repository whose `just build` needs two sibling clones cannot be built by
# anybody who finds it, which is the first thing an outside reviewer hits. The
# recipe vocabulary matches the rest of the estate; the machinery does not.

set unstable := true

# The staging directory for the PDF build, kept away from the published files:
# build-pdf.py writes <name>.html beside its output and deletes it on the way
# out, which would silently remove any page of that name here.
pdf_stage := '.ephemera/pdf'

_default:
    @just --list

# both renderers, from the one source
build: html pdf

# the web page. Pages serves index.html, so that is what lands.
html:
    uv run --frozen python build-page.py resume.md --css resume.css --out index.html

# the ATS document, staged away from the page builder's output
pdf:
    @mkdir -p {{pdf_stage}}
    python3 build-pdf.py resume.md --name resume --outdir {{pdf_stage}}
    @cp {{pdf_stage}}/resume.pdf resume.pdf
    @echo "wrote resume.pdf"

# everything that must be true before this is published
checks: leak-scan pages

# Nothing private reaches a published artefact.
#
# The source this is derived from carries a home town, a phone number and a
# personal address. The public copy carries neither, and the only thing keeping
# them apart is one edited line. This asserts it against the built files rather
# than against the intention.
#
# The patterns are themselves the private detail, so they are not in this
# repository. They come from $LEAK_PATTERNS, which CI sets from a secret, or
# else from the gitignored .leak-patterns, one extended regex on one line. With
# neither the scan fails: a leak check with nothing to look for passes every
# file, which is worse than no check.

# no private contact detail in any published artefact
leak-scan:
    #!/usr/bin/env bash
    set -euo pipefail
    private="${LEAK_PATTERNS:-}"
    if [ -z "$private" ] && [ -f .leak-patterns ]; then
        private=$(head -n 1 .leak-patterns)
    fi
    if [ -z "$private" ]; then
        echo "leak-scan: no patterns; set LEAK_PATTERNS or write .leak-patterns"
        exit 1
    fi
    fail=0
    for f in resume.md index.html; do
        [ -f "$f" ] || continue
        if grep -qEi "$private" "$f"; then
            echo "LEAK: $f carries private contact detail"
            fail=1
        fi
    done
    # Extract first and fail if that fails. Piped straight into grep, an
    # extraction error reads as text with no leak in it.
    if [ -f resume.pdf ]; then
        if ! text=$(uvx --from pdfminer.six pdf2txt.py resume.pdf); then
            echo "leak-scan: could not extract text from resume.pdf"
            exit 1
        fi
        if printf '%s' "$text" | grep -qEi "$private"; then
            echo "LEAK: resume.pdf carries private contact detail"
            fail=1
        fi
    fi
    [ "$fail" = 0 ] && echo "leak-scan: clean"
    exit "$fail"

# The PDF is a two page document. Three means something grew. The count is
# the /Count of the root /Pages node, the one with no /Parent. Counting
# /Type /Page over-reports, and the first /Count in the file can belong to the
# bookmark outline instead: both have produced a wrong answer here.

# the PDF still fits two pages
pages:
    #!/usr/bin/env bash
    set -euo pipefail
    n=$(python3 -c "
    import re, sys
    d = open('resume.pdf', 'rb').read()
    n = '0'
    for m in re.finditer(rb'<<(?:(?!>>).)*?/Type\s*/Pages\b.*?>>', d, re.S):
        if b'/Parent' not in m.group(0):
            c = re.search(rb'/Count\s+(\d+)', m.group(0))
            n = c.group(1).decode() if c else '0'
    sys.stdout.write(n)
    ")
    echo "resume.pdf: $n pages"
    [ "$n" -le 2 ] || { echo "FAIL: the PDF must fit two pages"; exit 1; }

# look at the page
serve:
    @echo "http://localhost:8137/"
    python3 -m http.server 8137 --bind 127.0.0.1

# build outputs only. resume.md is source and is never removed.
clean:
    rm -rf .ephemera/pdf resume.pdf index.html
    @echo "clean: build outputs removed"
