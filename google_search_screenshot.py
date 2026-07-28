#!/usr/bin/env python3
"""
google_search_screenshot.py
---------------------------
Performs a Google search, then captures a screenshot of the results page.

Features
  * Screenshot file name = the date & time the shot was taken
        e.g.  2026-07-28_10-54-09.png
  * A banner is drawn on top of the page showing the exact search query
    (and the timestamp) so the "search info" is always visible in the image.
  * All screenshots are saved into a dedicated folder (default: ./screenshots).
  * A running log (search_log.csv) records every query, timestamp and file path.

Usage
  # Interactive (you'll be prompted for the query):
  python google_search_screenshot.py

  # Pass the query directly:
  python google_search_screenshot.py "best coffee in Edmonton"

  # Options:
  python google_search_screenshot.py "python tutorials" \
        --output-dir ./shots --full-page --headed

First-time setup
  pip install playwright
  playwright install chromium
"""

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


# ----------------------------------------------------------------------------- #
#  Helpers
# ----------------------------------------------------------------------------- #
def timestamp() -> dt.datetime:
    """Return current local time."""
    return dt.datetime.now()


def make_filename(ts: dt.datetime) -> str:
    """Build a filesystem-safe file name from the date & time."""
    # Colons are illegal in Windows file names, so use dashes.
    return ts.strftime("%Y-%m-%d_%H-%M-%S") + ".png"


def dismiss_consent(page) -> None:
    """
    Google often shows a cookie/consent interstitial. Try to click the
    'Accept all' / 'Reject all' button so the real results are captured.
    Silently ignore if no such button exists.
    """
    selectors = [
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('I agree')",
        "button:has-text('Reject all')",
        "button#L2AGLb",              # common Google consent button id
        "div[role='none'] button",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click(timeout=2000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            continue


def add_banner(page, query: str, ts: dt.datetime) -> None:
    """
    Inject a fixed banner at the top of the page that shows the search query
    and the capture time, guaranteeing the 'search info' is visible in the shot.
    """
    when = ts.strftime("%Y-%m-%d %H:%M:%S")
    js = """
    ([query, when]) => {
        const bar = document.createElement('div');
        bar.style.cssText = [
            'position:relative','z-index:2147483647','width:100%',
            'box-sizing:border-box','padding:10px 16px',
            'background:#0b57d0','color:#ffffff',
            'font-family:Arial,Helvetica,sans-serif','font-size:15px',
            'font-weight:600','letter-spacing:.2px',
            'box-shadow:0 2px 6px rgba(0,0,0,.25)'
        ].join(';');
        bar.textContent = 'Search query: "' + query + '"   |   Captured: ' + when;
        document.body.insertBefore(bar, document.body.firstChild);
    }
    """
    try:
        page.evaluate(js, [query, when])
        page.wait_for_timeout(300)
    except Exception:
        # Banner is a nice-to-have; never fail the run because of it.
        pass


def log_result(output_dir: Path, ts: dt.datetime, query: str, path: Path) -> None:
    """Append a row to a CSV log so you have a searchable history."""
    log_file = output_dir / "search_log.csv"
    new_file = not log_file.exists()
    with log_file.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["timestamp", "query", "screenshot_file"])
        writer.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"), query, str(path.name)])


# ----------------------------------------------------------------------------- #
#  Core routine
# ----------------------------------------------------------------------------- #
def run(query: str, output_dir: Path, full_page: bool, headed: bool,
        ignore_https_errors: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = timestamp()
    filename = make_filename(ts)
    out_path = output_dir / filename

    search_url = "https://www.google.com/search?q=" + quote_plus(query)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context(
            viewport={"width": 1366, "height": 900},
            locale="en-CA",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=ignore_https_errors,
        )
        page = context.new_page()

        print(f'Searching Google for: "{query}"')
        try:
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        except PWTimeout:
            print("  ! Navigation timed out; capturing whatever loaded.")

        dismiss_consent(page)

        # Give results a moment to settle, then annotate and capture.
        page.wait_for_timeout(2000)
        add_banner(page, query, ts)

        page.screenshot(path=str(out_path), full_page=full_page)
        browser.close()

    log_result(output_dir, ts, query, out_path)
    return out_path


# ----------------------------------------------------------------------------- #
#  CLI
# ----------------------------------------------------------------------------- #
def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Perform a Google search and save a timestamped screenshot."
    )
    parser.add_argument(
        "query", nargs="*",
        help="The search query. If omitted, you'll be prompted for it.",
    )
    parser.add_argument(
        "--output-dir", "-o", default="screenshots",
        help="Folder to save screenshots in (default: ./screenshots).",
    )
    parser.add_argument(
        "--full-page", action="store_true",
        help="Capture the entire scrollable page instead of just the viewport.",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Show the browser window while it works (default: headless).",
    )
    parser.add_argument(
        "--ignore-https-errors", action="store_true",
        help="Ignore TLS/certificate errors (needed behind some corporate proxies).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    query = " ".join(args.query).strip()
    if not query:
        try:
            query = input("Enter your Google search query: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nNo query provided. Exiting.")
            return 1
    if not query:
        print("A search query is required.")
        return 1

    try:
        path = run(
            query=query,
            output_dir=Path(args.output_dir).expanduser().resolve(),
            full_page=args.full_page,
            headed=args.headed,
            ignore_https_errors=args.ignore_https_errors,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Something went wrong: {exc}")
        return 2

    print(f"Screenshot saved to: {path}")
    print(f"History log:        {path.parent / 'search_log.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
