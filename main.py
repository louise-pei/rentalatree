"""
main.py — 租屋偵探主程式

執行流程：
1. 初始化（設定、logging、資料庫）
2. 爬蟲（591 等來源）
3. 篩選（行政區、價格、坪數、排除關鍵字）
4. 地理編碼 + 捷運距離（新物件才執行）
5. 寫入 SQLite（去重）
6. 寫入 Notion（冪等）
7. LINE Bot 推播（只推新物件）
8. 產生地圖頁面

用法：
    python main.py                    # 一般執行
    RUN_MODE=local python main.py     # 本機模式（不用 headless）
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

# ── 載入環境變數 ──
load_dotenv()

# ── 設定 logging ──
def setup_logging():
    """設定同時輸出到 stdout 與 data/run.log 的 logging"""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]

    # 嘗試建立 log 檔案
    try:
        Path("data").mkdir(exist_ok=True)
        handlers.append(logging.FileHandler("data/run.log", encoding="utf-8"))
    except Exception:
        pass  # log 檔案建立失敗不影響主程式

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers,
    )
    # 降低 httpx 的 debug 雜訊
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


setup_logging()
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict:
    """載入 YAML 設定檔"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def main():
    logger.info("=" * 50)
    logger.info("租屋偵探 啟動")
    run_mode = os.getenv("RUN_MODE", "ci")
    logger.info(f"執行模式：{run_mode}")
    logger.info("=" * 50)

    # ── 1. 初始化 ──
    config = load_config()
    filter_cfg = config.get("filter", {})
    map_cfg = config.get("map", {})

    # 從環境變數取得 API 金鑰
    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_db_id = os.getenv("NOTION_DATABASE_ID", "")
    line_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_user_id = os.getenv("LINE_USER_ID", "")
    db_path = os.getenv("DB_PATH", "data/rental_detective.db")

    # 延遲 import（避免 import 時就連接外部服務）
    from src.db import Database
    from src.geo import load_mrt_stations, find_nearest_mrt, geocode
    from src.filter import passes_filter
    from src.crawlers.site_591 import Crawler591
    from src.crawlers.site_sinyi import CrawlerSinyi
    from src.map_generator import generate_map

    # 初始化資料庫
    db = Database(db_path)
    db.connect()
    db.init_schema()

    # 載入捷運站資料
    mrt_data_path = config.get("geo", {}).get("mrt_data_path", "src/data/mrt_stations.json")
    mrt_stations = load_mrt_stations(mrt_data_path)
    logger.info(f"載入 {len(mrt_stations)} 個捷運站點")

    try:
        # ── 2. 爬蟲階段 ──
        logger.info("── 階段 2：爬蟲 ──")

        # 將篩選用的行政區傳入爬蟲（縮小爬取範圍）
        scraper_cfg = dict(config.get("scraper", {}))
        scraper_cfg["filter_districts"] = filter_cfg.get("districts", [])

        all_listings = []

        targets = scraper_cfg.get("targets", [{"name": "591"}])

        # 591 爬蟲
        if any(str(t["name"]) == "591" and t.get("enabled", True) for t in targets):
            try:
                crawler = Crawler591(scraper_cfg)
                listings_591 = await crawler.fetch_listings()
                all_listings.extend(listings_591)
                logger.info(f"591 爬取完成：{len(listings_591)} 筆")
            except Exception as e:
                logger.error(f"591 爬蟲失敗：{e}", exc_info=True)

        # 信義房屋爬蟲
        if any(str(t["name"]) == "sinyi" and t.get("enabled", True) for t in targets):
            try:
                crawler = CrawlerSinyi(scraper_cfg)
                listings_sinyi = await crawler.fetch_listings()
                all_listings.extend(listings_sinyi)
                logger.info(f"信義房屋爬取完成：{len(listings_sinyi)} 筆")
            except Exception as e:
                logger.error(f"信義房屋爬蟲失敗：{e}", exc_info=True)

        logger.info(f"爬蟲階段完成，共 {len(all_listings)} 筆原始資料")

        # ── 3. 篩選階段 ──
        logger.info("── 階段 3：篩選 ──")
        filtered = [l for l in all_listings if passes_filter(l, filter_cfg)]
        logger.info(f"篩選後剩 {len(filtered)} 筆（原 {len(all_listings)} 筆）")

        # ── 4. 地理編碼 + 捷運距離 ──
        logger.info("── 階段 4：地理編碼 + 捷運距離 ──")
        new_listings = [l for l in filtered if not db.exists(l["id"])]
        logger.info(f"需要地理編碼的新物件：{len(new_listings)} 筆")

        async with httpx.AsyncClient(timeout=15.0) as http_client:
            for listing in new_listings:
                # 若爬蟲已取得座標則跳過地理編碼
                if listing.get("lat") and listing.get("lng"):
                    logger.debug(f"已有座標，跳過地理編碼：{listing.get('title', '')}")
                else:
                    # 使用地址查詢座標
                    address = listing.get("address", "")
                    if address:
                        coords = await geocode(address, db=db, client=http_client)
                        if coords:
                            listing["lat"], listing["lng"] = coords
                        else:
                            logger.warning(f"地理編碼失敗：{address}")

                # 計算最近捷運站
                if listing.get("lat") and listing.get("lng") and mrt_stations:
                    mrt_info = find_nearest_mrt(listing["lat"], listing["lng"], mrt_stations)
                    listing.update(mrt_info)

        # ── 5. 寫入 SQLite ──
        logger.info("── 階段 5：寫入 SQLite ──")
        inserted = 0
        for listing in filtered:
            if not db.exists(listing["id"]):
                db.upsert(listing)
                inserted += 1
        logger.info(f"SQLite 新增 {inserted} 筆，跳過 {len(filtered) - inserted} 筆（已存在）")

        # ── 6. 寫入 Notion ──
        logger.info("── 階段 6：寫入 Notion ──")
        if notion_token and notion_db_id:
            from src.notion_client import NotionWriter
            notion = NotionWriter(notion_token, notion_db_id)
            notion_success = 0

            try:
                for listing in filtered:
                    # 跳過已有 Notion 頁面的物件
                    if db.get_notion_page_id(listing["id"]):
                        continue
                    try:
                        page_id = await notion.create_page(listing)
                        if page_id:
                            db.set_notion_page_id(listing["id"], page_id)
                            notion_success += 1
                        # 遵守 Notion API 速率限制
                        await asyncio.sleep(0.4)
                    except Exception as e:
                        logger.error(f"Notion 頁面建立失敗：{listing.get('title', '')}，錯誤：{e}")

                logger.info(f"Notion 寫入完成：{notion_success} 筆")
            finally:
                await notion.close()
        else:
            logger.warning("NOTION_TOKEN 或 NOTION_DATABASE_ID 未設定，跳過 Notion 寫入")

        # ── 7. LINE Bot 推播 ──
        logger.info("── 階段 7：LINE Bot 推播 ──")
        if line_token and line_user_id:
            from src.notifier import LineNotifier
            notifier = LineNotifier(line_token, line_user_id)

            try:
                within_minutes = filter_cfg.get("notify_within_minutes", 120)
                to_notify = db.get_unnotified(within_minutes=within_minutes)
                logger.info(f"待推播物件：{len(to_notify)} 筆")

                if to_notify:
                    success, fail = await notifier.push_batch(to_notify)
                    # 標記已推播
                    for listing in to_notify:
                        db.mark_notified(listing["id"])
                    logger.info(f"LINE 推播完成：成功 {success}，失敗 {fail}")
                else:
                    logger.info("無待推播物件")
            finally:
                await notifier.close()
        else:
            logger.warning("LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID 未設定，跳過 LINE 推播")

        # ── 8. 產生地圖 ──
        logger.info("── 階段 8：產生地圖 ──")
        all_active = db.get_all_active()
        generate_map(all_active, mrt_stations, map_cfg)
        logger.info(f"地圖產生完成（{len(all_active)} 個房源標記）")

    except Exception as e:
        logger.error(f"主程式發生未預期錯誤：{e}", exc_info=True)
        sys.exit(1)
    finally:
        db.close()

    logger.info("=" * 50)
    logger.info("租屋偵探 執行完畢")
    logger.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
