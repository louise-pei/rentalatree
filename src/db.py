"""
src/db.py — SQLite 資料庫介面

負責：
1. listings 資料表：儲存所有爬取到的房源（主要去重依據）
2. geocode_cache 資料表：快取地址→座標，避免重複呼叫 Geocoding API
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 台灣時區（UTC+8）
TZ_TAIPEI = timezone(timedelta(hours=8))


class Database:
    def __init__(self, db_path: str = "data/rental_detective.db"):
        self.db_path = db_path
        # 確保父資料夾存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self):
        """建立資料庫連線，設定 row_factory 使結果可像 dict 操作"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        # 啟用 WAL 模式，改善並發效能
        self.conn.execute("PRAGMA journal_mode=WAL")
        logger.debug(f"資料庫連線建立：{self.db_path}")

    def close(self):
        """關閉資料庫連線"""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.debug("資料庫連線已關閉")

    def init_schema(self):
        """建立資料表結構（若尚未存在）"""
        if not self.conn:
            self.connect()
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS listings (
                id                      TEXT PRIMARY KEY,
                source                  TEXT NOT NULL,
                title                   TEXT,
                price                   INTEGER,
                district                TEXT,
                address                 TEXT,
                lat                     REAL,
                lng                     REAL,
                size                    REAL,
                room_type               TEXT,
                features                TEXT,
                floor                   TEXT,
                total_floors            TEXT,
                nearest_mrt_station     TEXT,
                nearest_mrt_line        TEXT,
                nearest_mrt_distance_m  INTEGER,
                url                     TEXT,
                images                  TEXT,
                posted_at               TEXT,
                crawled_at              TEXT,
                is_notified             INTEGER DEFAULT 0,
                notion_page_id          TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_crawled_at  ON listings(crawled_at);
            CREATE INDEX IF NOT EXISTS idx_district    ON listings(district);
            CREATE INDEX IF NOT EXISTS idx_notified    ON listings(is_notified);

            CREATE TABLE IF NOT EXISTS geocode_cache (
                address     TEXT PRIMARY KEY,
                lat         REAL,
                lng         REAL,
                cached_at   TEXT
            );
        """)
        self.conn.commit()
        logger.info("資料庫 schema 初始化完成")

    # ──────────────────────────────────────
    # listings 操作
    # ──────────────────────────────────────

    def exists(self, listing_id: str) -> bool:
        """檢查指定 ID 的物件是否已存在於資料庫（快速去重用）"""
        row = self.conn.execute(
            "SELECT 1 FROM listings WHERE id = ? LIMIT 1", (listing_id,)
        ).fetchone()
        return row is not None

    def upsert(self, listing: dict):
        """
        新增或更新一筆房源資料。
        features 與 images 以 JSON 字串儲存，
        crawled_at 若未提供則自動填入當前時間。
        """
        data = dict(listing)

        # list 欄位序列化為 JSON 字串
        if isinstance(data.get("features"), list):
            data["features"] = json.dumps(data["features"], ensure_ascii=False)
        if isinstance(data.get("images"), list):
            data["images"] = json.dumps(data["images"], ensure_ascii=False)

        # 自動填入爬取時間
        if not data.get("crawled_at"):
            data["crawled_at"] = datetime.now(TZ_TAIPEI).isoformat()

        self.conn.execute("""
            INSERT INTO listings (
                id, source, title, price, district, address,
                lat, lng, size, room_type, features,
                floor, total_floors,
                nearest_mrt_station, nearest_mrt_line, nearest_mrt_distance_m,
                url, images, posted_at, crawled_at, is_notified
            ) VALUES (
                :id, :source, :title, :price, :district, :address,
                :lat, :lng, :size, :room_type, :features,
                :floor, :total_floors,
                :nearest_mrt_station, :nearest_mrt_line, :nearest_mrt_distance_m,
                :url, :images, :posted_at, :crawled_at, 0
            )
            ON CONFLICT(id) DO UPDATE SET
                title      = excluded.title,
                price      = excluded.price,
                crawled_at = excluded.crawled_at
        """, data)
        self.conn.commit()

    def mark_notified(self, listing_id: str):
        """將指定物件標記為已推播"""
        self.conn.execute(
            "UPDATE listings SET is_notified = 1 WHERE id = ?", (listing_id,)
        )
        self.conn.commit()

    def get_unnotified(self, within_minutes: int = 120) -> list[dict]:
        """
        取得尚未推播且在指定分鐘內爬取的物件列表。
        用於決定要推播哪些物件到 LINE Bot。
        """
        cutoff = (
            datetime.now(TZ_TAIPEI) - timedelta(minutes=within_minutes)
        ).isoformat()

        rows = self.conn.execute("""
            SELECT * FROM listings
            WHERE is_notified = 0
              AND crawled_at >= ?
            ORDER BY crawled_at DESC
        """, (cutoff,)).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def get_all_active(self) -> list[dict]:
        """
        取得所有房源，用於產生地圖頁面。
        只取有座標的物件（沒有座標無法顯示在地圖上）。
        """
        rows = self.conn.execute("""
            SELECT * FROM listings
            WHERE lat IS NOT NULL AND lng IS NOT NULL
            ORDER BY crawled_at DESC
        """).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def set_notion_page_id(self, listing_id: str, page_id: str):
        """記錄 Notion 頁面 ID，用於避免重複建立 Notion 頁面"""
        self.conn.execute(
            "UPDATE listings SET notion_page_id = ? WHERE id = ?",
            (page_id, listing_id)
        )
        self.conn.commit()

    def get_notion_page_id(self, listing_id: str) -> Optional[str]:
        """取得指定物件的 Notion 頁面 ID（若已建立）"""
        row = self.conn.execute(
            "SELECT notion_page_id FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if row:
            return row["notion_page_id"]
        return None

    # ──────────────────────────────────────
    # geocode_cache 操作
    # ──────────────────────────────────────

    def get_geocode_cache(self, address: str) -> Optional[tuple[float, float]]:
        """從快取取得地址的座標，若無快取則回傳 None"""
        row = self.conn.execute(
            "SELECT lat, lng FROM geocode_cache WHERE address = ?", (address,)
        ).fetchone()
        if row:
            return (row["lat"], row["lng"])
        return None

    def set_geocode_cache(self, address: str, lat: float, lng: float):
        """將地址→座標結果存入快取"""
        now = datetime.now(TZ_TAIPEI).isoformat()
        self.conn.execute("""
            INSERT INTO geocode_cache (address, lat, lng, cached_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                lat = excluded.lat,
                lng = excluded.lng,
                cached_at = excluded.cached_at
        """, (address, lat, lng, now))
        self.conn.commit()

    # ──────────────────────────────────────
    # 內部工具
    # ──────────────────────────────────────

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """將 sqlite3.Row 轉換為普通 dict，並還原 JSON 欄位"""
        d = dict(row)
        # 還原 JSON 字串為 list
        for key in ("features", "images"):
            if isinstance(d.get(key), str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
            elif d.get(key) is None:
                d[key] = []
        return d

    # ──────────────────────────────────────
    # context manager 支援（with Database(...) as db）
    # ──────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
