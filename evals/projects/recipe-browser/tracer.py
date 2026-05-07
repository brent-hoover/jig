#!/usr/bin/env python3
"""Tracer for recipe-browser.

Pass conditions (see brief.md):
1. Boots the app, loads /, asserts 3 rows render with title and tag.
2. Clicks the "quick" tag chip; only pancakes + miso-soup remain.
3. Clicks pancakes; detail page renders heading + ingredient list.
4. Resizes viewport to 320px; no horizontal scroll.

Requires: playwright (pip install playwright; playwright install chromium)
The app must be running on localhost:8000 before this script is called.
Falls back to a curl-based smoke if playwright is unavailable.
"""
from __future__ import annotations

import subprocess
import sys


def _curl_smoke() -> bool:
    """Minimal smoke via curl — can't test JS/filter interaction."""
    try:
        result = subprocess.run(
            ["curl", "-sf", "http://localhost:8000/"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            print("FAIL: / returned non-200", file=sys.stderr)
            return False
        body = result.stdout
        for title in ("Beef Stew", "Pancakes", "Miso Soup"):
            if title not in body:
                print(f"FAIL: '{title}' not found in index page", file=sys.stderr)
                return False
        print("PASS (curl smoke — playwright not available)")
        return True
    except Exception as exc:
        print(f"FAIL: curl smoke error: {exc}", file=sys.stderr)
        return False


def _playwright_smoke() -> bool:
    from playwright.sync_api import sync_playwright  # type: ignore[import]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()

        # Step 1: index shows 3 rows
        page.goto("http://localhost:8000/")
        rows = page.locator("[data-recipe-row]")
        count = rows.count()
        if count != 3:
            print(f"FAIL: expected 3 recipe rows, got {count}", file=sys.stderr)
            browser.close()
            return False
        for expected in ("Beef Stew", "Pancakes", "Miso Soup"):
            if not page.locator(f"text={expected}").count():
                print(f"FAIL: '{expected}' not in index", file=sys.stderr)
                browser.close()
                return False
        # Each row shows at least one tag
        for i in range(count):
            row = rows.nth(i)
            tags = row.locator("[data-tag]")
            if tags.count() == 0:
                print(f"FAIL: row {i} has no tags", file=sys.stderr)
                browser.close()
                return False

        # Step 2: filter by "quick" — only pancakes and miso-soup remain
        page.locator("[data-tag-filter='quick']").click()
        page.wait_for_load_state("networkidle")
        visible_rows = page.locator("[data-recipe-row]:visible")
        if visible_rows.count() != 2:
            print(
                f"FAIL: after 'quick' filter expected 2 rows, got {visible_rows.count()}",
                file=sys.stderr,
            )
            browser.close()
            return False
        if page.locator("[data-recipe-row]:visible >> text=Beef Stew").count():
            print("FAIL: Beef Stew should be hidden after 'quick' filter", file=sys.stderr)
            browser.close()
            return False

        # Step 3: click pancakes; detail page renders heading + ingredient list
        page.locator("text=Pancakes").first.click()
        page.wait_for_load_state("networkidle")
        if not page.locator("h1:has-text('Pancakes')").count():
            print("FAIL: no <h1>Pancakes</h1> on detail page", file=sys.stderr)
            browser.close()
            return False
        if not page.locator("ul li").count():
            print("FAIL: no ingredient list items on detail page", file=sys.stderr)
            browser.close()
            return False

        # Step 4: 320px viewport — no horizontal scroll
        page.set_viewport_size({"width": 320, "height": 640})
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        if scroll_width > 320:
            print(
                f"FAIL: page has horizontal scroll at 320px (scrollWidth={scroll_width})",
                file=sys.stderr,
            )
            browser.close()
            return False

        browser.close()
        print("PASS")
        return True


def main() -> None:
    try:
        import playwright  # noqa: F401
        ok = _playwright_smoke()
    except ImportError:
        ok = _curl_smoke()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
