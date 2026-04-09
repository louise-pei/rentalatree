# 🏠 租屋偵探

自動爬取台灣 591 租屋網，依條件篩選後同步至 Notion 資料庫、透過 LINE Bot 推播新物件，並產生含捷運站的互動地圖部署至 GitHub Pages。

**排程**：每 2 小時自動執行（GitHub Actions）

---

## 功能一覽

| 功能 | 說明 |
|---|---|
| 🕷️ 自動爬蟲 | 591 租屋網，支援翻頁、攔截 XHR API |
| 🔍 智慧篩選 | 行政區、月租、坪數、排除關鍵字 |
| 🗺️ 捷運距離 | 自動計算與所有台北捷運站的直線距離 |
| 📋 Notion 整合 | 自動建立資料庫頁面，可直接在 Notion 瀏覽 |
| 📱 LINE 推播 | 新物件即時推播通知 |
| 🗺️ 互動地圖 | Leaflet.js 地圖，含篩選功能，部署至 GitHub Pages |
| 🔄 自動排程 | GitHub Actions 每 2 小時執行 |

---

## 前置需求

- Python 3.11+
- Git
- GitHub 帳號（用於 GitHub Pages + Actions）
- Notion 帳號（建立 Integration）
- LINE 帳號（建立 Messaging API Bot）

---

## 第一步：建立 Notion Integration 與 Database

### 1-1. 建立 Notion Integration

1. 前往 https://www.notion.so/my-integrations
2. 點擊「+ New integration」
3. 填寫名稱（例如：租屋偵探）
4. 選擇 Workspace
5. Capabilities 勾選：**Read content**、**Update content**、**Insert content**
6. 點擊「Save」
7. 複製 **Internal Integration Token**（格式：`secret_xxx...`）→ 這是 `NOTION_TOKEN`

### 1-2. 建立 Notion Database

1. 在 Notion 新增一個 **全頁資料庫（Full-page database）**
2. 建立以下屬性（按照類型建立）：

| 屬性名稱 | 類型 | 備註 |
|---|---|---|
| 標題 | Title | （預設已存在） |
| 來源 | Text | |
| 來源ID | Text | 用於去重，可設為隱藏 |
| 月租金 | Number | 格式選「NT$」 |
| 行政區 | Select | |
| 地址 | Text | |
| 坪數 | Number | |
| 房型 | Select | |
| 特色 | Multi-select | |
| 樓層 | Text | |
| 最近捷運站 | Text | |
| 捷運線 | Select | 可手動設定各路線顏色 |
| 捷運距離(公尺) | Number | |
| 連結 | URL | |
| 刊登時間 | Date | |
| 爬取時間 | Date | |
| 已推播 | Checkbox | |
| 圖片 | Text | |

3. 從頁面 URL 取得 **Database ID**：
   ```
   https://www.notion.so/[workspace]/[DATABASE_ID]?v=...
   ```
   32 碼英數字，去掉連字號 → 這是 `NOTION_DATABASE_ID`

### 1-3. 將 Integration 分享給 Database

1. 開啟 Database 頁面
2. 右上角點「...」→「Add connections」
3. 搜尋並選擇剛才建立的 Integration
4. 確認授權

---

## 第二步：建立 LINE Bot

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立 Provider（若尚未有）
3. 建立新的 Channel：選擇 **Messaging API**
4. 進入 Channel 設定，**Messaging API** 分頁：
   - 點擊「Issue」發行 **Channel access token**（長效型）→ 這是 `LINE_CHANNEL_ACCESS_TOKEN`
5. 取得自己的 **LINE User ID**：
   - 方法一：在 [LINE Official Account Manager](https://manager.line.biz/) > 帳號設定 > Messaging API > Webhook，用任意訊息觸發 webhook，從 log 取得 userId
   - 方法二：使用 [LINE API test tool](https://developers.line.biz/console/) 發送測試請求
   - User ID 格式為 `U` 開頭 32 碼字串 → 這是 `LINE_USER_ID`

---

## 第三步：本地端設定

```bash
# 1. Clone 或初始化 repo
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO

# 2. 建立虛擬環境
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安裝套件
pip install -r requirements.txt

# 4. 安裝 Playwright 瀏覽器
playwright install chromium

# 5. 設定環境變數
cp .env.example .env
# 使用編輯器開啟 .env 並填入所有 API 金鑰
```

### .env 填寫說明

```ini
NOTION_TOKEN=secret_你的Integration Token
NOTION_DATABASE_ID=你的32碼DatabaseID
LINE_CHANNEL_ACCESS_TOKEN=你的LINE Bot Token
LINE_USER_ID=U你的LINE用戶ID
GOOGLE_GEOCODING_API_KEY=    # 可留空，改用免費 Nominatim
DB_PATH=data/rental_detective.db
RUN_MODE=local               # 本機執行改用 local
```

### 本地測試執行

```bash
# 一般執行（headless）
python main.py

# 本機模式（可看到瀏覽器視窗，較不易被封鎖）
RUN_MODE=local python main.py

# 或直接用 shell script
./run_local.sh
```

---

## 第四步：部署至 GitHub

### 4-1. 設定 GitHub Repository Secrets

前往 GitHub Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

依序新增以下 Secrets：

| Secret 名稱 | 說明 |
|---|---|
| `NOTION_TOKEN` | Notion Integration Token |
| `NOTION_DATABASE_ID` | Notion Database ID |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Bot Channel Access Token |
| `LINE_USER_ID` | LINE 推播目標的 User ID |
| `GOOGLE_GEOCODING_API_KEY` | （選填）Google Geocoding API Key |

### 4-2. 啟用 GitHub Pages

1. GitHub Repo → **Settings** → **Pages**
2. Source 選擇：**GitHub Actions**（使用 workflow 自動部署）
3. 儲存後等待第一次 workflow 執行

### 4-3. 初次推送

```bash
git add .
git commit -m "feat: 初始化租屋偵探"
git push origin main
```

### 4-4. 手動觸發首次執行

1. GitHub Repo → **Actions** → 選擇「租屋偵探爬蟲」workflow
2. 點擊「Run workflow」→「Run workflow」
3. 等待約 5-10 分鐘完成
4. 執行成功後，GitHub Pages 會自動更新地圖

地圖網址：`https://YOUR_USERNAME.github.io/YOUR_REPO/`

---

## 修改篩選條件

編輯 `config.yaml` 的 `filter` 區塊：

```yaml
filter:
  districts:            # 目標行政區
    - 大安區
    - 信義區
    - 松山區
  max_price: 28000      # 月租上限（元）
  min_size_ping: 20     # 最小坪數
  exclude_keywords:     # 標題含這些字詞則排除
    - 頂加
    - 隔套
    - 車庫
  notify_within_minutes: 120  # 只推播 2 小時內的新物件
```

修改後 commit 並 push，下次 Actions 執行時生效。

---

## 本機備援模式（591 被封鎖時）

若 GitHub Actions 被 591 封鎖（可從 Actions log 確認），改用本機執行：

```bash
# 確保 .env 已設定，且 RUN_MODE=local
./run_local.sh
```

此腳本會：
1. 在本機用非 headless 模式執行爬蟲
2. 自動 commit 更新的 listings.json 和 map.html
3. Push 到 GitHub，GitHub Pages 自動更新

---

## 資料夾結構

```
租屋偵探/
├── .env.example              # 環境變數範例（不含真實金鑰）
├── .github/workflows/
│   └── crawl.yml             # GitHub Actions 排程設定
├── config.yaml               # 主要設定檔
├── main.py                   # 主程式
├── requirements.txt          # Python 套件清單
├── run_local.sh              # 本機執行腳本
├── data/
│   └── listings.json         # 自動產生（房源資料，供地圖讀取）
├── docs/
│   └── map.html              # 自動產生（GitHub Pages 地圖頁面）
└── src/
    ├── crawlers/
    │   └── site_591.py       # 591 租屋網爬蟲
    ├── data/
    │   └── mrt_stations.json # 台北捷運站點座標
    ├── db.py                 # SQLite 資料庫介面
    ├── filter.py             # 房源篩選邏輯
    ├── geo.py                # 地理編碼 + 捷運距離計算
    ├── map_generator.py      # 地圖頁面產生器
    ├── notion_client.py      # Notion API 寫入模組
    └── notifier.py           # LINE Bot 推播模組
```

---

## 常見問題

### Q: 591 顯示無資料或被封鎖？
- GitHub Actions 的 IP 可能被 591 封鎖
- 解決方案：使用 `./run_local.sh` 在本機執行
- 或調整 `config.yaml` 的 `delay_min`/`delay_max` 增加延遲

### Q: LINE 推播收不到？
- 確認 `LINE_USER_ID` 正確（`U` 開頭）
- 確認已加 LINE Bot 為好友
- 檢查 `LINE_CHANNEL_ACCESS_TOKEN` 是否過期（長效 token 通常不會過期）

### Q: Notion 頁面沒有更新？
- 確認 Integration 已正確「分享」給 Database（步驟 1-3）
- 確認 `NOTION_DATABASE_ID` 不含連字號（純 32 碼）
- 查看 Actions log 的錯誤訊息

### Q: 地圖上沒有 marker？
- 確認房源有座標（`lat`/`lng`）
- 台灣地址 Nominatim 準確率較低，部分地址可能無法取得座標
- 考慮設定 `GOOGLE_GEOCODING_API_KEY`（前 $200/月 免費）

### Q: Actions cache 到期（7 天）後怎麼辦？
- SQLite DB 消失後，所有舊物件會被視為新物件重新推播一次
- 這是正常行為，之後恢復正常去重
- 如需永久保存，可考慮改用 PostgreSQL 等持久化方案

---

## 技術棧

- **語言**：Python 3.11+
- **爬蟲**：Playwright + httpx
- **資料庫**：SQLite（去重）+ Notion（主介面）
- **地理編碼**：Nominatim（免費）/ Google Geocoding API（選用）
- **推播**：LINE Messaging API v3
- **地圖**：Leaflet.js（CDN）
- **排程**：GitHub Actions
- **部署**：GitHub Pages
