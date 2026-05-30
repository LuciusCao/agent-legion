import { chromium } from "playwright";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto("http://127.0.0.1:8000/");
  await page.waitForSelector(".stats-pills", { timeout: 10000 });

  // Click "已完成" filter to show packed chips
  await page.click("text=已完成");
  await page.waitForTimeout(500);

  // Enter package mode
  await page.click("[title='打包']");
  await page.waitForTimeout(500);

  // Click "全选"
  await page.click("text=全选");
  await page.waitForTimeout(500);

  // Get first video title before packaging
  const firstItem = await page.locator("md-list-item").first();
  const titleBefore = await firstItem.locator("[slot=headline]").textContent();
  console.log("First video before:", titleBefore.trim());

  // Click package button
  await page.click("[title='打包']");
  await page.waitForTimeout(2000); // Wait for API + download

  // Check if packed badge appears
  const badges = await page.locator(".packed-badge").count();
  console.log("Packed badges found:", badges);

  // Refresh page and check again
  await page.reload();
  await page.waitForSelector(".stats-pills", { timeout: 10000 });
  await page.click("text=已完成");
  await page.waitForTimeout(500);

  const badgesAfter = await page.locator(".packed-badge").count();
  console.log("Packed badges after refresh:", badgesAfter);

  await browser.close();
})();
