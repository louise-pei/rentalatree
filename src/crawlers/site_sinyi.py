"""
src/crawlers/site_sinyi.py — 信義房屋租屋爬蟲

架構：兩階段爬蟲
Phase 1（httpx）：爬取各行政區列表頁，收集所有房源 ID
Phase 2（httpx）：爬取詳細頁取得完整欄位

信義房屋採 SSR 靜態 HTML，不需要 Playwright。
使用 BeautifulSoup 解析。

爬蟲介面合約：
    crawler = CrawlerSinyi(config)
    listings = await crawler.fetch_listings()
    # 回傳 list[dict]，欄位符合資料模型
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TZ_TAIPEI = timezone(timedelta(hours=8))

BASE_URL = "https://www.sinyi.com.tw"

# 台北市各行政區對應信義房屋 URL 路徑名稱
DISTRICT_URL_NAMES: dict[str, str] = {
    "大安區": "Da-an-district",
    "信義區": "Sinyi-district",
    "松山區": "Songshan-district",
    "中山區": "Zhongshan-district",
    "中正區": "Zhongzheng-district",
    "士林區": "Shilin-district",
    "北投區": "Beitou-district",
    "內湖區": "Neihu-district",
    "南港區": "Nangang-district",
    "文山區": "Wenshan-district",
    "萬華區": "Wanhua-district",
}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


class CrawlerSinyi:
    """信義房屋租屋爬蟲"""

    def __init__(self, config: dict):
        self.config = config
        self.delay_min = config.get("delay_min", 1.0)
        self.delay_max = config.get("delay_max", 3.0)
        self.max_pages = config.get("max_pages", 10)

    async def fetch_listings(self) -> list[dict]:
        """
        主要爬蟲入口。
        回傳標準化的房源列表，欄位符合資料模型。
        """
        logger.info("開始爬取信義房屋")
        all_listings = []

        target_districts = self.config.get("filter_districts", [])
        if not target_districts:
            target_districts = list(DISTRICT_URL_NAMES.keys())

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            "Referer": BASE_URL,
        }

        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            # Phase 1：收集所有目標行政區的房源 ID
            listing_ids = await self._fetch_all_listing_ids(client, target_districts)
            logger.info(f"Phase 1 完成，取得 {len(listing_ids)} 筆房源 ID")

            # Phase 2：逐一爬取詳細頁
            for i, listing_id in enumerate(listing_ids):
                try:
                    detail = await self._fetch_detail(client, listing_id)
                    if detail:
                        all_listings.append(detail)
                except Exception as e:
                    logger.warning(f"詳細頁失敗：{listing_id}，錯誤：{e}")

                await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))

                if (i + 1) % 10 == 0:
                    logger.info(f"已處理 {i + 1}/{len(listing_ids)} 筆詳細頁")

        logger.info(f"信義房屋爬取完成：{len(all_listings)} 筆")
        return all_listings

    # ──────────────────────────────────────
    # Phase 1：列表頁
    # ──────────────────────────────────────

    async def _fetch_all_listing_ids(
        self, client: httpx.AsyncClient, districts: list[str]
    ) -> list[str]:
        """爬取各行政區列表頁，回傳去重後的房源 ID 列表"""
        all_ids: list[str] = []
        seen: set[str] = set()

        for district in districts:
            url_name = DISTRICT_URL_NAMES.get(district)
            if not url_name:
                logger.warning(f"信義房屋不支援的行政區（無 URL 對應）：{district}")
                continue

            ids = await self._fetch_district_ids(client, district, url_name)
            new_ids = [id_ for id_ in ids if id_ not in seen]
            seen.update(new_ids)
            all_ids.extend(new_ids)
            logger.info(f"{district} 取得 {len(new_ids)} 筆")

        return all_ids

    async def _fetch_district_ids(
        self, client: httpx.AsyncClient, district: str, url_name: str
    ) -> list[str]:
        """翻頁爬取單一行政區的所有房源 ID"""
        ids: list[str] = []
        seen: set[str] = set()
        current_url = f"{BASE_URL}/rent/list/Taipei-city/{url_name}/buy-1/"

        for page in range(1, self.max_pages + 1):
            try:
                resp = await client.get(current_url)
                if resp.status_code != 200:
                    logger.warning(f"列表頁 HTTP {resp.status_code}：{current_url}")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                page_ids = self._extract_listing_ids(soup)

                if not page_ids:
                    logger.debug(f"{district} 第 {page} 頁無房源，停止")
                    break

                new = [id_ for id_ in page_ids if id_ not in seen]
                seen.update(new)
                ids.extend(new)

                # 找下一頁 URL
                next_url = self._find_next_page_url(soup)
                if not next_url:
                    break
                current_url = next_url

                await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))

            except Exception as e:
                logger.error(f"{district} 列表頁失敗（第 {page} 頁）：{e}")
                break

        return ids

    def _extract_listing_ids(self, soup: BeautifulSoup) -> list[str]:
        """從列表頁 HTML 提取所有房源 ID（格式：C123456）"""
        ids = []
        for a in soup.find_all("a", href=re.compile(r"houseno/C\d+")):
            m = re.search(r"houseno/(C\d+)", a.get("href", ""))
            if m:
                ids.append(m.group(1))
        return list(dict.fromkeys(ids))  # 去重保順序

    def _find_next_page_url(self, soup: BeautifulSoup) -> Optional[str]:
        """從分頁區找下一頁連結（尋找「下一頁」或「»」文字）"""
        for a in soup.find_all("a"):
            text = a.get_text(strip=True)
            if text in ("下一頁", "»", "›", ">"):
                href = a.get("href", "")
                if href and href != "#":
                    return href if href.startswith("http") else f"{BASE_URL}{href}"
        return None

    # ──────────────────────────────────────
    # Phase 2：詳細頁
    # ──────────────────────────────────────

    async def _fetch_detail(
        self, client: httpx.AsyncClient, listing_id: str
    ) -> Optional[dict]:
        """爬取單一房源詳細頁並解析"""
        url = f"{BASE_URL}/rent/houseno/{listing_id}"
        resp = await client.get(url)

        if resp.status_code != 200:
            logger.warning(f"詳細頁 HTTP {resp.status_code}：{url}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        return self._parse_detail(listing_id, url, soup, resp.text)

    def _parse_detail(
        self, listing_id: str, url: str, soup: BeautifulSoup, html: str
    ) -> dict:
        """
        解析詳細頁，回傳符合資料模型的 dict。

        解析策略：
        1. 優先用 dt/dd 結構化欄位
        2. 備用 regex 從文字節點提取
        3. 無法取得的欄位填 None（由 geo.py 補上座標）
        """
        # ── 標題 ──
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        if not title:
            og_title = soup.find("meta", property="og:title")
            if og_title:
                title = og_title.get("content", "").strip()

        # ── 收集所有 dt/dd 欄位（主要資訊來源）──
        specs = self._extract_specs(soup)

        # ── 價格 ──
        # 信義房屋：數字與「元/月」分在相鄰兩個 tag，需用換行文字比對
        price = None
        for key in ("月租金", "租金", "價格"):
            if key in specs:
                price = self._parse_price(specs[key])
                break
        if price is None:
            price = self._extract_price_near_unit(soup)
        if price is None:
            # 最後備用：HTML 原始字串的 inline 格式
            m = re.search(r"([\d,]+)\s*元[/／]月", html)
            if m:
                price = self._parse_price(m.group(1))

        # ── 地址與行政區 ──
        address = specs.get("地址") or specs.get("物件地址") or specs.get("地點") or ""
        district = self._extract_district(address)

        if not district:
            # 備用：從麵包屑找行政區
            for a in soup.find_all("a"):
                text = a.get_text(strip=True)
                if re.fullmatch(r".{2,3}區", text):
                    district = text
                    break

        # ── 坪數 ──
        size = None
        for key in ("出租坪數", "坪數", "建物坪數", "使用坪數", "權狀坪數"):
            if key in specs:
                size = self._parse_size(specs[key])
                if size:
                    break
        if size is None:
            m = re.search(r"(\d+(?:\.\d+)?)\s*坪", html)
            if m:
                size = float(m.group(1))

        # ── 房型與格局 ──
        room_type = specs.get("型態") or specs.get("建物型態") or ""
        layout = specs.get("格局") or specs.get("房型") or ""
        combined_room_type = f"{room_type} {layout}".strip() if layout else room_type

        # ── 樓層 ──
        floor, total_floors = self._parse_floor(specs.get("樓層", ""))

        # ── 特色標籤 ──
        features = self._extract_features(soup)

        # ── 圖片 ──
        images = self._extract_images(soup, html, listing_id)

        # ── 刊登/更新時間 ──
        posted_at = None
        for text in soup.stripped_strings:
            if "更新日期" in text or "刊登日期" in text:
                m = re.search(r"(\d{4}[/-]\d{2}[/-]\d{2}(?:\s+\d{2}:\d{2})?)", text)
                if m:
                    posted_at = self._parse_posted_at(m.group(1))
                    break

        return {
            "id": f"sinyi_{listing_id}",
            "source": "sinyi",
            "title": title,
            "price": price,
            "district": district,
            "address": address,
            "lat": None,
            "lng": None,
            "size": size,
            "room_type": combined_room_type,
            "features": features,
            "floor": floor,
            "total_floors": total_floors,
            "nearest_mrt_station": None,
            "nearest_mrt_line": None,
            "nearest_mrt_distance_m": None,
            "url": url,
            "images": images,
            "posted_at": posted_at,
            "crawled_at": datetime.now(TZ_TAIPEI).isoformat(),
        }

    # ──────────────────────────────────────
    # 解析工具
    # ──────────────────────────────────────

    def _extract_specs(self, soup: BeautifulSoup) -> dict[str, str]:
        """收集頁面中所有 dt/dd、th/td、label/span 對，回傳 key→value dict"""
        specs: dict[str, str] = {}

        # dt / dd
        for dt in soup.find_all("dt"):
            key = dt.get_text(strip=True).rstrip("：: ")
            dd = dt.find_next_sibling("dd")
            if dd and key:
                specs[key] = dd.get_text(separator=" ", strip=True)

        # th / td（表格型規格）
        for tr in soup.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            for i in range(0, len(cells) - 1, 2):
                key = cells[i].get_text(strip=True).rstrip("：: ")
                val = cells[i + 1].get_text(separator=" ", strip=True)
                if key and val:
                    specs[key] = val

        # 找「月租金」等常見標籤旁的數值（class 型頁面備用）
        for label in soup.find_all(string=re.compile(r"月租金|更新日期|地址|坪數|格局|樓層|型態")):
            parent = label.parent
            if parent:
                sibling = parent.find_next_sibling()
                if sibling:
                    key = label.strip().rstrip("：: ")
                    specs[key] = sibling.get_text(strip=True)

        return specs

    def _extract_price_near_unit(self, soup: BeautifulSoup) -> Optional[int]:
        """
        信義房屋的租金數字與「元/月」分在相鄰 tag。
        找到含「元/月」的節點，往前找最近的純數字兄弟/父輩節點。
        """
        unit_els = soup.find_all(string=re.compile(r"元[/／]月"))
        for unit_el in unit_els:
            parent = unit_el.parent
            if parent is None:
                continue
            # 找前一個兄弟節點的文字
            prev = parent.find_previous_sibling()
            if prev:
                text = prev.get_text(strip=True)
                p = self._parse_price(text)
                if p and p > 1000:
                    return p
            # 找父節點的全文（換行分隔，取第一個符合數字的部分）
            full = parent.parent.get_text(separator="\n") if parent.parent else ""
            lines = [l.strip() for l in full.splitlines() if l.strip()]
            for i, line in enumerate(lines):
                if "元/月" in line or "元／月" in line:
                    for candidate in (lines[i - 1:i] if i > 0 else []) + [line]:
                        p = self._parse_price(candidate)
                        if p and p > 1000:
                            return p
        return None

    def _extract_district(self, address: str) -> str:
        """從地址字串提取行政區（如「台北市大安區...」→「大安區」）"""
        if not address:
            return ""
        m = re.search(r"台北市([^\s]{2,3}區)", address)
        if m:
            return m.group(1)
        m = re.search(r"([^\s]{2,3}區)", address)
        if m:
            return m.group(1)
        return ""

    def _parse_floor(self, floor_text: str) -> tuple[str, str]:
        """
        解析樓層字串。
        「12樓/共15樓」→ ("12", "15")
        「2樓」→ ("2", "")
        """
        if not floor_text:
            return "", ""
        m = re.search(r"(\d+)\s*樓[/／]共\s*(\d+)\s*樓", floor_text)
        if m:
            return m.group(1), m.group(2)
        m = re.search(r"(\d+)\s*樓", floor_text)
        if m:
            return m.group(1), ""
        return floor_text.strip(), ""

    def _extract_features(self, soup: BeautifulSoup) -> list[str]:
        """
        提取特色標籤（設備、條件）。
        嘗試多種 selector，合併去重。
        """
        features: list[str] = []

        # class 含 tag / label / facility / feature / equip / condition 的元素
        for el in soup.find_all(class_=re.compile(
            r"\b(tag|label|facility|feature|equip|condition|badge|icon-text)\b", re.I
        )):
            text = el.get_text(strip=True)
            if text and 1 < len(text) <= 15:
                features.append(text)

        # 已知常見租屋特色關鍵字（備用：從整頁文字比對）
        KNOWN_FEATURES = [
            "可開伙", "可養寵物", "可養寵", "可設籍", "可晾衣",
            "歡迎租補", "歡迎設籍", "歡迎學生",
            "近捷運", "近公園", "近學校", "近市場",
            "附停車位", "有管理員", "有電梯",
            "冷氣", "洗衣機", "冰箱", "熱水器", "網路", "第四台",
            "沙發", "床架", "衣櫃", "書桌",
        ]
        page_text = soup.get_text()
        for kw in KNOWN_FEATURES:
            if kw in page_text and kw not in features:
                features.append(kw)

        return list(dict.fromkeys(features))[:20]

    def _extract_images(
        self, soup: BeautifulSoup, html: str, listing_id: str
    ) -> list[str]:
        """提取房源圖片 URL"""
        images: list[str] = []

        # img 標籤
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
            if "sinyi.com.tw" in src and not any(x in src for x in ("icon", "logo", "banner", "avatar")):
                images.append(src)

        # HTML 原始字串中找信義圖片 URL（JS 渲染備用）
        for m in re.finditer(
            r'https://[^\s"\']+res\.sinyi\.com\.tw[^\s"\']+\.(?:jpg|jpeg|png|JPG|JPEG|PNG)',
            html,
        ):
            images.append(m.group(0))

        # 信義圖片固定格式：res.sinyi.com.tw/rent/CXXXXXX/bigimg/X.JPG
        for m in re.finditer(
            r'"(https://res\.sinyi\.com\.tw/rent/' + re.escape(listing_id) + r'/[^"]+)"',
            html,
        ):
            images.append(m.group(1))

        return list(dict.fromkeys(images))[:10]

    # ──────────────────────────────────────
    # 靜態工具函數
    # ──────────────────────────────────────

    @staticmethod
    def _parse_price(price_str: str) -> Optional[int]:
        """「25,000元/月」→ 25000，「面議」→ None"""
        if not price_str:
            return None
        digits = re.sub(r"[^\d]", "", price_str)
        if digits:
            try:
                return int(digits)
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_size(size_str: str) -> Optional[float]:
        """「51.93坪」→ 51.93"""
        if not size_str:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)", size_str)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_posted_at(time_str: str) -> Optional[str]:
        """「2026/04/07 16:39」→ ISO 8601 字串"""
        if not time_str:
            return None
        time_str = time_str.strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(time_str, fmt)
                dt = dt.replace(tzinfo=TZ_TAIPEI)
                return dt.isoformat()
            except ValueError:
                continue
        return time_str
