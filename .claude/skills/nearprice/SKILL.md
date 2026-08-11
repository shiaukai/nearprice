---
name: nearprice
description: 查台灣不動產行情 —— 給一個地址，回傳附近的內政部實價登錄成交（買賣/租賃/預售屋）與各大房仲網站的現售、出租開價，並產出 HTML 視覺化報告。當使用者提到「實價登錄」、「這附近房價多少」、「行情」、「這間房子貴不貴」、「租金行情」、「議價空間」、「租金報酬率」，或給了一個台灣地址想知道週邊成交價／開價時使用。
---

# 台灣不動產行情查詢

給一個地址，一次拿到三種價格：**已成交**（內政部實價登錄）、**現在賣方要價**、
**現在房東要價**。三者一起看才知道行情、議價空間與租金報酬率。

## 路徑約定

底下所有指令裡的 `$SKILL` 是**這個 skill 的 base directory**（載入 skill 時會告訴你）。
執行前先設好，之後照抄即可：

```bash
SKILL="<載入時給的 base directory>"
```

輸出檔放在使用者目前工作目錄下的 `out/`（不存在就 `mkdir -p out`）。
`$SKILL` 底下不要寫入 —— skill 目錄可能是唯讀或被共用的。

## 一句話用法

```bash
mkdir -p out && python3 "$SKILL/scripts/nearby.py" "台北市大安區忠孝東路四段45號" --json out/nearby.json && python3 "$SKILL/scripts/report.py" out/nearby.json --html out/report.html
```

跑完會有兩個檔：`out/nearby.json`（結構化資料）與 `out/report.html`（可分享的報告）。
最後用 SendUserFile 把 `out/report.html` 交給使用者。

## 標準流程

1. **確認地址**。使用者只給「大安區」這種行政區也可以跑，但半徑篩選會失去意義 ——
   這時候直接用 `lvr.py` 查整個區，不要用 `nearby.py`。地址不完整就問一次。
2. **跑 `nearby.py`**。預設半徑 500 公尺、回溯 24 個月。使用者說「附近」通常指走路
   距離，300–500m 合適；郊區或案件稀少時放寬到 1000m。
   ```bash
   python3 "$SKILL/scripts/nearby.py" "<地址>" --radius 500 --months 24 --json out/nearby.json
   ```
   這一步會打十幾次網路請求（含 sleep），一個地址大約要 30–60 秒，是正常的。
   stderr 會印出每個階段的筆數；**半徑內筆數 < 20 就該放寬半徑或月數再跑一次**，
   否則中位數沒有代表性。
3. **產 HTML 報告**，然後用 SendUserFile 把 `out/report.html` 給使用者看。
   ```bash
   python3 "$SKILL/scripts/report.py" out/nearby.json --html out/report.html
   ```
4. **在對話裡講重點**，不要叫使用者自己去讀報告。至少講：
   - 買賣單價中位數與 P25–P75 區間（**永遠講中位數，不要講平均** —— 實登單價會被
     車位、豪宅、特殊關係人交易嚴重拉偏）
   - 最近幾季的趨勢方向
   - 市場開價 vs 實登中位數的差距（≈ 議價空間的粗估）
   - 租金中位數與推估年化報酬率
   - 樣本數太少、或抓取失敗的來源，要主動說

## 各支腳本

| 腳本 | 做什麼 |
|---|---|
| `scripts/nearby.py` | 主入口：地址 → 定位 → 實登 + 開價 → 統計 JSON |
| `scripts/report.py` | JSON → 自足 HTML 報告（inline SVG，深淺色皆可） |
| `scripts/lvr.py` | 內政部實價登錄即時查詢，可單獨用（查整區、查社區、查路名） |
| `scripts/geocode.py` | 地址 → 座標，可插拔 provider |
| `scripts/listings.py` | 591 / 信義 / 永慶 的現售與出租開價 |
| `scripts/twcrypto.py` | 純 Python 的 CryptoJS 相容 AES（實登 API 要用） |

全部只用 Python 標準函式庫，**不需要 pip install 任何東西**。

單獨查一個行政區的實登（不做半徑篩選）：

```bash
python3 "$SKILL/scripts/lvr.py" --city 台北市 --town 大安區 --type biz --start 114/1 --end 115/7 --ftype 05
```

`--type` 可用 `biz`(買賣) / `rent`(租賃) / `sale`(預售屋) / `saleRemark`(預售建案備查)。
其他參數與代碼表見 `references/lvr-api.md`。

## 定位（geocode）設定

精度直接決定「半徑內」篩得準不準。provider 依序嘗試，**沒設定的會直接跳過、
不發請求也不產生費用**。想知道目前狀態就跑（不會發任何請求）：

```bash
python3 "$SKILL/scripts/geocode.py" --doctor
```

- **`google`** —— Google Geocoding API，**一般使用者的主力**。門牌級精度。
  金鑰順序：環境變數 `GOOGLE_MAPS_API_KEY` → `.env` → `config.json`。
  一個地址只打 1 次。錯誤訊息會直接說明是金鑰無效、帳單沒開還是超額。
- **`locus`** —— 作者自架的 Geo API（`POST /resolve` + `X-API-Key`）。
  有部署才會生效，沒有就跳過。除錯用 `geocode.py --probe-locus "<地址>"`。
- **`custom`** —— 其他自架服務。把 URL 樣板與回應取值路徑寫進 `config.json` 即可，
  不用改程式碼。設定方式見 `references/geocoding.md`。
- **`lvr`** —— 零設定的免費備援：拿同一條路上的實登成交案件座標反推。
  **完全沒有任何金鑰時就是走這條，所以整個流程永遠跑得動。**
  但長路段會失準（實測台灣大道差 1876 公尺），此時半徑統計等於是錯的 ——
  **若輸出顯示 `provider: lvr`，要在結論裡告知使用者定位是估算的**，
  並建議設定 `GOOGLE_MAPS_API_KEY`。
- **`nominatim`** —— 只到「路/段」層級，誤差可達數百公尺，最後手段。

單獨測定位：

```bash
python3 "$SKILL/scripts/geocode.py" "台北市大安區忠孝東路四段45號"
```

設定檔找的順序是 `$NEARPRICE_CONFIG` → `$SKILL/config.json` → `~/.config/nearprice/config.json`。
skill 目錄若不可寫，就把設定放 `~/.config/nearprice/config.json`。

## 房仲網站抓不到的時候

`listings.py` 對每個站是 best-effort，單站失敗不會中斷其他站，失敗原因會進
JSON 的 `市場開價.失敗`。實測可靠度與已知限制見 `references/listings.md`。

**被擋或改版時，改用瀏覽器工具**（`mcp__Claude_Browser__*`）打開該站的搜尋頁，
用 `get_page_text` 或 `read_page` 取內容，整理成跟 `listings.py` 一樣的 schema
寫成 JSON，再合併進 `out/nearby.json` 的 `市場開價.資料` 陣列後重跑 `report.py`。
schema 欄位：

```json
{"來源":"", "類型":"售|租", "標題":"", "地址":"", "社區":"",
 "總價萬元":null, "月租金元":null, "單價萬元每坪":null, "坪數":null,
 "格局":"", "樓層":"", "屋齡":null, "型態":"", "連結":"", "lat":null, "lon":null}
```

抓網站要節制：`listings.py` 每次請求之間已經有 1–2 秒間隔，不要拿掉，也不要
為了湊資料把 `--pages` 開很大。

## 解讀資料時務必注意

- 實登是**已成交**且**有申報落差**（通常 1–2 個月），最新一兩個月的資料一定偏少。
- 「單價」是總價 ÷ 總面積。**含車位的案件單價會被拉低、純車位交易會出現極低單價**，
  備註欄有「含裝潢費」「毛胚屋」「親友交易」的要另外看待。報告的直方圖已經略去
  頭尾極端值，但表格是完整的。
- 市場開價是**要價不是成交價**，兩者差距在冷門區可以到兩成以上。
- 租金報酬率是用中位數推估的粗估值，沒扣稅、管理費與空置期，不能當投資建議。
- 內政部的查詢 API 是從官網前端逆向來的（見 `references/lvr-api.md`），
  官網改版就可能失效。失效時的判斷與修復步驟寫在該文件末尾。

## 參考文件

- `references/lvr-api.md` —— 內政部實登 API 的完整規格：加密方式、參數、代碼表、回應欄位
- `references/listings.md` —— 各房仲網站的網址規則、解析方式、實測可靠度
- `references/geocoding.md` —— geocode provider 設定，含自架 TGOS 服務的 config 範例
