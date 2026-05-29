import asyncio

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("http://127.0.0.1:8000/")
        await page.wait_for_selector(".stats-pills", timeout=10000)

        # Click "已完成" filter to show packed chips
        await page.click("text=已完成")
        await asyncio.sleep(0.5)

        # Enter package mode
        await page.click("[title='打包']")
        await asyncio.sleep(0.5)

        # Click "全选"
        await page.click("text=全选")
        await asyncio.sleep(0.5)

        # Get first video id before packaging
        first_item = await page.query_selector("md-list-item")
        title_before = await first_item.evaluate(
            "el => el.querySelector('[slot=headline]').textContent"
        )
        print(f"First video before: {title_before.strip()}")

        # Click package button
        await page.click("[title='打包']")
        await asyncio.sleep(2)  # Wait for API + download

        # Check if packed badge appears
        badges = await page.query_selector_all(".packed-badge")
        print(f"Packed badges found: {len(badges)}")

        # Refresh page and check again
        await page.reload()
        await page.wait_for_selector(".stats-pills", timeout=10000)
        await page.click("text=已完成")
        await asyncio.sleep(0.5)

        badges_after = await page.query_selector_all(".packed-badge")
        print(f"Packed badges after refresh: {len(badges_after)}")

        await browser.close()


asyncio.run(main())
