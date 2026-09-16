#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


RUNTIME = Path(os.environ.get("SOCIAL_AUTO_UPLOAD_HOME", Path.home() / ".openclaw/workspace/social-auto-upload"))
sys.path.insert(0, str(RUNTIME))

from patchright.async_api import async_playwright  # noqa: E402
from uploader.douyin_uploader.location_picker import add_douyin_groupbuy_location  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add a Douyin domestic POI on the current image publish page.")
    parser.add_argument("--location", required=True, help="POI keyword, for example: 慈溪城西银泰百货")
    parser.add_argument("--cdp-url", default=os.environ.get("CHROME_CDP_URL"), help="Existing browser CDP URL.")
    parser.add_argument("--screenshot-dir", default=str(RUNTIME / "logs"))
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    if not args.cdp_url:
        raise SystemExit("missing --cdp-url; launch Chrome with --remote-debugging-port first")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0]
        pages = [page for page in context.pages if "creator.douyin.com" in page.url]
        if not pages:
            raise SystemExit("no Douyin Creator Center page found in this browser")
        page = pages[-1]
        await page.bring_to_front()
        await add_douyin_groupbuy_location(page, args.location, Path(args.screenshot_dir), print)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
