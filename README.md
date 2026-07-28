# Search Screenshot Tool

A small Python program that performs a search on the engine of your choice (default Google) and 
saves a screenshot of the results page. The screenshot is named after the **date and time** it 
was taken, and a banner is drawn across the top of the image showing the **exact search query** 
that was run.

## What it does

- Opens Google by default, can configure to run bing or duckduckgo instead, and runs whatever
  query you give it.
- Draws a blue banner on the page: `Search query: "..."  |  Captured: YYYY-MM-DD HH:MM:SS`.
- Saves the image to a folder (default `./screenshots`) with a name like `2026-07-28_10-54-09.png`.
- Appends every run to `search_log.csv` (timestamp, query, file name) so you keep a history.

## Setup (one time)

Requires Python 3.8+.

```bash
pip install playwright
playwright install chromium
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

## Options

| Option | What it does |
|---|---|
| `-o`, `--output-dir` | Folder to save screenshots in (default: `./screenshots`). |
| `--full-page` | Capture the whole scrollable page, not just the visible area. |
| `--headed` | Show the browser window while it works (default is invisible/headless). |
| `--ignore-https-errors` | Skip TLS/certificate checks (only needed behind some corporate proxies). |

Example combining options:

```bash
python search_screenshot.py "python tutorials" -o ./shots --full-page --headed
```

## Notes

- The first run downloads the Chromium browser via `playwright install chromium`; after that it runs offline-fast.
- If the search engine shows a cookie/consent page, the script tries to click through it automatically.
- Colons are avoided in file names so the timestamps work on Windows, macOS, and Linux.
