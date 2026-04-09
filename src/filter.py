"""
src/filter.py — 房源篩選邏輯

純函數，無 I/O，方便單元測試。
篩選條件全部從 config.yaml 的 filter 區塊讀取。
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 台灣時區（UTC+8）
TZ_TAIPEI = timezone(timedelta(hours=8))


def passes_filter(listing: dict, filter_cfg: dict) -> bool:
    """
    判斷一筆房源是否符合所有篩選條件。

    檢查順序（由快到慢，失敗即早退）：
    1. 行政區
    2. 月租金上限
    3. 最小坪數
    4. 排除關鍵字（標題 + 特色標籤）

    回傳 True 表示符合條件，可進行後續處理。
    """
    if not _check_district(listing, filter_cfg):
        logger.debug(f"[篩選] 排除（行政區不符）：{listing.get('title', '')} - {listing.get('district', '')}")
        return False

    if not _check_price(listing, filter_cfg):
        logger.debug(f"[篩選] 排除（租金超過上限）：{listing.get('title', '')} - {listing.get('price', 0)}")
        return False

    if not _check_size(listing, filter_cfg):
        logger.debug(f"[篩選] 排除（坪數不足）：{listing.get('title', '')} - {listing.get('size', 0)}")
        return False

    if not _check_exclude_keywords(listing, filter_cfg):
        logger.debug(f"[篩選] 排除（含排除關鍵字）：{listing.get('title', '')}")
        return False

    if not _check_require_keywords(listing, filter_cfg):
        logger.debug(f"[篩選] 排除（不含必要關鍵字）：{listing.get('title', '')}")
        return False

    return True


def is_recently_crawled(listing: dict, within_minutes: int) -> bool:
    """
    判斷物件是否在指定分鐘內爬取（用於決定是否推播到 LINE）。

    crawled_at 應為 ISO 8601 格式字串（含時區資訊）。
    """
    crawled_at_str = listing.get("crawled_at")
    if not crawled_at_str:
        return False

    try:
        crawled_at = _parse_datetime(crawled_at_str)
        now = datetime.now(TZ_TAIPEI)
        delta = now - crawled_at
        return delta.total_seconds() <= within_minutes * 60
    except Exception as e:
        logger.warning(f"解析 crawled_at 失敗：{crawled_at_str}，錯誤：{e}")
        return False


# ──────────────────────────────────────
# 內部檢查函數
# ──────────────────────────────────────

def _check_district(listing: dict, filter_cfg: dict) -> bool:
    """檢查行政區是否在目標清單中"""
    allowed = filter_cfg.get("districts", [])
    if not allowed:
        return True  # 未設定則不限制

    district = listing.get("district", "")
    if not district:
        return False  # 沒有行政區資訊，保守起見排除

    # 模糊比對：物件的 district 包含任一目標區名即通過
    return any(d in district for d in allowed)


def _check_price(listing: dict, filter_cfg: dict) -> bool:
    """檢查月租金是否在上限內"""
    max_price = filter_cfg.get("max_price")
    if max_price is None:
        return True  # 未設定上限

    price = listing.get("price")
    if price is None:
        # 「面議」或無價格：保守起見排除（避免推播不知道價格的物件）
        return False

    try:
        return float(price) <= float(max_price)
    except (ValueError, TypeError):
        return False


def _check_size(listing: dict, filter_cfg: dict) -> bool:
    """檢查坪數是否達到最小要求"""
    min_size = filter_cfg.get("min_size_ping")
    if min_size is None:
        return True  # 未設定下限

    size = listing.get("size")
    if size is None:
        return False  # 無坪數資訊，保守起見排除

    try:
        return float(size) >= float(min_size)
    except (ValueError, TypeError):
        return False


def _check_exclude_keywords(listing: dict, filter_cfg: dict) -> bool:
    """
    檢查標題與特色標籤中是否含有排除關鍵字。
    含有任一關鍵字即排除（回傳 False）。
    """
    keywords = filter_cfg.get("exclude_keywords", [])
    if not keywords:
        return True  # 未設定排除關鍵字

    # 合併標題與所有特色標籤為一個字串進行比對
    title = listing.get("title", "") or ""
    features = listing.get("features", []) or []
    features_str = " ".join(features) if isinstance(features, list) else str(features)
    combined_text = f"{title} {features_str}"

    for kw in keywords:
        if kw in combined_text:
            return False

    return True


def _check_require_keywords(listing: dict, filter_cfg: dict) -> bool:
    """
    標題或特色標籤必須包含至少一個必要關鍵字。
    未設定 require_keywords 則不限制。
    """
    keywords = filter_cfg.get("require_keywords", [])
    if not keywords:
        return True

    title = listing.get("title", "") or ""
    features = listing.get("features", []) or []
    features_str = " ".join(features) if isinstance(features, list) else str(features)
    combined_text = f"{title} {features_str}"

    return any(kw in combined_text for kw in keywords)


def _parse_datetime(dt_str: str) -> datetime:
    """
    解析 ISO 8601 日期時間字串，確保帶有時區資訊。
    若原始字串無時區，假設為台灣時間（UTC+8）。
    """
    # Python 3.11 以上可直接用 datetime.fromisoformat 解析帶 Z 的字串
    # 為了相容性手動處理
    dt_str = dt_str.strip()

    # 處理 Z 後綴（UTC）
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        # 嘗試常見的無時區格式
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(dt_str, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"無法解析日期時間：{dt_str}")

    # 若無時區資訊，假設為台灣時間
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_TAIPEI)

    # 統一轉換為台灣時間
    return dt.astimezone(TZ_TAIPEI)
