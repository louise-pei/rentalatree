"""
src/geo.py — 地理編碼 + 捷運站距離計算

功能：
1. geocode(address, db) — 地址轉換為 (lat, lng)
   優先順序：SQLite cache → Google Geocoding API → Nominatim
2. find_nearest_mrt(lat, lng, stations) — 找出最近的捷運站
3. load_mrt_stations(path) — 從 JSON 檔載入捷運站資料
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from geopy.distance import distance as geo_distance

logger = logging.getLogger(__name__)

# Nominatim 速率限制：每秒最多 1 次請求
_NOMINATIM_DELAY = 1.1


def load_mrt_stations(path: str = "src/data/mrt_stations.json") -> list[dict]:
    """
    從 JSON 檔載入台北捷運站點資料。
    回傳格式：[{"name": "台北車站", "lat": 25.04, "lng": 121.51, "lines": [...]}]
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            stations = json.load(f)
        # 過濾掉重複站名（同站有多條線時，JSON 中可能有略微不同的條目）
        seen = set()
        unique = []
        for s in stations:
            key = (round(s["lat"], 4), round(s["lng"], 4))
            if key not in seen:
                seen.add(key)
                unique.append(s)
        logger.debug(f"載入 {len(unique)} 個捷運站點")
        return unique
    except Exception as e:
        logger.error(f"載入捷運站資料失敗：{e}")
        return []


def find_nearest_mrt(lat: float, lng: float, stations: list[dict]) -> dict:
    """
    計算給定座標與所有捷運站的直線距離（Haversine），
    回傳最近一站的站名、路線名稱與距離（公尺）。

    回傳格式：
    {
        "nearest_mrt_station": "大安森林公園",
        "nearest_mrt_line": "淡水信義線",
        "nearest_mrt_distance_m": 320
    }
    """
    if not stations:
        return {
            "nearest_mrt_station": None,
            "nearest_mrt_line": None,
            "nearest_mrt_distance_m": None,
        }

    best_station = None
    best_distance = float("inf")

    for station in stations:
        try:
            dist_m = geo_distance(
                (lat, lng), (station["lat"], station["lng"])
            ).meters
            if dist_m < best_distance:
                best_distance = dist_m
                best_station = station
        except Exception:
            continue

    if not best_station:
        return {
            "nearest_mrt_station": None,
            "nearest_mrt_line": None,
            "nearest_mrt_distance_m": None,
        }

    # 取第一條路線名稱作為代表
    line_name = (
        best_station["lines"][0]["name"]
        if best_station.get("lines")
        else "未知"
    )

    return {
        "nearest_mrt_station": best_station["name"],
        "nearest_mrt_line": line_name,
        "nearest_mrt_distance_m": int(best_distance),
    }


async def geocode(
    address: str,
    db=None,  # Database instance，用於快取
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[tuple[float, float]]:
    """
    地址轉換為 (lat, lng)。

    查詢順序：
    1. SQLite geocode_cache（若有 db 且已快取）
    2. Google Geocoding API（若有 GOOGLE_GEOCODING_API_KEY）
    3. Nominatim（OpenStreetMap，免費，1 req/sec 限制）

    回傳 (lat, lng) 或 None（無法取得時）
    """
    if not address or not address.strip():
        return None

    address = address.strip()

    # 步驟 1：查 SQLite cache
    if db is not None:
        cached = db.get_geocode_cache(address)
        if cached:
            logger.debug(f"Geocode cache 命中：{address}")
            return cached

    coords = None

    # 步驟 2：Google Geocoding API
    google_key = os.getenv("GOOGLE_GEOCODING_API_KEY", "").strip()
    if google_key:
        coords = await _geocode_google(address, google_key, client)

    # 步驟 3：Nominatim 備援
    if coords is None:
        coords = await _geocode_nominatim(address, client)

    # 存入快取
    if coords and db is not None:
        db.set_geocode_cache(address, coords[0], coords[1])

    return coords


async def _geocode_google(
    address: str,
    api_key: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[tuple[float, float]]:
    """呼叫 Google Geocoding API 取得座標"""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": api_key,
        "language": "zh-TW",
        "region": "TW",
    }

    try:
        should_close = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=10.0)

        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            lat, lng = loc["lat"], loc["lng"]
            logger.debug(f"Google Geocode 成功：{address} → ({lat}, {lng})")
            return (lat, lng)
        else:
            logger.warning(f"Google Geocode 無結果：{address}，status={data.get('status')}")
            return None

    except Exception as e:
        logger.warning(f"Google Geocode 失敗：{address}，錯誤：{e}")
        return None
    finally:
        if should_close and client:
            await client.aclose()


async def _geocode_nominatim(
    address: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[tuple[float, float]]:
    """
    呼叫 Nominatim（OpenStreetMap）取得座標。
    遵守速率限制：每次呼叫前等待 _NOMINATIM_DELAY 秒。
    """
    # 台灣地址補上「台灣」前綴，提高 Nominatim 辨識率
    query = address if "台灣" in address or "Taiwan" in address else f"台灣 {address}"

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "tw",
    }
    headers = {
        "User-Agent": "rental-detective-tw/1.0 (https://github.com/)",
        "Accept-Language": "zh-TW,zh;q=0.9",
    }

    # 遵守 Nominatim 速率限制
    await asyncio.sleep(_NOMINATIM_DELAY)

    try:
        should_close = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=15.0)

        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        results = resp.json()

        if results:
            lat = float(results[0]["lat"])
            lng = float(results[0]["lon"])
            logger.debug(f"Nominatim Geocode 成功：{address} → ({lat}, {lng})")
            return (lat, lng)
        else:
            logger.warning(f"Nominatim Geocode 無結果：{address}")
            return None

    except Exception as e:
        logger.warning(f"Nominatim Geocode 失敗：{address}，錯誤：{e}")
        return None
    finally:
        if should_close and client:
            await client.aclose()
