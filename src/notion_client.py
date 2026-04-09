"""
src/notion_client.py — Notion 資料庫寫入模組

功能：
- 將房源資料寫入 Notion Database
- 使用「來源ID」屬性實現冪等寫入（避免重複建立頁面）
- 自動處理速率限制與重試
"""

import asyncio
import logging
from functools import wraps
from typing import Optional

from notion_client import AsyncClient
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)

# Notion API 速率限制：約 3 req/sec，安全間隔設為 0.4s
_NOTION_RATE_DELAY = 0.4

# 捷運線名稱對應顏色（Notion select 選項顏色）
MRT_LINE_COLORS = {
    "淡水信義線": "red",
    "板南線": "blue",
    "中和新蘆線": "yellow",
    "松山新店線": "green",
    "文湖線": "brown",
    "環狀線": "yellow",
    "新北投支線": "red",
    "小碧潭支線": "green",
}


def retry_async(max_attempts: int = 3, base_delay: float = 2.0):
    """
    非同步重試裝飾器，使用指數退避策略。
    適用於 Notion API 等可能暫時失敗的呼叫。
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except APIResponseError as e:
                    # 429 Too Many Requests：一定要重試
                    if e.status == 429 or (attempt < max_attempts - 1):
                        wait = base_delay * (2 ** attempt)
                        logger.warning(
                            f"Notion API 錯誤（嘗試 {attempt + 1}/{max_attempts}），"
                            f"{wait:.1f}s 後重試：{e}"
                        )
                        await asyncio.sleep(wait)
                    else:
                        raise
                except Exception as e:
                    if attempt < max_attempts - 1:
                        wait = base_delay * (2 ** attempt)
                        logger.warning(
                            f"意外錯誤（嘗試 {attempt + 1}/{max_attempts}），"
                            f"{wait:.1f}s 後重試：{e}"
                        )
                        await asyncio.sleep(wait)
                    else:
                        raise
        return wrapper
    return decorator


class NotionWriter:
    """Notion 資料庫寫入客戶端"""

    def __init__(self, token: str, database_id: str):
        """
        token: Notion Integration Token（NOTION_TOKEN 環境變數）
        database_id: Notion Database 的 32 碼 ID（NOTION_DATABASE_ID 環境變數）
        """
        if not token:
            raise ValueError("NOTION_TOKEN 未設定")
        if not database_id:
            raise ValueError("NOTION_DATABASE_ID 未設定")

        self.client = AsyncClient(auth=token)
        self.database_id = database_id

    @retry_async(max_attempts=3)
    async def create_page(self, listing: dict) -> Optional[str]:
        """
        在 Notion Database 建立一個新頁面（房源）。
        回傳建立後的 Notion page_id，失敗則回傳 None。

        使用「來源ID」欄位儲存內部 id，
        讓 main.py 可先查詢是否已存在再決定是否建立。
        """
        properties = self._build_properties(listing)

        try:
            response = await self.client.pages.create(
                parent={"database_id": self.database_id},
                properties=properties,
            )
            page_id = response["id"]
            logger.info(f"Notion 頁面建立成功：{listing.get('title', '')} → {page_id}")
            return page_id

        except APIResponseError as e:
            logger.error(f"Notion API 錯誤：{e.status} {e.message}")
            raise
        except Exception as e:
            logger.error(f"建立 Notion 頁面失敗：{e}", exc_info=True)
            raise

    @retry_async(max_attempts=2)
    async def find_page_by_source_id(self, source_id: str) -> Optional[str]:
        """
        透過「來源ID」屬性查詢 Notion Database，
        確認物件是否已存在，回傳 page_id 或 None。

        此為第二道防線（第一道是 SQLite）。
        """
        try:
            results = await self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "property": "來源ID",
                    "rich_text": {"equals": source_id}
                },
                page_size=1,
            )
            pages = results.get("results", [])
            if pages:
                return pages[0]["id"]
            return None

        except Exception as e:
            logger.warning(f"查詢 Notion 頁面失敗（來源ID={source_id}）：{e}")
            return None

    def _build_properties(self, listing: dict) -> dict:
        """
        將房源 dict 轉換為 Notion Database Properties 格式。
        欄位對應 Notion Database 的 schema。
        """

        def rich_text(value) -> list:
            """建立 rich_text 格式，自動截斷超過 2000 字的文字"""
            text = str(value) if value is not None else ""
            return [{"text": {"content": text[:2000]}}]

        def number(value) -> Optional[float]:
            """轉換數字欄位"""
            try:
                return float(value) if value is not None else None
            except (ValueError, TypeError):
                return None

        # 特色標籤（multi-select）
        features = listing.get("features") or []
        if isinstance(features, str):
            import json
            try:
                features = json.loads(features)
            except Exception:
                features = [features]
        feature_options = [
            {"name": str(f)[:100]}
            for f in features
            if f and str(f).strip()
        ]

        # 圖片（存為逗號分隔的文字）
        images = listing.get("images") or []
        if isinstance(images, list):
            images_str = ", ".join(images[:5])  # 最多存 5 張
        else:
            images_str = str(images)

        # 樓層資訊
        floor = str(listing.get("floor") or "")
        total_floors = str(listing.get("total_floors") or "")
        floor_display = f"{floor}/{total_floors}" if floor and total_floors else floor or total_floors or ""

        # 捷運線顏色
        mrt_line = listing.get("nearest_mrt_line") or ""

        properties = {
            "標題": {
                "title": rich_text(listing.get("title", "（無標題）"))
            },
            "來源": {
                "rich_text": rich_text(listing.get("source", ""))
            },
            "來源ID": {
                "rich_text": rich_text(listing.get("id", ""))
            },
            "月租金": {
                "number": number(listing.get("price"))
            },
            "行政區": {
                "select": {"name": str(listing.get("district", ""))} if listing.get("district") else None
            },
            "地址": {
                "rich_text": rich_text(listing.get("address", ""))
            },
            "坪數": {
                "number": number(listing.get("size"))
            },
            "房型": {
                "select": {"name": str(listing.get("room_type", ""))} if listing.get("room_type") else None
            },
            "特色": {
                "multi_select": feature_options
            },
            "樓層": {
                "rich_text": rich_text(floor_display)
            },
            "最近捷運站": {
                "rich_text": rich_text(listing.get("nearest_mrt_station", ""))
            },
            "捷運線": {
                "select": {"name": mrt_line} if mrt_line else None
            },
            "捷運距離(公尺)": {
                "number": number(listing.get("nearest_mrt_distance_m"))
            },
            "連結": {
                "url": listing.get("url") or None
            },
            "刊登時間": {
                "date": {"start": listing["posted_at"]} if listing.get("posted_at") else None
            },
            "爬取時間": {
                "date": {"start": listing["crawled_at"]} if listing.get("crawled_at") else None
            },
            "已推播": {
                "checkbox": bool(listing.get("is_notified", False))
            },
            "圖片": {
                "rich_text": rich_text(images_str)
            },
        }

        # 移除值為 None 的屬性（Notion API 不接受 null select）
        cleaned = {}
        for key, value in properties.items():
            if value is None:
                continue
            # 移除 select/date 中值為 None 的情況
            if isinstance(value, dict):
                inner_val = list(value.values())[0] if value else None
                if inner_val is None:
                    continue
            cleaned[key] = value

        return cleaned

    async def close(self):
        """關閉 Notion client"""
        await self.client.aclose()
