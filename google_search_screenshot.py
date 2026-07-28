#!/usr/bin/env python3
"""
google_search_screenshot.py
---------------------------
Performs a web search and saves a screenshot of the ENTIRE first results page.

Features
  * Captures the full, scrollable first page of results (top to bottom) 
  * Screenshot file name = the date & time the shot was taken
        e.g.  2026-07-28_10-54-09.png
  * A banner is drawn on top of the page showing the exact search query
    (and the timestamp) so the "search info" is always visible in the image.
  * Screenshots saved into a dedicated folder (default: ./screenshots).
  * A running log (search_log.csv) records every query, timestamp and file.
  * Uses Microsoft Edge by default (no Chromium download needed)
    * Needs Linux support later. 
  * Persistent profile + stealth tweaks reduce Google CAPTCHAs. 
    * Run with --headed command first to save captcha settings. 

Typical usage
  python google_search_screenshot.py "Cat Facts" --headed --ignore-https-errors

  # Later runs usually skip the CAPTCHA thanks to the saved profile:
  python google_search_screenshot.py "another query" --ignore-https-errors

  # CAPTCHA-free alternative engines:
  python google_search_screenshot.py "Cat Facts" --engine bing
  python google_search_screenshot.py "Cat Facts" --engine duckduckgo

Setup
  pip install playwright
  # Using your installed Edge (default) needs NO extra download.
"""

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


SEARCH_URLS = {
    "google": "https://www.google.com/search?q=",
    "bing": "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
}

STEALTH_JS = """
// Hide the automation flag most sites check for.
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-CA', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""


# ----------------------------------------------------------------------------- #
#  Helpers
# ----------------------------------------------------------------------------- #
def timestamp() -> dt.datetime:
    return dt.datetime.now()


def make_filename(ts: dt.datetime) -> str:
    # Colons are illegal in Windows file names. Dumb windows stuff. 
    return ts.strftime("%Y-%m-%d_%H-%M-%S") + ".png"


def dismiss_consent(page) -> None:
    """Click through any cookie/consent interstitial if one appears."""
    selectors = [
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('I agree')",
        "button:has-text('Reject all')",
        "button#L2AGLb",
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


def looks_like_captcha(page) -> bool:
    """Best-effort detection of Google's 'unusual traffic' / reCAPTCHA page."""
    try:
        content = page.content().lower()
    except Exception:
        return False
    url = (page.url or "").lower()
    if "/sorry/" in url or "captcha" in url:
        return True
    markers = [
        "unusual traffic", "our systems have detected", "recaptcha",
        "are you a robot", "verify you're a human", "verify you are a human",
    ]
    return any(m in content for m in markers)


def handle_captcha(page, headed: bool) -> None:
    """In headed mode, pause so the user can solve a CAPTCHA by hand."""
    if not looks_like_captcha(page):
        return
    if headed:
        print("\n" + "=" * 64)
        print(" A CAPTCHA appeared. Please solve it in the browser window,")
        print(" then come back here and press Enter to continue capturing.")
        print("=" * 64)
        try:
            input(" Press Enter once solved... ")
        except (EOFError, KeyboardInterrupt):
            pass
        page.wait_for_timeout(1500)
    else:
        print("\n  ! A CAPTCHA was detected but the browser is headless so it")
        print("    can't be solved. Re-run with --headed, or use")
        print("    --engine bing / --engine duckduckgo to avoid it.\n")


def scroll_to_load_all(page) -> None:
    """
    Scroll from top to bottom in steps so lazy-loaded content (images, the AI
    Overview, later results) all render before a full-page capture, then return
    to the top so the image starts cleanly.
    """
    try:
        total = page.evaluate("document.body.scrollHeight")
        step = 700
        pos = 0
        while pos < total:
            page.evaluate("(y) => window.scrollTo(0, y)", pos)
            page.wait_for_timeout(300)
            pos += step
            total = page.evaluate("document.body.scrollHeight")  # may grow
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
    except Exception:
        pass


def add_banner(page, query: str, ts: dt.datetime) -> None:
    """Inject a banner showing the query + capture time at the top of the page."""
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
        pass


def log_result(output_dir: Path, ts: dt.datetime, query: str, path: Path) -> None:
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
def run(query: str, output_dir: Path, headed: bool, ignore_https_errors: bool,
        browser_channel: str, engine: str, profile_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    ts = timestamp()
    out_path = output_dir / make_filename(ts)
    search_url = SEARCH_URLS[engine] + quote_plus(query)

    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0")

    with sync_playwright() as p:
        ctx_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": not headed,
            "viewport": {"width": 1366, "height": 900},
            "locale": "en-CA",
            "user_agent": ua,
            "ignore_https_errors": ignore_https_errors,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if browser_channel and browser_channel.lower() != "chromium":
            ctx_kwargs["channel"] = browser_channel

        context = p.chromium.launch_persistent_context(**ctx_kwargs)
        context.add_init_script(STEALTH_JS)
        page = context.pages[0] if context.pages else context.new_page()

        print(f'Searching {engine} for: "{query}"  '
              f'(browser: {browser_channel or "chromium"})')
        try:
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        except PWTimeout:
            print("  ! Navigation timed out; capturing whatever loaded.")

        dismiss_consent(page)
        page.wait_for_timeout(1500)

        if engine == "google":
            handle_captcha(page, headed)

        # Let everything (incl. the async AI Overview) finish loading.
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout:
            pass
        scroll_to_load_all(page)

        add_banner(page, query, ts)

        # Full-page capture: the entire first results page in one image.
        page.screenshot(path=str(out_path), full_page=True)
        print("  - Captured the full first results page.")
        context.close()

    log_result(output_dir, ts, query, out_path)
    return out_path


# ----------------------------------------------------------------------------- #
#  CLI
# ----------------------------------------------------------------------------- #
def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Search the web and screenshot the entire first results page."
    )
    parser.add_argument("query", nargs="*",
                        help="The search query. If omitted, you'll be prompted.")
    parser.add_argument("--engine", "-e", default="google",
                        choices=["google", "bing", "duckduckgo"],
                        help="Search engine (default: google). bing/duckduckgo "
                             "rarely CAPTCHA automated browsers.")
    parser.add_argument("--output-dir", "-o", default="screenshots",
                        help="Folder to save screenshots (default: ./screenshots).")
    parser.add_argument("--profile-dir", default="edge_profile",
                        help="Folder that stores the persistent browser profile "
                             "(cookies/session). Default: ./edge_profile.")
    parser.add_argument("--browser-channel", "-b", default="msedge",
                        help="'msedge' (default), 'chrome', or 'chromium'.")
    parser.add_argument("--headed", action="store_true",
                        help="Show the browser window (needed to solve a CAPTCHA "
                             "by hand). Default: headless.")
    parser.add_argument("--ignore-https-errors", action="store_true",
                        help="Ignore TLS/cert errors (corporate proxies).")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    query = " ".join(args.query).strip()
    if not query:
        try:
            query = input("Enter your search query: ").strip()
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
            headed=args.headed,
            ignore_https_errors=args.ignore_https_errors,
            browser_channel=args.browser_channel,
            engine=args.engine,
            profile_dir=Path(args.profile_dir).expanduser().resolve(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Something went wrong: {exc}")
        if "channel" in str(exc).lower() or "executable" in str(exc).lower():
            print("Tip: ensure Edge is installed, or try --browser-channel chrome.")
        return 2

    print(f"Screenshot saved to: {path}")
    print(f"History log:        {path.parent / 'search_log.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
