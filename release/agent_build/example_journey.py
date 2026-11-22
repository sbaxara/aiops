# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Example browser journey for an Axara browser monitor.

The browser monitor runs this script and reads its exit code (0 = pass) and the last JSON line
it prints. URLs and credentials come only from the agent host's environment, never from the script:

    JOURNEY_URL                 page to open (required)
    JOURNEY_EXPECT_TEXT         text that must be visible for the check to pass (required)

Copy this file, adapt the steps, and point the monitor's config.script_path at the copy.
"""
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright


def _required(name):
    """Value of a required environment variable; prints an error result and exits when unset."""
    value = os.environ.get(name)
    if not value:
        print(json.dumps({"status": "error", "step": "config", "message": f"environment variable {name} is not set"}))
        sys.exit(2)
    return value


def main():
    """Open JOURNEY_URL, check that JOURNEY_EXPECT_TEXT appears, and print the JSON result."""
    url, expect = _required("JOURNEY_URL"), _required("JOURNEY_EXPECT_TEXT")
    result = {"status": "fail", "step": "start"}
    start = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            result["step"] = "open"
            page.goto(url, timeout=30000)
            result["step"] = "check"
            page.get_by_text(expect).first.wait_for(timeout=15000)
            result["status"] = "pass"
        except Exception as e:
            result["error"] = str(e)[:300]
        finally:
            browser.close()
    result["duration_ms"] = round((time.time() - start) * 1000)
    print(json.dumps(result))
    sys.exit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
