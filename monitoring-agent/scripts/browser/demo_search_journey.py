# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Demo browser journey: open a site, submit a search and check the result page.

Out of the box it searches Wikipedia for "Python (programming language)" and checks that the
article opens with that title. Use it to try the browser monitor, then copy it as the starting
point for your own journeys (log in, search, check out, ...).

How the browser monitor uses it: it runs this script, passes when the exit code is 0, and stores
the last JSON line printed (steps, timings, and a screenshot when the journey fails, which is
attached to the incident).

Settings (environment variables on the agent host, all optional):
    DEMO_SEARCH_URL      site to open (default https://www.wikipedia.org)
    DEMO_SEARCH_TERM     text to search for (default "Python (programming language)")
    DEMO_EXPECT_HEADING  page heading that must appear (default: the search term)
    DEMO_HEADLESS        0 to watch the browser while testing by hand (default 1)

Run it by hand from the agent folder:
    python scripts/browser/demo_search_journey.py
Monitor config (Monitors > Add Monitor > Browser):
    script_path: scripts/browser/demo_search_journey.py
    thresholds: duration_ms warning 8000, critical 15000

Real journeys must read URLs and credentials from environment variables like these, never from
the script itself.
"""
import base64
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("DEMO_SEARCH_URL", "https://www.wikipedia.org")
TERM = os.environ.get("DEMO_SEARCH_TERM", "Python (programming language)")
EXPECT = os.environ.get("DEMO_EXPECT_HEADING", TERM)
HEADLESS = os.environ.get("DEMO_HEADLESS", "1") != "0"
# Identify the monitor to the site, as Wikimedia asks of automated clients.
USER_AGENT = ("Mozilla/5.0 (compatible; AxaraAIOps-BrowserMonitorDemo/1.0; "
              "+https://github.com/sbaxara/aiops)")
STEP_TIMEOUT_MS = 20000


def main():
    """Run the journey and print one JSON result line; exit 0 on pass, 1 on fail."""
    result = {"status": "fail", "url": URL, "search": TERM, "steps": []}
    start = time.time()

    def step(name, fn):
        """Run one named step and record its duration."""
        result["step"] = name
        t = time.time()
        fn()
        result["steps"].append({"step": name, "ms": round((time.time() - t) * 1000)})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
        page.set_default_timeout(STEP_TIMEOUT_MS)
        try:
            step("open site", lambda: page.goto(URL, wait_until="domcontentloaded"))
            box = page.locator("input[type=search], input[name=search], input[name=q]").first
            step("type search", lambda: (box.wait_for(state="visible"), box.fill(TERM)))
            step("submit search", lambda: (box.press("Enter"), page.wait_for_load_state("domcontentloaded")))
            heading = page.locator("h1").first
            step("check result", lambda: heading.wait_for(state="visible"))
            title = heading.inner_text().strip()
            result["heading"] = title
            if EXPECT.lower() not in title.lower():
                raise AssertionError(f"expected heading containing '{EXPECT}', got '{title}'")
            result["status"] = "pass"
            result["step"] = "done"
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"[:300]
            try:
                result["screenshot_b64"] = base64.b64encode(page.screenshot(type="png")).decode()
            except Exception:
                pass
        finally:
            browser.close()

    result["duration_ms"] = round((time.time() - start) * 1000)
    print(json.dumps(result))
    sys.exit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
