"""
src/notifier.py — LINE Bot 推播模組

使用 LINE Messaging API v3 的 push_message 功能，
將符合條件的新房源推播給指定用戶。

推播格式：
🏠 [標題]
📍 [行政區]｜[捷運站] ([距離]m) - [路線]
💰 月租 [價格] 元
📐 [坪數] 坪｜[樓層]/[總樓層]F
🏷️ [標籤1] [標籤2]
🔗 [連結]
"""

import asyncio
import logging
from typing import Optional

from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    PushMessageRequest,
    TextMessage,
)
from linebot.v3.messaging.exceptions import ApiException

logger = logging.getLogger(__name__)

# 推播訊息之間的間隔（秒），遵守 LINE API 速率限制
_LINE_PUSH_DELAY = 0.5


class LineNotifier:
    """LINE Bot 推播客戶端"""

    def __init__(self, channel_access_token: str, push_target: str):
        """
        channel_access_token: LINE Bot 的 Channel Access Token
        push_target: 推播目標的 ID（個人：U 開頭；群組：C 開頭）
        """
        if not channel_access_token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN 未設定")
        if not push_target:
            raise ValueError("LINE_USER_ID 未設定")

        config = Configuration(access_token=channel_access_token)
        self._api_client = AsyncApiClient(config)
        self.api = AsyncMessagingApi(self._api_client)
        self.push_target = push_target

    async def push(self, listing: dict) -> bool:
        """
        推播一筆房源通知給目標用戶。
        成功回傳 True，失敗回傳 False（不拋出例外，確保主程式繼續運行）。
        """
        message_text = self._format_message(listing)

        try:
            await self.api.push_message(
                PushMessageRequest(
                    to=self.push_target,
                    messages=[TextMessage(text=message_text)],
                )
            )
            logger.info(f"LINE 推播成功：{listing.get('title', '')}")
            return True

        except ApiException as e:
            logger.error(
                f"LINE API 錯誤：{e.status} - {listing.get('title', '')}，"
                f"回應：{e.body}"
            )
            return False
        except Exception as e:
            logger.error(f"LINE 推播失敗：{listing.get('title', '')}，錯誤：{e}")
            return False

    async def push_batch(self, listings: list[dict]) -> tuple[int, int]:
        """
        批次推播多筆房源。
        推播之間加入 _LINE_PUSH_DELAY 秒延遲，避免觸發速率限制。

        回傳 (成功數, 失敗數)。
        """
        success_count = 0
        fail_count = 0

        for i, listing in enumerate(listings):
            ok = await self.push(listing)
            if ok:
                success_count += 1
            else:
                fail_count += 1

            # 最後一筆不需要等待
            if i < len(listings) - 1:
                await asyncio.sleep(_LINE_PUSH_DELAY)

        logger.info(f"LINE 批次推播完成：成功 {success_count}，失敗 {fail_count}")
        return success_count, fail_count

    def _format_message(self, listing: dict) -> str:
        """
        將房源資料格式化為 LINE 訊息文字。

        格式：
        🏠 [標題]
        📍 [行政區]｜[捷運站] ([距離]m) - [路線]
        💰 月租 [價格] 元
        📐 [坪數] 坪｜[樓層]/[總樓層]F
        🏷️ [標籤1] [標籤2]
        🔗 [連結]
        """
        title = listing.get("title") or "（無標題）"
        district = listing.get("district") or "未知"
        price = listing.get("price")
        size = listing.get("size")
        floor = listing.get("floor") or ""
        total_floors = listing.get("total_floors") or ""
        url = listing.get("url") or ""

        # 捷運資訊
        mrt_station = listing.get("nearest_mrt_station")
        mrt_distance = listing.get("nearest_mrt_distance_m")
        mrt_line = listing.get("nearest_mrt_line")

        if mrt_station and mrt_distance is not None:
            mrt_info = f"{mrt_station} ({mrt_distance}m)"
            if mrt_line:
                mrt_info += f" - {mrt_line}"
        else:
            mrt_info = "捷運資訊未知"

        # 價格顯示
        price_str = f"{price:,}" if isinstance(price, int) else str(price or "面議")

        # 坪數顯示
        if size is not None:
            try:
                size_str = f"{float(size):.1f}".rstrip("0").rstrip(".")
            except (ValueError, TypeError):
                size_str = str(size)
        else:
            size_str = "未知"

        # 樓層顯示
        if floor and total_floors:
            floor_str = f"{floor}/{total_floors}F"
        elif floor:
            floor_str = f"{floor}F"
        else:
            floor_str = "樓層未知"

        # 特色標籤
        features = listing.get("features") or []
        if isinstance(features, list) and features:
            features_str = " ".join(f"#{f}" for f in features[:5])
        else:
            features_str = ""

        # 組合訊息
        lines = [
            f"🏠 {title}",
            f"📍 {district}｜{mrt_info}",
            f"💰 月租 {price_str} 元",
            f"📐 {size_str} 坪｜{floor_str}",
        ]

        if features_str:
            lines.append(f"🏷️ {features_str}")

        if url:
            lines.append(f"🔗 {url}")

        return "\n".join(lines)

    async def close(self):
        """關閉 LINE API client"""
        await self._api_client.__aexit__(None, None, None)
