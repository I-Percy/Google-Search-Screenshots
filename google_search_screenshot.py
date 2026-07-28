#!/usr/bin/env python3
"""
google_search_screenshot.py
---------------------------
Performs a web search, then captures a screenshot of the results page.

Why this version exists
  Google aggressively CAPTCHA-blocks automated browsers. This version reduces
  that in three ways:
    1. Persistent profile  - reuses a real, saved Edge profile folder so
       cookies/session persist. Solve a CAPTCHA once (in headed mode) and it
       is usually remembered for future runs.
    2. Stealth tweaks      - hides the navigator.webdriver automation flag and
       sets a normal user agent so you look like a regular browser.
    3. CAPTCHA handling    - detects the "unusual traffic" page. In headed mode
       it pauses so you can solve it by hand, then continues automatically.
  You can also switch --engine to bing or duckduckgo, which almost never CAPTCHA.

Features
  * Screenshot file name = the date & time the shot was taken
        e.g.  2026-07-28_10-54-09.png
  * A banner is drawn on top of the page showing the exact search query
    (and the timestamp) so the "search info" is always visible in the image.
  * Screenshots saved into a dedicated folder (default: ./screenshots).
  * A running log (search_log.csv) records every query, timestamp and file.
  * Uses your installed Microsoft Edge by default (no Chromium download needed).

Typical usage (recommended for Google to beat the CAPTCHA)
  python google_search_screenshot.py "best coffee in Edmonton" --headed --ignore-https-errors

  # Run again later - the saved profile usually means no CAPTCHA:
  python google_search_screenshot.py "next query" --ignore-https-errors

  # CAPTCHA-free alternative engines:
  python google_search_screenshot.py "best coffee in Edmonton" --engine bing
  python google_search_screenshot.py "best coffee in Edmonton" --engine duckduckgo

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

# Google's hidden "Web" filter. Appending &udm=14 returns the classic
# links-only results page with NO AI Overview rendered server-side. This is
# far more reliable than trying to remove the AI Overview from the DOM after
# it lazily loads. Used automatically for Google when --skip-ai-overview is on.
GOOGLE_WEB_ONLY_PARAM = "&udm=14"

STEALTH_JS = """
// Hide the automation flag most sites check for.
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// A couple of other commonly-checked properties.
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
    # Colons are illegal in Windows file names, so use dashes.
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
    markers = [
        "unusual traffic",
        "our systems have detected",
        "recaptcha",
        "/sorry/",
        "are you a robot",
        "verify you're a human",
        "verify you are a human",
    ]
    url = (page.url or "").lower()
    if "/sorry/" in url or "captcha" in url:
        return True
    return any(m in content for m in markers)


def handle_captcha(page, headed: bool) -> None:
    """
    If a CAPTCHA is shown: in headed mode, pause so the user can solve it and
    press Enter to continue. In headless mode, warn (can't be solved).
    """
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


def skip_ai_overview(page, engine: str, scroll_px: int) -> None:
    """
    Skip the AI-generated summary (Google 'AI Overview', Bing Copilot answer,
    DuckDuckGo AI answers) two ways, for robustness:
      1. Remove the AI card from the DOM by walking UP from its "AI Overview"
         heading to the full card container (works even when class names change).
      2. Scroll the FIRST ORGANIC RESULT to the top of the viewport, so even if
         the card can't be removed, it falls out of the captured frame. This
         does not rely on the card's height, unlike a fixed pixel scroll.
    Best-effort: never fails the run.
    """
    js = """
    ([engine, fallbackPx]) => {
        const out = {removed: 0, scrolled: false, method: 'none'};

        // ---- Step 1: remove the AI Overview / AI answer card ----------------
        const he = (text) => (text || '').toLowerCase();
        const wanted = ['ai overview', 'ai-powered', 'generated by ai',
                        'ai mode', 'search labs'];
        // Find any small element whose OWN text is an AI-overview label.
        const candidates = document.querySelectorAll(
            'h1, h2, h3, span, div[role="heading"], [aria-label]');
        for (const el of candidates) {
            const label = he(el.getAttribute && el.getAttribute('aria-label'));
            const txt = he(el.textContent);
            const isLabel =
                wanted.some(w => label === w || label.startsWith('ai overview')) ||
                (txt.length < 40 && wanted.some(w => txt.trim() === w ||
                                                     txt.includes('ai overview')));
            if (!isLabel) continue;
            // Walk up a few levels to capture the WHOLE card, but stop before
            // we swallow the main results container.
            let node = el;
            for (let i = 0; i < 6 && node && node.parentElement; i++) {
                const p = node.parentElement;
                const pid = (p.id || '').toLowerCase();
                if (pid === 'rso' || pid === 'search' || pid === 'center_col' ||
                    p.tagName === 'BODY') break;
                node = p;
            }
            try { node.remove(); out.removed++; }
            catch (e) { node.style.setProperty('display','none','important');
                        out.removed++; }
        }

        // Google also shows a right-hand "N sites" source panel; drop it too.
        if (engine === 'google') {
            document.querySelectorAll('[data-attrid], div').forEach(d => {
                const t = he(d.textContent);
                if (t && /^\\s*\\d+\\s+sites\\b/.test(t) && d.offsetHeight < 700) {
                    try { d.remove(); out.removed++; } catch (e) {}
                }
            });
        }

        // ---- Step 2: scroll the first organic result to the top ------------
        const firstResultSelectors = {
            google: ['#rso', '#search #rso', '#rso .MjjYud', '#rso div.g',
                     'div#center_col #rso'],
            bing:   ['#b_results > li.b_algo', '#b_results'],
            duckduckgo: ['ol.react-results--main li[data-layout="organic"]',
                         'section[data-testid="mainline"]',
                         'article[data-testid="result"]']
        };
        const sels = firstResultSelectors[engine] || [];
        for (const sel of sels) {
            const target = document.querySelector(sel);
            if (target) {
                target.scrollIntoView({block: 'start', inline: 'nearest'});
                out.scrolled = true;
                out.method = sel;
                break;
            }
        }
        if (!out.scrolled) {
            window.scrollTo({top: fallbackPx, behavior: 'instant'});
            out.method = 'fixed-px';
        }
        return out;
    }
    """
    try:
        res = page.evaluate(js, [engine, scroll_px])
        if res:
            if res.get("removed"):
                print(f"  - Removed {res['removed']} AI Overview / source block(s).")
            print(f"  - Scrolled to organic results (via: {res.get('method')}).")
    except Exception as e:  # noqa: BLE001
        # Fall back to a plain scroll if the richer logic errored out.
        try:
            page.evaluate("(px) => window.scrollTo({top: px})", scroll_px)
        except Exception:
            pass
    page.wait_for_timeout(600)


def add_banner(page, query: str, ts: dt.datetime) -> None:
    """Inject a banner showing the query + capture time so it's in the image."""
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
def run(query: str, output_dir: Path, full_page: bool, headed: bool,
        ignore_https_errors: bool, browser_channel: str, engine: str,
        profile_dir: Path, skip_ai: bool, scroll_px: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    ts = timestamp()
    out_path = output_dir / make_filename(ts)
    search_url = SEARCH_URLS[engine] + quote_plus(query)
    # For Google, the most reliable way to avoid the AI Overview is the hidden
    # "Web" filter (udm=14), which never renders it server-side.
    if engine == "google" and skip_ai:
        search_url += GOOGLE_WEB_ONLY_PARAM

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

        # Persistent context keeps cookies/session between runs -> fewer CAPTCHAs.
        context = p.chromium.launch_persistent_context(**ctx_kwargs)
        context.add_init_script(STEALTH_JS)
        page = context.pages[0] if context.pages else context.new_page()

        mode = ""
        if engine == "google" and skip_ai:
            mode = "  [Web-only mode: AI Overview disabled via udm=14]"
        print(f'Searching {engine} for: "{query}"  '
              f'(browser: {browser_channel or "chromium"}){mode}')
        try:
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        except PWTimeout:
            print("  ! Navigation timed out; capturing whatever loaded.")

        dismiss_consent(page)
        page.wait_for_timeout(1500)

        if engine == "google":
            handle_captcha(page, headed)

        page.wait_for_timeout(1000)

        if skip_ai:
            skip_ai_overview(page, engine, scroll_px)

        add_banner(page, query, ts)

        # A full-page shot ignores the scroll offset (it captures everything),
        # so when skipping the AI block we force a viewport shot to honour it.
        use_full_page = full_page and not skip_ai
        if full_page and skip_ai:
            print("  - Note: --full-page ignored because --skip-ai-overview "
                  "needs a scrolled viewport capture.")
        page.screenshot(path=str(out_path), full_page=use_full_page)
        context.close()

    log_result(output_dir, ts, query, out_path)
    return out_path


# ----------------------------------------------------------------------------- #
#  CLI
# ----------------------------------------------------------------------------- #
def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Perform a web search and save a timestamped screenshot."
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
    parser.add_argument("--skip-ai-overview", action="store_true",
                        help="Hide the AI Overview / AI answer block and scroll "
                             "down slightly so organic results are captured.")
    parser.add_argument("--scroll-px", type=int, default=350,
                        help="How many pixels to scroll down when skipping the "
                             "AI Overview (default: 350).")
    parser.add_argument("--full-page", action="store_true",
                        help="Capture the entire scrollable page. (Ignored when "
                             "--skip-ai-overview is used.)")
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
            full_page=args.full_page,
            headed=args.headed,
            ignore_https_errors=args.ignore_https_errors,
            browser_channel=args.browser_channel,
            engine=args.engine,
            profile_dir=Path(args.profile_dir).expanduser().resolve(),
            skip_ai=args.skip_ai_overview,
            scroll_px=args.scroll_px,
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
