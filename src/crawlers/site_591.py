"""
src/crawlers/site_591.py — 591 租屋網爬蟲

架構：兩階段爬蟲
Phase 1（Playwright）：攔截 rsList XHR API 取得列表
Phase 2（httpx）：抓取詳細頁取得完整欄位

爬蟲介面合約：
    crawler = Crawler591(config)
    listings = await crawler.fetch_listings()
    # 回傳 list[dict]，欄位符合資料模型

反爬蟲措施：
- 停用 AutomationControlled 特徵
- 隨機 User-Agent
- 設定真實的 viewport、locale、timezone
- 每次請求間隨機延遲
"""

import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from playwright.async_api import async_playwright, BrowserContext, Page, Response

logger = logging.getLogger(__name__)

# 台灣時區（UTC+8）
TZ_TAIPEI = timezone(timedelta(hours=8))

# 591 行政區對應的 section ID（台北市）
DISTRICT_SECTION_IDS = {
    "大安區": "6",
    "信義區": "7",
    "松山區": "8",
    "中山區": "4",
    "中正區": "3",
    "士林區": "2",
    "北投區": "1",
    "內湖區": "11",
    "南港區": "12",
    "文山區": "13",
    "萬華區": "5",
    "中壢區": "9",
}

# 輪換 User-Agent 清單
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


class Crawler591:
    """591 租屋網爬蟲"""

    def __init__(self, config: dict):
        """
        config: 來自 config.yaml 的 scraper 區塊
        """
        self.config = config
        self.delay_min = config.get("delay_min", 1.0)
        self.delay_max = config.get("delay_max", 3.0)
        self.max_pages = config.get("max_pages", 10)
        self.run_mode = os.getenv("RUN_MODE", "ci")

    async def fetch_listings(self) -> list[dict]:
        """
        主要爬蟲入口。
        回傳標準化的房源列表，欄位符合資料模型。
        """
        logger.info("開始爬取 591 租屋網")
        all_listings = []

        async with async_playwright() as pw:
            # 根據執行模式選擇 headless 設定
            is_headless = self.run_mode != "local"

            browser = await pw.chromium.launch(
                headless=is_headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

            context = await self._create_context(browser)

            try:
                # 取得目標行政區列表
                target_districts = self.config.get("filter_districts", [])
                # 若 config 沒有指定，從環境讀（main.py 會傳入）
                if not target_districts:
                    target_districts = list(DISTRICT_SECTION_IDS.keys())

                # 取得這些行政區的 section IDs
                section_ids = [
                    DISTRICT_SECTION_IDS[d]
                    for d in target_districts
                    if d in DISTRICT_SECTION_IDS
                ]

                if not section_ids:
                    logger.warning("沒有有效的行政區 section ID，使用預設（全台北）")
                    section_ids = list(DISTRICT_SECTION_IDS.values())

                # Phase 1：取得列表
                raw_listings = await self._fetch_list(context, section_ids)
                logger.info(f"Phase 1 完成，取得 {len(raw_listings)} 筆列表資料")

                # Phase 2：取得詳細資料（httpx + Playwright cookies）
                cookies = await context.cookies()
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

                detailed = await self._fetch_details(raw_listings, cookie_str)
                logger.info(f"Phase 2 完成，成功取得 {len(detailed)} 筆詳細資料")

                all_listings.extend(detailed)

            except Exception as e:
                logger.error(f"591 爬蟲發生錯誤：{e}", exc_info=True)
            finally:
                await browser.close()

        return all_listings

    async def _create_context(self, browser) -> BrowserContext:
        """建立偽裝為真實用戶的瀏覽器 context"""
        ua = random.choice(USER_AGENTS)
        context = await browser.new_context(
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
            locale="zh-TW",
            timezone_id="Asia/Taipei",
            extra_http_headers={
                "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )
        # 注入腳本覆蓋 navigator.webdriver 特徵
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        return context

    async def _fetch_list(self, context: BrowserContext, section_ids: list[str]) -> list[dict]:
        """
        Phase 1：從 SSR 渲染的 DOM 直接提取房源列表（591 已改版，不再有列表 XHR API）。
        section 參數可傳入多個行政區 ID（逗號分隔）。翻頁使用直接 URL 導航（&page=N）。
        """
        page = await context.new_page()
        collected_listings = []

        try:
            sections_param = ",".join(section_ids)
            base_url = (
                f"https://rent.591.com.tw/list?region=1&section={sections_param}"
                f"&kind=0&order=posttime&orderType=desc"
            )

            for page_num in range(1, self.max_pages + 1):
                url = base_url if page_num == 1 else f"{base_url}&page={page_num}"

                if page_num == 1:
                    logger.info(f"載入 591 列表頁：{url}")
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))

                page_listings = await self._extract_from_dom(page)

                if not page_listings:
                    logger.info(f"第 {page_num} 頁無資料，停止翻頁")
                    break

                collected_listings.extend(page_listings)

                if page_num == 1:
                    logger.info(f"第 1 頁取得 {len(page_listings)} 筆")
                else:
                    logger.info(f"第 {page_num} 頁取得，累計 {len(collected_listings)} 筆")

                # 若此頁筆數不足 30（最後一頁），停止翻頁
                if len(page_listings) < 30:
                    logger.info(f"第 {page_num} 頁僅 {len(page_listings)} 筆，已是最後一頁")
                    break

        except Exception as e:
            logger.error(f"取得列表失敗：{e}", exc_info=True)
        finally:
            await page.close()

        # 去重（同一次執行內）
        seen = set()
        unique = []
        for item in collected_listings:
            if item["id"] not in seen:
                seen.add(item["id"])
                unique.append(item)

        return unique

    async def _extract_from_dom(self, page: Page) -> list[dict]:
        """
        從 Playwright page 的渲染 DOM 提取房源列表。
        591 改用 SSR（Nuxt.js），房源資料直接嵌在 HTML 中，不再透過 XHR API。
        """
        try:
            raw_items = await page.evaluate("""
            () => {
                const cards = document.querySelectorAll('.item[data-id]');
                return Array.from(cards).map(card => {
                    const id = card.dataset.id || '';

                    // 標題
                    const titleEl = card.querySelector('.item-info-title .link');
                    const title = titleEl ? titleEl.innerText.trim() : '';

                    // 價格（含「元/月」，只取數字部分）
                    const priceEl = card.querySelector('.item-info-price strong');
                    const priceRaw = priceEl ? priceEl.innerText.trim() : '';

                    // 特色標籤
                    const tagEls = card.querySelectorAll('.item-info-tag .tag');
                    const tags = Array.from(tagEls).map(t => t.innerText.trim()).filter(Boolean);

                    // 列表連結（href）
                    const linkEl = card.querySelector('.item-info-title a.link');
                    const href = linkEl ? (linkEl.href || '') : '';

                    // 各 item-info-txt div：
                    //   [0] 房型資訊（房型、格局、坪數、樓層）
                    //   [1] 地址資訊（行政區-街道）
                    //   [2] 捷運資訊
                    const infoTxts = card.querySelectorAll('.item-info-txt');

                    let roomType = '', layout = '', size = '', floor = '', address = '', district = '';

                    if (infoTxts.length >= 1) {
                        const txt0 = infoTxts[0];
                        // 房型：icon 旁的文字節點
                        for (const node of txt0.childNodes) {
                            if (node.nodeType === 3 && node.textContent.trim()) {
                                roomType = node.textContent.trim();
                                break;
                            }
                        }
                        const lines = txt0.querySelectorAll('.line');
                        if (lines[0]) layout = lines[0].innerText.trim();
                        if (lines[1]) size = lines[1].innerText.trim();
                        if (lines[2]) floor = lines[2].innerText.trim();
                    }

                    if (infoTxts.length >= 2) {
                        const addrDiv = infoTxts[1].querySelector('.inline-flex-row');
                        if (addrDiv) address = addrDiv.innerText.trim();
                        // 從地址提取行政區（格式：信義區-基隆路一段101巷）
                        const districtMatch = address.match(/^([^\-\s]+區)/);
                        if (districtMatch) district = districtMatch[1];
                    }

                    return { id, title, priceRaw, tags, href, roomType, layout, size, floor, address, district };
                });
            }
            """)
        except Exception as e:
            logger.warning(f"DOM 提取失敗：{e}")
            return []

        listings = []
        for item in raw_items:
            listing_id = str(item.get("id", "")).strip()
            if not listing_id:
                continue

            # 從 href 取得 URL（新格式：https://rent.591.com.tw/{id}）
            href = item.get("href", "")
            listing_url = href if href else f"https://rent.591.com.tw/{listing_id}"

            listing = {
                "id": f"591_{listing_id}",
                "source": "591",
                "title": item.get("title", "").strip(),
                "price": self._parse_price(item.get("priceRaw", "")),
                "district": item.get("district", "").strip(),
                "address": item.get("address", "").strip(),
                "url": listing_url,
                "room_type": item.get("roomType", "").strip(),
                "size": self._parse_size(item.get("size", "")),
                "floor": item.get("floor", "").strip(),
                "total_floors": "",
                "images": [],
                "features": item.get("tags", []),
                "lat": None,
                "lng": None,
                "nearest_mrt_station": None,
                "nearest_mrt_line": None,
                "nearest_mrt_distance_m": None,
                "posted_at": None,
                "crawled_at": datetime.now(TZ_TAIPEI).isoformat(),
            }
            listings.append(listing)

        return listings

    def _parse_list_api_response(self, data: dict) -> list[dict]:
        """解析 591 rsList API 的回應，提取基本列表資訊"""
        listings = []
        try:
            # 591 API 回應結構：data.data.data 為列表陣列
            items = (
                data.get("data", {}).get("data", [])
                or data.get("data", [])
                or []
            )

            for item in items:
                try:
                    listing_id = str(item.get("id") or item.get("post_id") or "")
                    if not listing_id:
                        continue

                    # 提取價格（可能含「元/月」文字，需清理）
                    price_raw = item.get("price", "") or ""
                    price = self._parse_price(str(price_raw))

                    # 提取行政區
                    region_name = item.get("regionname", "") or item.get("section_name", "") or ""

                    listing = {
                        "id": f"591_{listing_id}",
                        "source": "591",
                        "title": (item.get("title") or "").strip(),
                        "price": price,
                        "district": region_name.strip(),
                        "address": (item.get("address") or "").strip(),
                        "url": f"https://rent.591.com.tw/rent-detail-{listing_id}.html",
                        "room_type": (item.get("room_type") or item.get("kind_name") or "").strip(),
                        "size": self._parse_size(str(item.get("area", "") or "")),
                        "floor": str(item.get("floor", "") or ""),
                        "total_floors": str(item.get("allfloor", "") or ""),
                        "images": [],
                        "features": [],
                        "lat": None,
                        "lng": None,
                        "nearest_mrt_station": None,
                        "nearest_mrt_line": None,
                        "nearest_mrt_distance_m": None,
                        "posted_at": self._parse_posted_at(item.get("refreshtime") or item.get("posttime") or ""),
                        "crawled_at": datetime.now(TZ_TAIPEI).isoformat(),
                    }
                    listings.append(listing)

                except Exception as e:
                    logger.debug(f"解析單筆列表資料失敗：{e}")
                    continue

        except Exception as e:
            logger.warning(f"解析 rsList API 回應失敗：{e}")

        return listings

    async def _fetch_details(self, listings: list[dict], cookie_str: str) -> list[dict]:
        """
        Phase 2：用 httpx 批次取得詳細頁，補充座標、特色標籤、圖片等資料。
        使用 Playwright 取得的 cookies 維持 session。
        """
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Cookie": cookie_str,
            "Referer": "https://rent.591.com.tw/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9",
        }

        detailed = []

        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            for i, listing in enumerate(listings):
                try:
                    detail_url = listing["url"]
                    resp = await client.get(detail_url)

                    if resp.status_code == 200:
                        enriched = self._parse_detail_page(listing, resp.text)
                        detailed.append(enriched)
                    else:
                        logger.warning(f"詳細頁 HTTP {resp.status_code}：{detail_url}")
                        detailed.append(listing)

                    # 隨機延遲，避免被封鎖
                    await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))

                    if (i + 1) % 10 == 0:
                        logger.info(f"已處理 {i + 1}/{len(listings)} 筆詳細頁")

                except Exception as e:
                    logger.warning(f"取得詳細頁失敗：{listing.get('url', '')}，錯誤：{e}")
                    detailed.append(listing)

        return detailed

    def _parse_detail_page(self, listing: dict, html: str) -> dict:
        """
        解析詳細頁 HTML，補充以下資料：
        - lat / lng（從 window.__INITIAL_STATE__ 提取）
        - features（特色標籤）
        - images（圖片列表）
        - 補強 address、size、floor 等欄位
        """
        result = dict(listing)

        # ── 嘗試從 __INITIAL_STATE__ 提取座標 ──
        lat_match = re.search(r'"lat"\s*:\s*"?([0-9.]+)"?', html)
        lng_match = re.search(r'"lng"\s*:\s*"?([0-9.]+)"?', html)
        if lat_match and lng_match:
            try:
                result["lat"] = float(lat_match.group(1))
                result["lng"] = float(lng_match.group(1))
            except ValueError:
                pass

        # ── 提取特色標籤 ──
        # 591 詳細頁的特色標籤通常在 data-value 或特定 class
        feature_matches = re.findall(
            r'class="[^"]*(?:label|tag|facility)[^"]*"[^>]*>([^<]{1,20})</[^>]+>',
            html
        )
        if feature_matches:
            features = list({f.strip() for f in feature_matches if f.strip() and len(f.strip()) <= 10})
            result["features"] = features[:15]  # 最多保留 15 個標籤

        # ── 提取圖片 ──
        img_matches = re.findall(
            r'"(https://[^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"',
            html
        )
        # 過濾廣告圖片，只保留房源圖片
        images = [
            url for url in img_matches
            if "photo" in url.lower() or "img" in url.lower() or "rent" in url.lower()
        ]
        if images:
            result["images"] = list(dict.fromkeys(images))[:10]  # 去重，最多 10 張

        # ── 補強地址（若列表頁地址為空）──
        if not result.get("address"):
            addr_match = re.search(
                r'(?:地址|address)[^>]*>\s*<[^>]+>\s*([^<]{5,50})\s*<',
                html, re.IGNORECASE
            )
            if addr_match:
                result["address"] = addr_match.group(1).strip()

        # ── 補強坪數（取「使用坪數」優先）──
        if not result.get("size"):
            # 尋找「XX坪」格式
            size_match = re.search(r'(\d+(?:\.\d+)?)\s*坪', html)
            if size_match:
                result["size"] = float(size_match.group(1))

        return result

    async def _has_next_page(self, page: Page) -> bool:
        """檢查是否有下一頁按鈕且可點擊"""
        try:
            next_btn = page.locator(
                "a.page-next:not(.disabled), "
                "li.next:not(.disabled) > a, "
                "[class*='next']:not([class*='disabled'])"
            ).first
            return await next_btn.is_visible(timeout=2000)
        except Exception:
            return False

    async def _click_next_page(self, page: Page):
        """點擊下一頁按鈕"""
        try:
            next_btn = page.locator(
                "a.page-next:not(.disabled), "
                "li.next:not(.disabled) > a"
            ).first
            await next_btn.click()
        except Exception as e:
            logger.debug(f"點擊下一頁失敗：{e}")

    # ──────────────────────────────────────
    # 工具函數
    # ──────────────────────────────────────

    @staticmethod
    def _parse_price(price_str: str) -> Optional[int]:
        """
        解析價格字串為整數。
        例如：「25,000」→ 25000，「面議」→ None
        """
        if not price_str:
            return None
        # 移除非數字字元
        digits = re.sub(r"[^\d]", "", price_str)
        if digits:
            try:
                return int(digits)
            except ValueError:
                return None
        return None  # 「面議」等非數字價格

    @staticmethod
    def _parse_size(size_str: str) -> Optional[float]:
        """
        解析坪數字串為浮點數。
        例如：「25.3坪」→ 25.3，「25」→ 25.0
        """
        if not size_str:
            return None
        match = re.search(r"(\d+(?:\.\d+)?)", size_str)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_posted_at(time_str: str) -> Optional[str]:
        """
        解析 591 的刊登時間字串，轉換為 ISO 8601 格式。
        591 的時間格式通常為 Unix timestamp 或 「YYYY-MM-DD」。
        """
        if not time_str:
            return None
        time_str = str(time_str).strip()

        # Unix timestamp（數字）
        if time_str.isdigit():
            try:
                ts = int(time_str)
                dt = datetime.fromtimestamp(ts, tz=TZ_TAIPEI)
                return dt.isoformat()
            except Exception:
                return None

        # 日期字串
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(time_str, fmt)
                dt = dt.replace(tzinfo=TZ_TAIPEI)
                return dt.isoformat()
            except ValueError:
                continue

        return time_str  # 無法解析則原樣回傳
