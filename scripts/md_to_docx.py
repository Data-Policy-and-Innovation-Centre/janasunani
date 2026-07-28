"""Render a repo Markdown doc to a DPIC-branded Word file.

Wraps `dpic.documents.build_brief`, whose Markdown dialect is deliberately small:
frontmatter, headings, paragraphs, tables, blockquotes, `*italic notes*`, rules,
figures, footnotes. It has **no list support** — `_starts_block` in
`dpic/documents/markdown.py` does not treat `-` or `1.` as a block start, so a run
of bullets collapses into one run-on paragraph.

Our engineering docs (ROADMAP.md especially) are bullet-heavy, so this normalises
them first: each list item becomes its own paragraph, blank-line separated, with a
bullet glyph and indentation by nesting depth. Everything else passes through
untouched, and fenced code blocks are protected.

Usage:
    uv run python scripts/md_to_docx.py docs/ROADMAP.md docs/ROADMAP.docx
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

BULLET = re.compile(r"^(?P<indent>\s*)(?P<marker>[-*+]|\d+[.)])\s+(?P<text>.*)$")
GLYPHS = ("\u2022", "\u25e6", "\u2023")  # bullet, white bullet, triangular bullet
FENCE = re.compile(r"^\s*(```|~~~)")
BLOCK_START = re.compile(r"^\s*(#{1,6}\s|\||>|!\[|<!--|---\s*$)")
# dpic's _add_runs renders bold/italic/code but not links, so `[a](b)` would show
# its own syntax. Keep the label; append the target only when it is a real URL.
LINK = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<target>[^)]+)\)")


def _delink(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        label, target = match.group("label"), match.group("target")
        if target.startswith(("http://", "https://")):
            return f"{label} ({target})"
        return label

    return LINK.sub(repl, text)


def normalise(text: str) -> str:
    """Expand list items into standalone paragraphs the dpic parser can see.

    Each item absorbs its wrapped continuation lines, so a bullet stays one
    paragraph instead of shedding orphans, and is emitted blank-line separated so
    the parser treats it as its own block.
    """
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    i = 0

    while i < len(lines):
        line = lines[i]

        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        if in_fence:
            out.append(line)
            i += 1
            continue

        match = BULLET.match(line)
        if not match:
            out.append(_delink(line))
            i += 1
            continue

        parts = [match.group("text").strip()]
        i += 1
        # Absorb wrapped continuation lines: indented, non-blank, and not the
        # start of another item or block.
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip() or BULLET.match(nxt) or BLOCK_START.match(nxt):
                break
            if not nxt.startswith((" ", "\t")):
                break
            parts.append(nxt.strip())
            i += 1

        depth = len(match.group("indent")) // 2
        marker = match.group("marker")
        glyph = marker if marker[0].isdigit() else GLYPHS[min(depth, len(GLYPHS) - 1)]
        if out and out[-1].strip():
            out.append("")
        out.append(_delink(f"{'  ' * depth}{glyph} {' '.join(parts)}"))
        out.append("")

    return "\n".join(out) + "\n"


def render(src: Path, dest: Path) -> Path:
    try:
        from dpic.documents import build_brief
    except ImportError as exc:  # pragma: no cover - environment guard
        sys.exit(f"dpic is not installed in this environment: {exc}\nTry: uv sync")

    dest.parent.mkdir(parents=True, exist_ok=True)
    # build_brief resolves figure paths relative to the source file, so keep the
    # temporary copy beside the original.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", dir=src.parent, encoding="utf-8", delete=True
    ) as tmp:
        tmp.write(normalise(src.read_text(encoding="utf-8")))
        tmp.flush()
        build_brief(tmp.name, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Markdown source")
    parser.add_argument("output", type=Path, help="DOCX destination")
    args = parser.parse_args()
    print(f"{args.input} -> {render(args.input, args.output)}")


if __name__ == "__main__":
    main()
