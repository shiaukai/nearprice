# 地址定位（geocode）設定

半徑篩選準不準，完全取決於這一步。`scripts/geocode.py` 支援六個 provider，
依 `config.json` 的 `providers` 順序嘗試，第一個成功的就採用。
**沒設定的 provider 會直接跳過，不會發出任何請求，也不會產生費用。**

先跑這個看目前狀態，不會發任何請求：

```bash
python3 "$SKILL/scripts/geocode.py" --doctor
```

```
嘗試順序   : google → lvr → nominatim

  ✓ google     可用   金鑰=.env 的 GOOGLE_MAPS_API_KEY
  ✓ lvr        可用   零設定備援（拿同路段實登成交案件反推）
  ✓ nominatim  可用   只到路段層級，誤差可達數百公尺

實際會用到：google（失敗時往後退）
```

| provider | 精度 | 費用 | 給誰用 |
|---|---|---|---|
| `google` | 門牌（ROOFTOP） | 付費，有免費額度 | **一般使用者的主力** |
| `locus` | 門牌 | 自架 | 作者自架的服務，沒有的人會被跳過 |
| `custom` | 看服務 | 看服務 | 任何其他自架定位服務 |
| `tgos` | 門牌 | 免費但限資格申請 | 政府／法人／學術／業界 |
| `lvr` | 門牌附近 | **免費、零設定** | 沒有任何金鑰時的備援 |
| `nominatim` | 路段 | 免費 | 最後手段 |

## `google` —— Google Geocoding API（推薦的主力）

只需要 **Geocoding API** 這一個 API，不需要 Maps JavaScript、Places 或其他。

啟用步驟：

1. Google Cloud Console 建專案並**開啟帳單**（沒開帳單一律 `REQUEST_DENIED`）
2. 啟用 **Geocoding API**
3. 建 API key，建議加「API 限制」只允許 Geocoding API
4. 金鑰放進 `.env`：`GOOGLE_MAPS_API_KEY=你的金鑰`

```json
{
  "providers": ["google", "lvr", "nominatim"],
  "google": {
    "api_key_env": "GOOGLE_MAPS_API_KEY",
    "api_key": "",
    "region": "tw",
    "language": "zh-TW",
    "enabled": true
  }
}
```

請求固定帶 `components=country:TW`，避免同名地址跑到別的國家；回傳的座標還會再檢查
一次是否落在台灣範圍內，不在就報錯而不是默默採用。

**用量**：`nearby.py` 一個地址只打 **1 次** Geocoding。要完全關掉就設
`"enabled": false`，或把金鑰拿掉（沒金鑰等於跳過，不會發請求）。

`location_type` 直接當成定位層級：

| Google | 報告顯示 | 意義 |
|---|---|---|
| `ROOFTOP` | 門牌 | 精確到建物 |
| `RANGE_INTERPOLATED` | 門牌（號碼內插推算） | 用門牌區間推算，可能差幾戶 |
| `GEOMETRIC_CENTER` | 路段中心 | 只認到路，半徑篩選會失準 |
| `APPROXIMATE` | 概略位置 | 通常只到行政區 |

`partial_match: true` 會在精度後面附註「部分比對」—— 代表地址沒完全對上，要留意。

錯誤處理：`ZERO_RESULTS` 安靜往下一個 provider；其他狀態會丟出明確錯誤，例如
`Google Geocoding 回 REQUEST_DENIED：The provided API key is invalid.`
（最常見的原因是**帳單沒開**或**沒啟用 Geocoding API**，不是金鑰打錯）。

## 設定檔位置

先找到哪個就用哪個：

1. `$NEARPRICE_CONFIG` 環境變數指到的檔
2. `.claude/skills/nearprice/config.json`
3. `~/.config/nearprice/config.json`

`config.example.json` 是可直接複製的範本。

## `locus` —— 自架的 Locus Geo API

> 這是作者自架的服務，**一般使用者沒有**。`config.example.json` 裡的 `base` 是空的，
> 所以這個 provider 會被直接跳過，不影響任何人。有自己的 Locus 部署才需要看這節。

服務把 TGOS 與 Google 包在後面（`GET /health` 會顯示 `tgos_enabled` / `google_enabled`）。
**所有端點（含 `/ui`、`/docs`）都要 `X-API-Key`**，沒有金鑰一律 401。
金鑰由 `/admin` 頁面簽發（`POST /admin/key`，需管理帳密），
也可以改走 `POST /admin/ip` 的來源 IP 白名單。

```json
{
  "providers": ["locus", "google", "lvr", "nominatim"],
  "locus": {
    "base": "https://<你的部署位址>",
    "path": "/resolve",
    "api_key_header": "X-API-Key",
    "api_key_env": "LOCUS_API_KEY",
    "api_key": "",
    "timeout": 30
  }
}
```

### 金鑰放哪 —— 有一個很容易踩的坑

找的順序是：**環境變數 → `.env` 檔 → `config.json` 的 `api_key`**。

> ⚠️ **`~/.zshrc` 沒有用。** 腳本跑在**非互動 shell**，zsh 只 source `~/.zshenv`，
> 不 source `~/.zshrc`。金鑰寫在 `.zshrc` 裡的話，你自己在終端機跑得起來，
> Claude 自動跑卻一律 401 —— 而且會靜默退到 `lvr` 備援，很難察覺。
> 症狀就是報告上的 `provider` 顯示 `lvr` 而不是 `locus`。

三種正確做法，擇一：

```bash
# A. 放 ~/.zshenv（不重複存放金鑰，最乾淨）
echo 'export LOCUS_API_KEY="你的金鑰"' >> ~/.zshenv

# B. 放 .env（不動 dotfile）
printf 'LOCUS_API_KEY=你的金鑰\n' > "$SKILL/.env" && chmod 600 "$SKILL/.env"

# C. 直接寫進 config.json 的 locus.api_key
```

`.env` 找的位置是 `$SKILL/.env` 與 `~/.config/nearprice/.env`，格式一行一個
`KEY=value`（`export ` 前綴、引號都會被忽略）。三者都已列入 `.gitignore`。

### 請求 / 回應（2026-08 實測）

沿用服務既有的 `/resolve`，不需要為這個 skill 改 API：

```
POST /resolve
X-API-Key: …
{"items":[{"id":"q","address":"台北市大安區忠孝東路四段45號"}]}
```

```json
{"results":[{
  "id": "q", "found": true, "precision": "house",
  "lat": 25.041902, "lon": 121.544883,
  "confidence": "high", "source": "tgos", "place_id": null,
  "formatted": "臺北市大安區光武里27鄰忠孝東路四段45號"
}]}
```

`precision` 直接當成定位層級（`house`→門牌、`street`/`road`→路段、`district`→行政區），
`confidence` 與 `source`（tgos / google）一併帶進報告的定位說明欄。
`found: false` 會丟出明確錯誤，鏈路自動往下一個 provider 走。

**欄位讀取有兩層。** 先照上面這個 schema 讀；讀不到才退回 `_find_latlon()` 的通用偵測 ——
遞迴找「鍵名像座標（lat/latitude/y、lon/lng/longitude/x）**且**值落在台灣經緯度範圍
（lat 21.5–26.5、lon 118–122.5）」的第一組值。所以：

- 服務改版換欄位名或改包裝層級，不會直接壞掉
- lat/lon 放反了會自動校正
- **TWD97（EPSG:3826）會被擋掉** —— `x≈302000, y≈2770000` 不在經緯度範圍內，
  會明確報「偵測不到台灣範圍內的 WGS84 座標」，而不是默默用錯的座標去算距離

### 為什麼一定要設一個真正的定位服務

`lvr` 備援靠「同路段最近的成交案件」反推座標，遇到很長的路就會差很多。實測：

| 地址 | 門牌級定位 vs `lvr` 備援的差距 |
|---|---|
| 台北市大安區忠孝東路四段45號 | 65 m |
| 新北市板橋區文化路一段266號 | 8 m |
| 台中市西屯區台灣大道三段301號 | **1876 m** |

台灣大道那筆差了將近兩公里 —— 圓心跑掉了，整份半徑統計就是錯的。
所以 `lvr` 只適合當「完全沒設定時仍然跑得動」的保底，正式使用請設 `google` 或 `locus`。

**怎麼確認實際用了哪一個**：報告最上方的「定位」那行，以及 `nearby.py` 的 stderr
都會印 provider 名稱。跑 `--doctor` 可以在不發請求的情況下看設定狀態。

### 設好金鑰後先驗一次

```bash
python3 "$SKILL/scripts/geocode.py" --probe-locus "台北市大安區忠孝東路四段45號"
```

會印出**原始回應全文**加上偵測到的座標。確認沒問題再跑正式的：

```bash
python3 "$SKILL/scripts/geocode.py" --provider locus "台北市大安區忠孝東路四段45號"
```

### 服務的其他端點

`/distance`、`/match`、`/compare`、`/optimize`（配送路線最佳化）目前這個 skill 用不到，
只用 `/resolve`。`/whoami` 是診斷用，可以確認反向代理後面取到的來源 IP 對不對。

## `custom` —— 其他自架的定位服務

不用改任何程式碼，把服務的呼叫方式描述進設定檔即可：

```json
{
  "providers": ["custom", "lvr", "nominatim"],
  "custom": {
    "url": "https://your-service.example.com/geocode?addr={address}",
    "method": "GET",
    "headers": { "Authorization": "Bearer YOUR_TOKEN" },
    "lat_path": "data.0.lat",
    "lon_path": "data.0.lon",
    "score_path": "data.0.matchScore",
    "timeout": 30
  }
}
```

- `{address}` 會被 **URL-encode** 後代入 `url`
- POST 的話加 `"body"`，裡面的 `{address}` 會用**未 encode** 的原字串代入：
  ```json
  "method": "POST",
  "headers": {"Content-Type": "application/json"},
  "body": "{\"address\":\"{address}\",\"srs\":\"EPSG:4326\"}"
  ```
- `lat_path` / `lon_path` 用點號走 JSON，純數字代表 list 索引。
  例：回應 `{"results":[{"geometry":{"y":25.04,"x":121.54}}]}`
  → `"lat_path": "results.0.geometry.y"`、`"lon_path": "results.0.geometry.x"`
- 座標必須是 **WGS84 / EPSG:4326**（緯度、經度），因為要跟實登回傳的 `lat`/`lon` 比對。
  服務若回 TWD97 / EPSG:3826，請在服務端先轉好。
- `score_path` 選填，只是把比對分數帶進報告，不影響邏輯。

設定完先測一次：

```bash
python3 scripts/geocode.py --provider custom "台北市大安區忠孝東路四段45號"
```

應該回 `{"lat":…, "lon":…, "provider":"custom", "precision":"門牌"}`。

## `tgos` —— 官方 TGOS 全國門牌地址定位服務

直接打官方 API，需要在 TGOS 申請 appId / apiKey（免費，但限政府機關／法人／
學術／業界申請）：

```json
{
  "providers": ["tgos", "lvr"],
  "tgos": {
    "appId": "YOUR_APP_ID",
    "apiKey": "YOUR_API_KEY",
    "srs": "EPSG:4326",
    "fuzzyType": "2"
  }
}
```

若官方端點路徑有變，用 `"url"` 覆寫。回應預期是
`{"AddressList":[{"X":121.54,"Y":25.04,"MATCH_TYPE":"…"}]}`（`X`=經度、`Y`=緯度）。
格式不同的話，改用 `custom` provider 描述它，不要改程式。

## `lvr` —— 用實價登錄自己的資料反推（零設定備援）

實登每一筆成交都帶座標，所以查同一條路的成交案件、挑門牌號最接近的那一筆，
就等於一個免費的門牌級 geocoder。

比對邏輯：先過濾同「路名 + 段」，再用 `|門牌號差|` 當成本，巷號對不上加罰分，
取成本最低的一筆。實測「台北市大安區忠孝東路四段45號」會對到「四段46號」。

限制：那條路近三年**要有成交紀錄**，沒有就回不出來（換 `nominatim`）。
新開發區、純住宅巷弄容易落空。

可調參數：

```json
{ "lvr_geocode_years": 3, "lvr_geocode_end_year": 115 }
```

## `nominatim` —— OpenStreetMap（最後手段）

台灣的 OSM 資料**只到「路/段」層級，查不到門牌號**，所以 `geocode.py` 會先把
門牌號拿掉再查。誤差可達數百公尺，用它定位時半徑篩選只能當參考。

請遵守 Nominatim 的使用政策（每秒 1 次、要有可識別的 User-Agent）：

```json
{ "nominatim_user_agent": "your-app/1.0 (you@example.com)" }
```

## 地址解析

`geocode.py` 的 `parse_address()` 會把中文地址拆成
`city / town / road / section / lane / alley / number`，
並把「台」正規化成「臺」（實登官方用「臺」）。單獨測：

```bash
python3 scripts/geocode.py --parse-only "台北市大安區忠孝東路四段45號"
```

解析不到 `city` + `town` + `road` 的話，`lvr` provider 會直接放棄 —— 地址太簡略
（例如只有「大安區忠孝東路」）就補上縣市再試。
