"""Screenshot every slide of a rendered reveal.js deck, so it can be looked at.

A deck can be structurally perfect and visually broken. `slides.html` renders,
the DOM is right, the CSS compiles -- and every title is at the bottom of the
page because one rule fought reveal's own layout. That happened; checking the
compiled CSS did not catch it, because the bug was in the interaction rather
than in either side of it. Nothing short of rendering the page finds that class
of problem.

Playwright is NOT a repository dependency: it is needed only when someone is
building a deck, and it pulls a browser down with it. Run it ephemerally.

    uv run --with playwright playwright install chromium     # once
    uv run --with playwright python scripts/screenshot_deck.py \\
        docs/presentations/2026-08-17-value-add

PNGs land in `outputs/deck-shots/<deck>/` (gitignored) at the deck's own
configured size, one per slide, numbered so they sort in presentation order.

Fragments are disabled before capture. Incremental reveal is right for
presenting and wrong for checking: it hides most of a slide's content, which is
exactly what you are trying to look at.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720


def _configured_size(deck: Path) -> tuple[int, int]:
    """Read width/height out of _quarto.yml so shots match the real deck."""
    config = deck / "_quarto.yml"
    if not config.exists():
        return DEFAULT_WIDTH, DEFAULT_HEIGHT
    text = config.read_text(encoding="utf-8")

    def _one(key: str, fallback: int) -> int:
        match = re.search(rf"^\s*{key}:\s*(\d+)\s*$", text, re.MULTILINE)
        return int(match.group(1)) if match else fallback

    return _one("width", DEFAULT_WIDTH), _one("height", DEFAULT_HEIGHT)


# Measuring overflow has one trap worth naming. reveal SCALES `.slides` with a
# CSS transform, so `getBoundingClientRect()` returns post-transform viewport
# pixels while `clientHeight` is pre-transform layout pixels. Comparing the two
# reports every slide as comfortably inside its own frame while content visibly
# runs off the bottom. Compare against `window.innerHeight` and nothing else.
_OVERFLOW_JS = """() => {
  const s = document.querySelector('.reveal .slides section.present');
  const h = s.querySelector('h1,h2');
  let bottom = 0, culprit = '';
  s.querySelectorAll('*').forEach(e => {
    const r = e.getBoundingClientRect();
    if (r.height > 2 && r.bottom > bottom) { bottom = r.bottom; culprit = e.className || e.tagName; }
  });
  return {
    over: Math.round(bottom - window.innerHeight),
    title: h ? h.textContent.trim() : '(no heading)',
    culprit: String(culprit).slice(0, 30),
  };
}"""


def check(deck: Path, margin: int = 0) -> int:
    """Report slides whose content runs past the bottom. Returns the count."""
    from playwright.sync_api import sync_playwright

    slides = deck / "slides.html"
    if not slides.exists():
        raise SystemExit(f"{slides} does not exist. Run `make deck DECK={deck.name}` first.")
    width, height = _configured_size(deck)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(slides.resolve().as_uri())
        page.wait_for_selector(".reveal .slides section.present", timeout=30_000)
        page.evaluate("Reveal.configure({fragments: false, transition: 'none'})")
        total = page.evaluate("Reveal.getTotalSlides()")

        over = []
        print(f"{'#':>3} {'px past bottom':>15}  title")
        for index in range(total):
            page.evaluate(f"Reveal.slide({index})")
            page.wait_for_timeout(120)
            row = page.evaluate(_OVERFLOW_JS)
            flag = "  OVERFLOWS" if row["over"] > margin else ""
            if row["over"] > margin:
                over.append(index)
            print(f"{index:>3} {row['over']:>15}  {row['title'][:44]:44s} {row['culprit']}{flag}")
        browser.close()

    print(f"\n{len(over)} slide(s) overflow: {over}" if over else "\nno slide overflows")
    return len(over)


def capture(deck: Path, out_dir: Path, only: list[int] | None = None) -> int:
    from playwright.sync_api import sync_playwright

    slides = deck / "slides.html"
    if not slides.exists():
        raise SystemExit(f"{slides} does not exist. Run `make deck DECK={deck.name}` first.")

    width, height = _configured_size(deck)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.png"):
        stale.unlink()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(slides.resolve().as_uri())
        page.wait_for_selector(".reveal .slides section.present", timeout=30_000)

        # Show everything on each slide. `incremental: true` is right for
        # presenting and wrong for inspection.
        page.evaluate("Reveal.configure({fragments: false, transition: 'none'})")
        total = page.evaluate("Reveal.getTotalSlides()")

        wanted = only or list(range(total))
        written = 0
        for index in wanted:
            if index >= total:
                print(f"  slide {index} does not exist (deck has {total})", file=sys.stderr)
                continue
            page.evaluate(f"Reveal.slide({index})")
            page.wait_for_timeout(180)
            title = page.evaluate(
                "() => { const s = document.querySelector('.reveal .slides section.present');"
                "  const h = s && s.querySelector('h1,h2');"
                "  return h ? h.textContent.trim() : ''; }"
            )
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:44] or "slide"
            destination = out_dir / f"{index:02d}-{slug}.png"
            page.screenshot(path=str(destination))
            print(f"  {destination.name}")
            written += 1
        browser.close()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deck", type=Path, help="directory holding slides.html")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--slide",
        type=int,
        action="append",
        help="capture only this 0-based slide; repeatable",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report slides whose content runs off the bottom, and write nothing",
    )
    args = parser.parse_args()

    deck = args.deck
    if args.check:
        # Non-zero exit when something overflows, so this can gate a build.
        return 1 if check(deck) else 0

    out_dir = args.out or Path("outputs/deck-shots") / deck.name
    written = capture(deck, out_dir, args.slide)
    print(f"wrote {written} screenshots to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
