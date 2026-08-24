"""Capture static fallback images for the deck's three interactive components.

    -> assets/fallback/replay.png, hotspot.png, nlq.png

WHY. The pipeline replay, the dot map and the query mock all need JavaScript.
If the presenting browser blocks scripts, or a component throws before it
finishes wiring itself up, the slide is blank -- in front of the room, with no
recovery. Each component now carries one of these images and shows it unless its
own script reaches the end and marks itself live. So the failure mode is a
static picture of the thing rather than an empty slide.

Element screenshots, not slide screenshots: the image sits *inside* the slide, so
capturing the whole slide would repeat the heading and the footer inside itself.

The replay is captured mid-motion on purpose -- run it, let the clock reach the
end, then shoot -- because a picture of an unstarted bar communicates nothing.

Run after any change to a component's appearance, then rebuild the deck:

    uv run --no-project --with playwright python scripts/build_deck_fallbacks.py
    make deck
"""

from __future__ import annotations

import argparse
from pathlib import Path

DECK = Path("docs/presentations/2026-08-17-value-add")

# (slide index, element selector, output name, run the replay first)
TARGETS = [
    (6, "#replay", "replay.png", True),
    (17, "#hotspot", "hotspot.png", False),
    (19, "#nlq", "nlq.png", False),
]


def capture(deck: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright

    page_html = (deck / "slides.html").resolve()
    if not page_html.exists():
        raise SystemExit(f"{page_html} not found -- run `make deck` first")
    out_dir = deck / "assets/fallback"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=2)
        page.goto(page_html.as_uri())
        page.wait_for_timeout(1200)
        page.evaluate("Reveal.configure({fragments: false, transition: 'none'})")

        for index, selector, name, run_first in TARGETS:
            page.evaluate(f"Reveal.slide({index})")
            page.wait_for_timeout(500)
            element = page.query_selector(selector)
            if element is None:
                raise SystemExit(f"slide {index} has no {selector} -- did the deck change?")
            if run_first:
                # The speed control CYCLES 1x -> 4x -> slow, so exactly one
                # click gets 4x. Two clicks land on slow and the capture catches
                # the bar a third of the way through, which is worse than not
                # running it at all.
                page.click("#replay-speed")
                page.click("#replay-go")
                # 13.66s of measured time at 4x is ~3.4s. Wait past it.
                page.wait_for_timeout(5000)
            target = out_dir / name
            element.screenshot(path=str(target))
            written.append(target)
        browser.close()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, default=DECK)
    args = parser.parse_args()
    for path in capture(args.deck):
        print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
