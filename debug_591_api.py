"""
debug_591_api.py — 591 API 偵測工具
用途：攔截所有 *.591.com.tw 的 JSON 回應，儲存至 data/api_debug.json
用法：python debug_591_api.py
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright, Response

TARGET_URL = (
    "https://rent.591.com.tw/list?region=1&section=6,7,8"
    "&kind=0&order=posttime&orderType=desc"
)
OUTPUT_PATH = Path("data/api_debug.json")
HTML_OUTPUT_PATH = Path("data/page_debug.html")


async def main():
    captured = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="zh-TW",
            timezone_id="Asia/Taipei",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = await context.new_page()

        async def handle_response(response: Response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if "591.com.tw" not in url or "json" not in ct or response.status != 200:
                return
            try:
                body = await response.json()
                captured.append({"url": url, "body": body})
                listing_flag = " <-- LISTING CANDIDATE" if _looks_like_listing(body) else ""
                print(f"[CAPTURED]{listing_flag} {url}")
            except Exception as e:
                print(f"[SKIP] {url} ({e})")

        page.on("response", handle_response)

        print(f"Loading: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="networkidle", timeout=45000)
        await asyncio.sleep(3)
        # 捲動頁面，觸發 lazy-load 的 XHR
        await page.evaluate("window.scrollTo(0, 500)")
        await asyncio.sleep(10)  # 等 timestamp 驗證後的列表 API

        # 儲存頁面 HTML（偵測 SSR 或 CAPTCHA）
        html_content = await page.content()
        HTML_OUTPUT_PATH.parent.mkdir(exist_ok=True)
        HTML_OUTPUT_PATH.write_text(html_content, encoding="utf-8")
        print(f"[HTML] Page content saved to {HTML_OUTPUT_PATH} ({len(html_content)} chars)")

        await browser.close()

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=2)

    print(f"\nTotal captured: {len(captured)}")
    print(f"Saved to: {OUTPUT_PATH}")
    print("\nSummary:")
    for entry in captured:
        listing_flag = " <-- LISTING CANDIDATE" if _looks_like_listing(entry["body"]) else ""
        print(f"  {entry['url']}{listing_flag}")


def _looks_like_listing(obj, depth=0) -> bool:
    """遞迴偵測 JSON 是否包含含有 id + price 的物件陣列"""
    if depth > 6:
        return False
    if isinstance(obj, list) and obj:
        first = obj[0]
        if isinstance(first, dict):
            keys = set(first.keys())
            if keys & {"id", "post_id"} and "price" in keys:
                return True
        for item in obj:
            if _looks_like_listing(item, depth + 1):
                return True
    elif isinstance(obj, dict):
        for v in obj.values():
            if _looks_like_listing(v, depth + 1):
                return True
    return False


if __name__ == "__main__":
    asyncio.run(main())
