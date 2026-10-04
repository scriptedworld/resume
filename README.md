# resume

My resume, as a web page and as a PDF, built from one markdown file.

    just build      both
    just checks     what must be true before it is published
    just serve      look at the page

## One source

`resume.md` is the only thing written by hand. It is also a published
artefact -- the Download Markdown link serves that exact file -- so it carries
no presentation markup: no hard line breaks, no HTML, no link syntax. Anything
the page needs in order to look like the page is added by the builder.

That constraint is load-bearing rather than tidiness. Six markdown hard breaks,
added to stop the skills block rendering as one run-on paragraph, looked free
and cost the PDF a whole page: what reads as a line break on screen is a forced
break on paper. The break belongs to the page, so it lives in the builder.

## Two renderers

    build-page.py           the page. Parsed with tree-sitter-markdown, whose
                            sections become the page's sections and articles.
    build-pdf.py            the PDF. Deliberately plain -- no tables, no
                            columns, no headers or footers, because applicant
                            tracking systems mangle all three. Two pages.

They are separate because they are different documents for different readers,
and one stylesheet trying to be both was worse at each. The page has a caret
that follows the pointer and a load-in sequence; the PDF has none of that, and
its print stylesheet exists only so that Ctrl+P on the page is legible.

**The PDF is staged under `.ephemera/`.** `build-pdf.py` writes its own
`<name>.html` beside its output and removes it on the way out, so pointed at
the repository root it would delete any HTML file of the same name there.

## What `just checks` asserts

**No private contact detail in any published artefact.** This resume is derived
from a private one carrying a home town, a phone number and a personal address.
The public copy carries a metro area and two profile links, and the only thing
separating them is one edited line. `leak-scan` reads the built files rather
than trusting that line, and it was verified by seeding a leak and watching it
fail rather than by watching it pass.

The patterns it looks for are the private detail itself, so they are not in
the repository: `LEAK_PATTERNS` in the environment, set from a secret in CI,
or else the first line of a gitignored `.leak-patterns`. With neither, the scan
fails.

**The PDF still fits two pages.** Read from the page tree's `/Count`, not by
counting `/Type /Page` occurrences, which over-reports: that method claimed
three pages for a two page document here and sent an investigation the wrong
way for an hour.

## Themes

`resume.css` is a symlink to whichever theme is live, and a symlink rather than
a copy because the copy went stale within the hour -- the builder reads
`resume.css` while the theme was being edited under its own name.

    ln -sf resume.encom.css resume.css && just build

`resume.tokyo-night.css` is the current one; `resume.encom.css` is a Tron
console alternative. Both are commented and deliberately not minified. The page
is about 10KB over the wire once gzipped, and the reasoning in the stylesheet is
worth more than the few kilobytes minifying it would save.

## Building it elsewhere

Needs `uv`, `python3`, `just`, and Chrome or Chromium for the PDF. Nothing else,
and no sibling repository: a public project that cannot be built by whoever
finds it is not really published.

## Licence

The tooling is Apache-2.0; see LICENSE. The resume itself, meaning
`resume.md`, `resume.pdf` and `index.html`, is all rights reserved: published
to be read, not licensed for reuse. NOTICE draws the line file by file.
