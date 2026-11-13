import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

PAGES = [
    ("airmen-home", "https://iafrecruitment.edcil.co.in/airmen/"),
    ("airmen-psl", "https://iafrecruitment.edcil.co.in/airmen/psl.html"),
]
OUT = Path("playwright-artifacts")
OUT.mkdir(exist_ok=True)

CHALLENGE_MARKERS = (
    "access denied",
    "request blocked",
    "verify you are human",
    "checking your browser",
    "security challenge",
    "captcha",
)

async def main():
    failures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="en-IN",
        )
        page = await context.new_page()
        page.set_default_navigation_timeout(45000)
        page.set_default_timeout(15000)

        for name, url in PAGES:
            print("\n" + "=" * 70)
            print(f"TEST: {name}")
            print(f"REQUESTED URL: {url}")
            status = None
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                status = response.status if response else None
                await page.wait_for_timeout(2500)
                final_url = page.url
                title = await page.title()
                visible_text = await page.locator("body").inner_text(timeout=15000)
                visible_text = re.sub(r"\\s+", " ", visible_text).strip()
                screenshot_path = OUT / f"{name}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)

                print(f"HTTP STATUS: {status}")
                print(f"FINAL URL: {final_url}")
                print(f"PAGE TITLE: {title}")
                print(f"VISIBLE TEXT LENGTH: {len(visible_text)}")
                print("VISIBLE TEXT SAMPLE:")
                print(visible_text[:1800] or "[NO VISIBLE TEXT]")
                print(f"SCREENSHOT: {screenshot_path}")

                lowered = visible_text.lower()
                challenge = any(marker in lowered for marker in CHALLENGE_MARKERS)
                genuine_content = len(visible_text) >= 100 and not challenge
                if status != 200:
                    failures.append(f"{name}: expected HTTP 200, got {status}")
                if not genuine_content:
                    failures.append(f"{name}: content is empty/too short or appears to be an access challenge")
            except Exception as exc:
                failures.append(f"{name}: browser navigation/read failed: {type(exc).__name__}: {exc}")
                try:
                    await page.screenshot(path=str(OUT / f"{name}-failure.png"), full_page=True)
                except Exception:
                    pass

        await browser.close()

    print("\n" + "=" * 70)
    if failures:
        print("RESULT: FAIL")
        for failure in failures:
            print(" - " + failure)
        raise SystemExit(1)
    print("RESULT: PASS — both official pages returned HTTP 200 and visible non-challenge content.")

if __name__ == "__main__":
    asyncio.run(main())
