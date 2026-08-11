---
name: nearprice
description: 查台灣不動產行情 —— 給一個地址，回傳附近的內政部實價登錄成交（買賣/租賃/預售屋）與各大房仲網站的現售、出租開價，並產出 HTML 視覺化報告。當使用者提到「實價登錄」、「這附近房價多少」、「行情」、「這間房子貴不貴」、「租金行情」、「議價空間」、「租金報酬率」，或給了一個台灣地址想知道週邊成交價／開價時使用。
license: MIT
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/nearby.py:*), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/report.py:*), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/lvr.py:*), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/geocode.py:*), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/listings.py:*), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/relay.py:*)
---

# 台灣不動產行情查詢

給一個地址，一次拿到三種價格：**已成交**（內政部實價登錄）、**現在賣方要價**、
**現在房東要價**。三者一起看才知道行情、議價空間與租金報酬率。

## 引數

`$ARGUMENTS`

上面這行是使用者用 `/nearprice <地址>` 呼叫時帶的引數。有值就當作查詢地址
（後面可以跟 `--radius`／`--months` 之類的選項，照傳即可）；空白就照下面的流程
先問使用者要查哪個地址。

## 路徑約定

`${CLAUDE_SKILL_DIR}` 會被自動代換成這個 skill 的所在目錄，**直接照抄即可，
不需要自己設變數**。

> 若你看到的是字面上的 `${CLAUDE_SKILL_DIR}` 而不是一個真實路徑，代表目前的執行環境
> 不支援這個代換（Claude Code 以外的介面）。這時改用 `SKILL.md` 所在的那個目錄，
> 例如 `find / -name SKILL.md -path '*nearprice*'` 找到後用它的上層目錄。

輸出檔放在使用者目前工作目錄下的 `out/`（腳本會自己建目錄）。
`${CLAUDE_SKILL_DIR}` 底下不要寫入 —— 那是 git working tree，寫進去會污染
`git status`，而且 skill 目錄可能是唯讀或被共用的。

## 先決定走哪條路徑

這個 skill 有兩種模式。**開始前先跑一次**（很快，只打一個小請求）：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/relay.py check
```

- **回「有對外連線」→ 走路徑 A**（下面的「標準流程」）。Claude Code 幾乎都是這個。
- **回「連不到」→ 走路徑 B**（接力模式，見最後一節）。claude.ai / Cowork 的
  程式碼沙箱有網域白名單，預設連不到內政部。

不要在路徑 A 失敗後才反覆重試 —— 連線被沙箱擋掉和官網改版的症狀很像，
但處理方式完全不同。先 `check` 就不會誤判。

## 需要對外連線

路徑 A 的每一項功能都要打外網。**沙箱環境若沒開對應網域，會全部失敗**：

| 網域 | 用途 | 沒有的話 |
|---|---|---|
| `lvr.land.moi.gov.tw` | 內政部實價登錄 | **整個 skill 無法運作** |
| `maps.googleapis.com` | Google 定位 | 退到 `lvr` 備援定位 |
| `nominatim.openstreetmap.org` | 備援定位 | 少一層備援 |
| `sale.591.com.tw` `rent.591.com.tw` | 591 開價 | 少這個來源 |
| `www.sinyi.com.tw` | 信義開價 | 少這個來源 |
| `buy.yungching.com.tw` | 永慶開價 | 少這個來源 |

若查詢一直失敗且錯誤是連線逾時或被拒，先確認是不是網路被沙箱擋掉，
不要往 API 改版的方向排查。**這種情況改走路徑 B，不要放棄。**

## 一句話用法

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/nearby.py "台北市大安區忠孝東路四段45號" --json out/nearby.json && python3 ${CLAUDE_SKILL_DIR}/scripts/report.py out/nearby.json --html out/report.html
```

跑完會有兩個檔：`out/nearby.json`（結構化資料）與 `out/report.html`（可分享的報告）。
最後用 SendUserFile 把 `out/report.html` 交給使用者。

## 標準流程

1. **確認地址**。使用者只給「大安區」這種行政區也可以跑，但半徑篩選會失去意義 ——
   這時候直接用 `lvr.py` 查整個區，不要用 `nearby.py`。地址不完整就問一次。
2. **跑 `nearby.py`**。預設半徑 500 公尺、回溯 24 個月。使用者說「附近」通常指走路
   距離，300–500m 合適；郊區或案件稀少時放寬到 1000m。
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/nearby.py "<地址>" --radius 500 --months 24 --json out/nearby.json
   ```
   這一步會打十幾次網路請求（含 sleep），一個地址大約要 30–60 秒，是正常的。
   stderr 會印出每個階段的筆數；**半徑內筆數 < 20 就該放寬半徑或月數再跑一次**，
   否則中位數沒有代表性。
3. **產 HTML 報告**，然後用 SendUserFile 把 `out/report.html` 給使用者看。
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/report.py out/nearby.json --html out/report.html
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
| `scripts/relay.py` | 接力模式：沙箱沒網路時的 check / plan / build |
| `scripts/twcrypto.py` | 純 Python 的 CryptoJS 相容 AES（實登 API 要用） |

全部只用 Python 標準函式庫，**不需要 pip install 任何東西**。

單獨查一個行政區的實登（不做半徑篩選）：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/lvr.py --city 台北市 --town 大安區 --type biz --start 114/1 --end 115/7 --ftype 05
```

`--type` 可用 `biz`(買賣) / `rent`(租賃) / `sale`(預售屋) / `saleRemark`(預售建案備查)。
其他參數與代碼表見 `references/lvr-api.md`。

## 定位（geocode）設定

精度直接決定「半徑內」篩得準不準。provider 依序嘗試，**沒設定的會直接跳過、
不發請求也不產生費用**。想知道目前狀態就跑（不會發任何請求）：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/geocode.py --doctor
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
python3 ${CLAUDE_SKILL_DIR}/scripts/geocode.py "台北市大安區忠孝東路四段45號"
```

設定檔找的順序是 `$NEARPRICE_CONFIG` → `${CLAUDE_SKILL_DIR}/config.json` → `~/.config/nearprice/config.json`。
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

## 路徑 B：接力模式（沙箱沒有對外連線時）

關鍵事實：**實登的 QueryPrice 是無狀態 GET**（不驗證 token 或 session），而加密與
雜湊都是純運算。所以可以把工作拆成「沙箱裡算」與「你用網頁抓取工具去抓」兩半 ——
即使沙箱連不到內政部，**你自己的抓取工具連得到**。

### 步驟

**1. 取得鄉鎮代碼。** 無網路時查不到代碼表，用你的網頁抓取工具讀
`https://lvr.land.moi.gov.tw/SERVICE/CITY/{縣市代碼}`
（縣市代碼：A 臺北 F 新北 H 桃園 B 臺中 D 臺南 E 高雄 C 基隆 O 新竹市 J 新竹縣
K 苗栗 M 南投 N 彰化 P 雲林 I 嘉義市 Q 嘉義縣 T 屏東 G 宜蘭 U 花蓮 V 臺東
X 澎湖 W 金門 Z 連江）。

**2. 算出要抓的 URL**（不連網）：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/relay.py plan "<地址>" --months 24 --town-code A02
```

會印出三個 URL（買賣／租賃／預售屋）與對應的檔名。

**3. 用你的網頁抓取工具逐一抓那三個 URL**，把回應**原封不動**存成
`biz.json` / `rent.json` / `sale.json`。回應是 JSON 陣列，前後有多餘文字沒關係，
`build` 會自己切出來。

> 資料量會很大（一個行政區一年可能上千筆、數 MB）。**不要把整包 JSON 讀進 context**，
> 直接寫成檔案交給下一步。真的太大就縮小 `--months`，或加 `--ftype 05` 只看住宅大樓。

**4. 合併＋統計**（不連網，輸出格式跟路徑 A 完全一樣）：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/relay.py build "<地址>" \
  --biz biz.json --rent rent.json --sale sale.json \
  --radius 500 --months 24 --json out/nearby.json
python3 ${CLAUDE_SKILL_DIR}/scripts/report.py out/nearby.json --html out/report.html
```

圓心是從抓回來的紀錄裡挑門牌最接近的一筆推出來的，不需要另外 geocode。

**5. 報告**：能傳檔案就傳 `out/report.html`；不能的話讀進來當 artifact 呈現。
講重點的要求跟路徑 A 一樣。

### 路徑 B 的限制，要主動告訴使用者

- **定位是估算的**（同路段最近成交案件反推），長路段誤差可達 1 公里以上。
- **沒有房仲開價**，所以看不到議價空間。需要的話用網頁抓取工具讀 591／信義／永慶的
  搜尋頁，整理成 `references/listings.md` 裡的 schema，補進 `nearby.json` 的
  `市場開價.資料` 後重跑 `report.py`。

## 參考文件

- `references/lvr-api.md` —— 內政部實登 API 的完整規格：加密方式、參數、代碼表、回應欄位
- `references/listings.md` —— 各房仲網站的網址規則、解析方式、實測可靠度
- `references/geocoding.md` —— geocode provider 設定，含自架 TGOS 服務的 config 範例
