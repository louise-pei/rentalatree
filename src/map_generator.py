"""
src/map_generator.py — 地圖頁面與 listings.json 產生模組

輸出：
1. data/listings.json — 房源資料（Leaflet 地圖動態讀取）
2. docs/map.html — 完整 Leaflet 靜態地圖頁面（部署至 GitHub Pages）

地圖功能：
- 房源標記（marker）+ 點擊 popup
- 台北捷運站點小圓點（顏色依路線區分）
- 側邊欄篩選（價格、坪數、行政區）— 純前端 JS
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

TZ_TAIPEI = timezone(timedelta(hours=8))


def generate_map(
    listings: list[dict],
    mrt_stations: list[dict],
    map_cfg: dict,
):
    """
    主要入口：產生 listings.json 和 map.html。

    listings: 所有有座標的房源列表
    mrt_stations: 捷運站點列表
    map_cfg: config.yaml 的 map 區塊
    """
    output_json = map_cfg.get("output_json", "data/listings.json")
    output_html = map_cfg.get("output_html", "docs/map.html")

    # 確保輸出資料夾存在
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_html).parent.mkdir(parents=True, exist_ok=True)

    # 產生 listings.json
    _write_listings_json(listings, output_json)

    # 產生 map.html
    _write_map_html(listings, mrt_stations, map_cfg, output_html)


def _write_listings_json(listings: list[dict], output_path: str):
    """將房源資料序列化為 JSON 檔"""
    # 只輸出地圖需要的欄位（避免敏感資訊外洩）
    safe_listings = []
    for listing in listings:
        safe_listings.append({
            "id":                     listing.get("id", ""),
            "source":                 listing.get("source", ""),
            "title":                  listing.get("title", ""),
            "price":                  listing.get("price"),
            "district":               listing.get("district", ""),
            "address":                listing.get("address", ""),
            "lat":                    listing.get("lat"),
            "lng":                    listing.get("lng"),
            "size":                   listing.get("size"),
            "room_type":              listing.get("room_type", ""),
            "features":               listing.get("features") or [],
            "floor":                  listing.get("floor", ""),
            "total_floors":           listing.get("total_floors", ""),
            "nearest_mrt_station":    listing.get("nearest_mrt_station", ""),
            "nearest_mrt_line":       listing.get("nearest_mrt_line", ""),
            "nearest_mrt_distance_m": listing.get("nearest_mrt_distance_m"),
            "url":                    listing.get("url", ""),
            "images":                 (listing.get("images") or [])[:3],
            "posted_at":              listing.get("posted_at", ""),
            "crawled_at":             listing.get("crawled_at", ""),
        })

    payload = {
        "generated_at": datetime.now(TZ_TAIPEI).isoformat(),
        "total": len(safe_listings),
        "listings": safe_listings,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"listings.json 已更新：{len(safe_listings)} 筆 → {output_path}")


def _write_map_html(
    listings: list[dict],
    mrt_stations: list[dict],
    map_cfg: dict,
    output_path: str,
):
    """產生完整的 Leaflet 靜態地圖 HTML"""

    default_lat = map_cfg.get("default_lat", 25.0330)
    default_lng = map_cfg.get("default_lng", 121.5654)
    default_zoom = map_cfg.get("default_zoom", 13)

    # 捷運站資料內嵌到 HTML（避免額外 fetch）
    mrt_json = json.dumps(mrt_stations, ensure_ascii=False)

    # 取得所有行政區（用於篩選器）
    districts = sorted(set(
        l.get("district", "") for l in listings if l.get("district")
    ))
    districts_options = "\n".join(
        f'<option value="{d}">{d}</option>' for d in districts
    )

    generated_at = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>租屋偵探｜台北租屋地圖</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; height: 100vh; overflow: hidden; }}

    /* 側邊欄 */
    #sidebar {{
      width: 300px;
      min-width: 260px;
      background: #fff;
      box-shadow: 2px 0 8px rgba(0,0,0,.12);
      display: flex;
      flex-direction: column;
      z-index: 1000;
    }}
    #sidebar-header {{
      padding: 16px;
      background: #2c3e50;
      color: #fff;
    }}
    #sidebar-header h1 {{ font-size: 1.1rem; font-weight: 700; }}
    #sidebar-header p {{ font-size: .75rem; opacity: .7; margin-top: 2px; }}
    #filters {{
      padding: 12px 16px;
      border-bottom: 1px solid #eee;
      flex-shrink: 0;
    }}
    .filter-group {{ margin-bottom: 12px; }}
    .filter-group label {{ font-size: .8rem; color: #555; display: block; margin-bottom: 4px; font-weight: 600; }}
    .filter-group input[type=range] {{ width: 100%; cursor: pointer; }}
    .filter-group select {{ width: 100%; padding: 5px 8px; border: 1px solid #ddd; border-radius: 4px; font-size: .85rem; }}
    .range-display {{ font-size: .8rem; color: #2980b9; font-weight: 600; margin-top: 2px; }}
    #listing-count {{
      padding: 8px 16px;
      font-size: .8rem;
      color: #888;
      border-bottom: 1px solid #eee;
      flex-shrink: 0;
    }}
    #listing-list {{
      overflow-y: auto;
      flex: 1;
    }}
    .listing-card {{
      padding: 12px 16px;
      border-bottom: 1px solid #f0f0f0;
      cursor: pointer;
      transition: background .15s;
    }}
    .listing-card:hover {{ background: #f8f9fa; }}
    .listing-card .card-title {{ font-size: .9rem; font-weight: 600; color: #2c3e50; margin-bottom: 4px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
    .listing-card .card-price {{ color: #e74c3c; font-weight: 700; font-size: .95rem; }}
    .listing-card .card-meta {{ font-size: .75rem; color: #888; margin-top: 3px; }}
    .listing-card .card-mrt {{ font-size: .75rem; color: #2980b9; margin-top: 2px; }}

    /* 地圖 */
    #map {{ flex: 1; z-index: 0; }}

    /* Popup */
    .popup-content {{ min-width: 200px; max-width: 260px; }}
    .popup-content h3 {{ font-size: .9rem; font-weight: 700; margin-bottom: 6px; color: #2c3e50; }}
    .popup-price {{ color: #e74c3c; font-weight: 700; font-size: 1rem; }}
    .popup-meta {{ font-size: .8rem; color: #555; margin-top: 4px; line-height: 1.6; }}
    .popup-link {{ display: inline-block; margin-top: 8px; padding: 5px 10px; background: #2980b9; color: #fff; border-radius: 4px; text-decoration: none; font-size: .8rem; }}
    .popup-link:hover {{ background: #1a6fa0; }}

    /* 響應式 */
    @media (max-width: 600px) {{
      #sidebar {{ width: 100%; min-width: unset; height: 40vh; }}
      body {{ flex-direction: column; }}
      #map {{ height: 60vh; flex: unset; }}
    }}
  </style>
</head>
<body>

<div id="sidebar">
  <div id="sidebar-header">
    <h1>🏠 租屋偵探</h1>
    <p>更新時間：{generated_at}</p>
  </div>

  <div id="filters">
    <div class="filter-group">
      <label>月租上限：<span class="range-display" id="price-display">--</span></label>
      <input type="range" id="price-range" min="5000" max="80000" step="1000" value="80000">
    </div>
    <div class="filter-group">
      <label>最小坪數：<span class="range-display" id="size-display">--</span></label>
      <input type="range" id="size-range" min="5" max="60" step="1" value="5">
    </div>
    <div class="filter-group">
      <label>行政區</label>
      <select id="district-select">
        <option value="">全部行政區</option>
        {districts_options}
      </select>
    </div>
  </div>

  <div id="listing-count">載入中...</div>
  <div id="listing-list"></div>
</div>

<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
// ── 初始化地圖 ──
const map = L.map('map').setView([{default_lat}, {default_lng}], {default_zoom});
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 19,
}}).addTo(map);

// ── 捷運站點資料（內嵌）──
const mrtStations = {mrt_json};

// ── 捷運路線顏色對應 ──
const lineColors = {{
  '淡水信義線': '#EF2626',
  '板南線': '#0070BD',
  '中和新蘆線': '#F8B61C',
  '松山新店線': '#008659',
  '文湖線': '#C49A17',
  '環狀線': '#FFCB00',
  '新北投支線': '#EF2626',
  '小碧潭支線': '#008659',
}};

// ── 繪製捷運站點（小圓點）──
const mrtLayer = L.layerGroup().addTo(map);
mrtStations.forEach(station => {{
  const color = (station.lines && station.lines[0]) ? lineColors[station.lines[0].name] || '#888' : '#888';
  L.circleMarker([station.lat, station.lng], {{
    radius: 4,
    color: color,
    fillColor: color,
    fillOpacity: 0.85,
    weight: 1,
  }}).bindTooltip(station.name, {{ permanent: false, direction: 'top', className: 'mrt-tooltip' }})
    .addTo(mrtLayer);
}});

// ── 房源資料 ──
let allListings = [];
let markers = [];
const markerLayer = L.layerGroup().addTo(map);
const listingListEl = document.getElementById('listing-list');
const countEl = document.getElementById('listing-count');

// 從 listings.json 讀取（相對路徑）
fetch('../data/listings.json')
  .then(r => r.json())
  .then(data => {{
    allListings = data.listings || [];
    renderAll();
  }})
  .catch(err => {{
    countEl.textContent = '⚠️ 無法載入房源資料';
    console.error('載入 listings.json 失敗：', err);
  }});

// ── 篩選邏輯 ──
function getFilters() {{
  return {{
    maxPrice: parseInt(document.getElementById('price-range').value),
    minSize:  parseInt(document.getElementById('size-range').value),
    district: document.getElementById('district-select').value,
  }};
}}

function passesFilter(listing, filters) {{
  if (listing.price !== null && listing.price > filters.maxPrice) return false;
  if (listing.size  !== null && listing.size  < filters.minSize)  return false;
  if (filters.district && listing.district !== filters.district)  return false;
  return true;
}}

function renderAll() {{
  const filters = getFilters();
  const visible = allListings.filter(l => l.lat && l.lng && passesFilter(l, filters));

  // 清除舊 markers
  markerLayer.clearLayers();
  markers = [];
  listingListEl.innerHTML = '';

  visible.forEach((listing, idx) => {{
    // 建立地圖 marker
    const marker = L.marker([listing.lat, listing.lng]);
    const popupHtml = buildPopup(listing);
    marker.bindPopup(popupHtml, {{ maxWidth: 280 }});
    marker.addTo(markerLayer);
    markers.push(marker);

    // 建立側邊欄卡片
    const card = buildCard(listing, idx);
    listingListEl.appendChild(card);
  }});

  countEl.textContent = `共 ${{visible.length}} 筆房源（總計 ${{allListings.length}} 筆）`;
}}

function buildPopup(listing) {{
  const price = listing.price ? listing.price.toLocaleString() + ' 元/月' : '面議';
  const size  = listing.size  ? listing.size + ' 坪' : '未知';
  const mrt   = listing.nearest_mrt_station
    ? `${{listing.nearest_mrt_station}} (${{listing.nearest_mrt_distance_m}}m)`
    : '捷運資訊未知';
  const features = (listing.features || []).slice(0, 5).map(f => `<span style="background:#eef;padding:1px 5px;border-radius:3px;font-size:.75rem;margin-right:3px">${{f}}</span>`).join('');

  return `<div class="popup-content">
    <h3>${{listing.title || '（無標題）'}}</h3>
    <div class="popup-price">${{price}}</div>
    <div class="popup-meta">
      📍 ${{listing.district || ''}}｜${{listing.address || ''}}<br>
      📐 ${{size}}｜${{listing.floor || ''}}/${{listing.total_floors || ''}}F<br>
      🚇 ${{mrt}}<br>
      ${{features ? '🏷️ ' + features : ''}}
    </div>
    ${{listing.url ? `<a class="popup-link" href="${{listing.url}}" target="_blank" rel="noopener">查看詳情 →</a>` : ''}}
  </div>`;
}}

function buildCard(listing, idx) {{
  const card = document.createElement('div');
  card.className = 'listing-card';
  const price = listing.price ? listing.price.toLocaleString() + ' 元' : '面議';
  const mrt   = listing.nearest_mrt_station
    ? `🚇 ${{listing.nearest_mrt_station}} ${{listing.nearest_mrt_distance_m}}m`
    : '';
  card.innerHTML = `
    <div class="card-title">${{listing.title || '（無標題）'}}</div>
    <div class="card-price">💰 ${{price}}</div>
    <div class="card-meta">📐 ${{listing.size || '?'}} 坪 ｜ ${{listing.district || ''}}</div>
    ${{mrt ? `<div class="card-mrt">${{mrt}}</div>` : ''}}
  `;
  // 點擊卡片：地圖飛到該 marker 並開啟 popup
  card.addEventListener('click', () => {{
    map.flyTo([listing.lat, listing.lng], 16, {{ duration: 0.8 }});
    if (markers[idx]) markers[idx].openPopup();
  }});
  return card;
}}

// ── 篩選器事件 ──
const priceRange    = document.getElementById('price-range');
const sizeRange     = document.getElementById('size-range');
const districtSelect = document.getElementById('district-select');
const priceDisplay  = document.getElementById('price-display');
const sizeDisplay   = document.getElementById('size-display');

function updateDisplays() {{
  priceDisplay.textContent = parseInt(priceRange.value).toLocaleString() + ' 元';
  sizeDisplay.textContent  = sizeRange.value + ' 坪以上';
}}

priceRange.addEventListener('input', () => {{ updateDisplays(); renderAll(); }});
sizeRange.addEventListener('input',  () => {{ updateDisplays(); renderAll(); }});
districtSelect.addEventListener('change', renderAll);

updateDisplays();
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"map.html 已產生：{len(listings)} 個 markers → {output_path}")
