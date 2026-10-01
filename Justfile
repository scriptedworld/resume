# One resume, two renderers, one command.
#
# `resume.md` is the only source. It is also a published artefact -- the
# Download Markdown link on the page serves this exact file -- so it carries no
# presentation. Both renderers read it:
#
#     build-pdf.py            the ATS document. Plain layout by design: no
#                             tables, no columns, no headers. Two pages.
#     src/resume_markdown/    the web page. Derived from mikepqr/resume-markdown
#                             under MIT; see NOTICE.
#
# This tree deliberately does NOT import toolbox's just/base.just. A public
# repository whose `just build` needs two sibling clones cannot be built by
# anybody who finds it, which is the first thing an outside reviewer hits. The
# recipe vocabulary matches the rest of the estate; the machinery does not.

set unstable := true

# The staging directory for the PDF build. It must NOT be this directory:
# build-pdf.py writes <name>.html beside its output and deletes it on the way
# out, which silently destroys the page builder's own resume.html.
pdf_stage := '.ephemera/pdf'

_default:
    @just --list

# both renderers, from the one source
build: html pdf

# the web page. Pages serves index.html, so that is what lands.
html:
    uv run --frozen resume-markdown build resume.md --no-pdf
    @mv resume.html index.html
    @echo "wrote index.html"

# the ATS document, staged away from the page builder's output
pdf:
    @mkdir -p {{pdf_stage}}
    python3 build-pdf.py resume.md --name resume --outdir {{pdf_stage}}
    @cp {{pdf_stage}}/resume.pdf resume.pdf
    @echo "wrote resume.pdf"

# everything that must be true before this is published
checks: leak-scan pages

# NOTHING PRIVATE REACHES A PUBLISHED ARTEFACT.
#
# The source this is derived from carries a home town, a phone number and a
# personal address. The public copy carries neither, and the only thing keeping
# them apart is one edited line. This asserts it against the built files rather
# than against the intention.

# no private contact detail in any published artefact
leak-scan:
    #!/usr/bin/env bash
    set -euo pipefail
    private='REDACTED|REDACTED|REDACTED'
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

# The PDF is a two page document. Three means something grew, and the page
# count is read from the page tree rather than by counting /Type /Page, which
# over-reports and has already produced one wrong answer here.

# the PDF still fits two pages
pages:
    #!/usr/bin/env bash
    set -euo pipefail
    n=$(python3 -c "
    import re, sys
    d = open('resume.pdf', 'rb').read()
    m = re.search(rb'/Count\s+(\d+)', d)
    sys.stdout.write(m.group(1).decode() if m else '0')
    ")
    echo "resume.pdf: $n pages"
    [ "$n" -le 2 ] || { echo "FAIL: the PDF must fit two pages"; exit 1; }

# look at the page
serve:
    @echo "http://localhost:8137/"
    python3 -m http.server 8137 --bind 127.0.0.1

# build outputs only. resume.md is source and is never removed.
clean:
    rm -rf .ephemera/pdf resume.html resume.pdf index.html
    @echo "clean: build outputs removed"
