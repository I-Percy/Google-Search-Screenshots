# Search Screenshot Tool

A small Python program that performs a search on the engine of your choice (default Google) and
saves a screenshot of the **entire first results page**. The screenshot is named after the
**date and time** it was taken, and a banner is drawn across the top of the image showing the
**exact search query** that was run.

The purpose for this is to automate the ability to see search rankings in real time. This requires 
you to set it up yourself, but if you have the knowledge it won't be difficult. This also allows 
you to see search rankings quickly and without a profile attached. Just wipe the edge_profile folder 
and you are a new user again. 

## What it does

- Opens Google by default (can be switched to Bing or DuckDuckGo) and runs whatever query you give it.
- Captures the **full first results page** in a single tall image, everything from the top of the page down to the bottom of the results. 
- Draws a blue banner across the top of the page: `Search query: "..."  |  Captured: YYYY-MM-DD HH:MM:SS`.
- Saves the image to a folder (default `./screenshots`) with a name like `2026-07-28_10-54-09.png`.
- Appends every run to `search_log.csv` (timestamp, query, file name) so you keep a history.
- Uses installed **Microsoft Edge** by default, so no separate browser download is required.
  - Not supported on Linux. 

## Setup (one time)

Requires Python 3.8 or newer (tested on Python 3.14 on Windows).

```bash
pip install playwright
```

## How to run

Interactive (it will ask you for the query):

```bash
python search_screenshot.py
```

Pass the query directly:

```bash
python search_screenshot.py "best coffee in my city"
```
OR 
```bash
python search_screenshot.py "Cat Facts" --headed --ignore-https-errors
```

## Options

| Option | What it does |
|---|---|
| `-e`, `--engine` | Search engine to use: `google` (default), `bing`, or `duckduckgo`. Bing and DuckDuckGo rarely trigger a CAPTCHA. |
| `-o`, `--output-dir` | Folder to save screenshots in (default: `./screenshots`). |
| `--profile-dir` | Folder that stores the persistent browser profile (cookies/session). Default: `./edge_profile`. Keeping this between runs reduces CAPTCHAs. |
| `-b`, `--browser-channel` | Which browser to drive: `msedge` (default), `chrome`, or `chromium` (Playwright's bundled build, requires `python -m playwright install chromium`). |
| `--headed` | Show the browser window while it works. Needed if you have to solve a CAPTCHA by hand. Default is invisible/headless. |
| `--ignore-https-errors` | Skip TLS/certificate checks (needed behind some corporate proxies). |

Example combining options:

```bash
python search_screenshot.py "python tutorials" -o ./shots --engine bing --headed
```

## Handling Google CAPTCHAs

Google sometimes shows an "unusual traffic" CAPTCHA to automated browsers. The tool reduces this by reusing a saved browser profile and applying a few stealth tweaks, but if one still appears:

- Run with `--headed`, solve the CAPTCHA in the browser window, then press **Enter** in the terminal to continue. The saved profile usually means later runs won't ask again.
- Or simply switch engines with `--engine bing` or `--engine duckduckgo`, which almost never CAPTCHA and run cleanly headless.

## Notes

- The screenshot is a **full-page** capture, so the resulting image can be quite tall.
- Before capturing, the tool scrolls the page top-to-bottom so lazy-loaded content (images, the AI Overview, later results) fully renders, then returns to the top for a clean image.
- If the search engine shows a cookie/consent page, the script tries to click through it automatically.
- Colons are avoided in file names so the timestamps work on Windows, macOS, and Linux.

## Example: 
<img width="1366" height="3084" alt="image" src="https://github.com/user-attachments/assets/47eb9566-7ed3-4455-aaac-2689c6192093" />

