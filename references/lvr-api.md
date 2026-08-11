# 內政部實價登錄查詢 API（逆向規格）

對象：`https://lvr.land.moi.gov.tw`（不動產交易實價查詢服務網）。
以下是從官網前端 JS bundle 逆向出來的規格，2026-08 實測可用。

## 加密方式

查詢條件是一個 JSON，用 CryptoJS 的 AES **passphrase 模式** 加密後放進 query string：

```
GET /SERVICE/QueryPrice/{md5(json)}?q={base64(ciphertext)}
```

- **passphrase = `window.location.host` = 字串 `"lvr.land.moi.gov.tw"`**
  （在 `common.bundle.js` 裡是 `var g = window.location.host`）
- CryptoJS passphrase 模式 = OpenSSL 相容格式：
  `salt(8B random)` → `EVP_BytesToKey(passphrase, salt, MD5, 1 round)` → 32B key + 16B iv
  → AES-256-CBC + PKCS#7 → `base64("Salted__" + salt + ct)`
- **`q` 是把上面那串 base64 字串「再 base64 一次」**
  （前端是 `Base64.stringify(Utf8.parse(ciphertext))`）
- **URL path 是同一份 JSON 字串的 MD5 hex**，所以 JSON 的**鍵順序不能變**
  （前端用 `$.extend(defaults, dataObj)`，順序 = defaults 的鍵 + dataObj 新增的鍵）
- JSON 用 `JSON.stringify` 產生：無空白、非 ASCII 不轉義
  → Python 要用 `json.dumps(payload, ensure_ascii=False, separators=(",", ":"))`

`scripts/twcrypto.py` 是純 stdlib 的相容實作，已跟瀏覽器的 CryptoJS 對答案驗證過。

## 呼叫順序

1. `GET /` —— 拿 `JSESSIONID` cookie
2. `GET /jsp/setToken.jsp` → `{"token":"RLS6462861443"}`，token 要放進 payload
   （回 `"401"` 代表 session 失效，重新來一次）
3. `GET /SERVICE/QueryPrice/{md5}?q={q}` → JSON array

同一個 session 可以重複查，但要保留 cookie。

> 註：前端有 `if (window.navigator.webdriver) return;` 擋自動化瀏覽器；
> 直接走 HTTP 不受影響。

## 參考資料端點（不用加密）

| 端點 | 回傳 |
|---|---|
| `GET /SERVICE/CITY` | 全部縣市 `[{code:"A", title:"臺北市", use:true}, …]` |
| `GET /SERVICE/CITY/{cityCode}` | 該縣市的鄉鎮市區 `[{code:"A02", officecode:"AF", title:"大安區"}, …]` |

縣市代碼：A 臺北 F 新北 H 桃園 B 臺中 D 臺南 E 高雄 C 基隆 O 新竹市 J 新竹縣
K 苗栗 M 南投 N 彰化 P 雲林 I 嘉義市 Q 嘉義縣 T 屏東 G 宜蘭 U 花蓮 V 臺東
X 澎湖 W 金門 Z 連江

## 查詢參數

鍵順序（**必須照這個順序**，否則 md5 對不上）：

```
ptype, starty, startm, endy, endm,
qryType, city, town, p_build,
ftype, price_s, price_e, unit_price_s, unit_price_e, area_s, area_e,
build_s, build_e, buildyear_s, buildyear_e, doorno, pattern, community, floor,
rent_type, rent_order, urban, urbantext, nurban, aa12, p_purpose,
p_unusual_yn, p_unusualcode, QB41, show_avg, tmoney_unit, pmoney_unit, unit,
token
```

| 參數 | 說明 |
|---|---|
| `qryType` | `biz` 不動產買賣 / `rent` 不動產租賃 / `sale` 預售屋買賣 / `saleRemark` 預售屋建案備查 |
| `city` / `town` | 代碼，如 `A` / `A02` |
| `starty`/`startm`/`endy`/`endm` | 民國年、月，皆為字串且月份**不補零**（`"7"` 不是 `"07"`） |
| `ptype` | 交易標的。買賣/預售預設 `1,2,3,4,5`；**租賃必須用 `1,2,4,6,7`**（官網 `#rent_ptype` 的預設值），用買賣的預設會查回空陣列 |
| `ftype` | 建物型態，逗號分隔 |
| `doorno` | 門牌關鍵字（路名），**要先 URL-encode 再放進 JSON** |
| `community` / `p_build` | 社區/建案名稱，同樣先 URL-encode |
| `p_purpose` | 主要用途，先 URL-encode |
| `unit` | `1`=㎡ `2`=坪 |
| `tmoney_unit` / `pmoney_unit` | `1`=萬元 `2`=元 |
| `show_avg` | `Y`/`N`，是否要平均值列 |
| `p_unusual_yn` | 是否排除特殊交易 |

### ptype（交易標的）

`1` 房地 · `2` 房地(含車位) · `3` 土地 · `4` 建物 · `5` 車位

### ftype（建物型態）

買賣：`01` 公寓 `02` 透天厝 `03` 店面(店鋪) `04` 辦公大樓 `05` 住宅大樓
`06` 華廈 `07` 套房 `08` 工廠 `09` 廠辦 `10` 農舍 `11` 倉庫

租賃另有：`01` 公寓(無電梯) `04` 商辦大樓 `12` 其他 `L` 土地 `P` 車位

### rent_type（出租型態）

`1` 整棟(戶)出租 · `2` 分層出租 · `3` 獨立套房 · `4` 分租套房 · `5` 分租雅房

### rent_order（租賃附加條件）

`01` 含車位 · `02` 電梯 · `03` 附屬設備 · `04` 管理員 · `05` 管理組織 · `06` 包租代管服務

### p_purpose（主要用途）

住家用 / 商業用 / 工業用 / 農業用 / 辦公用 / 住商用 / 住工用 / 住辦用 /
工商用 / 商辦用 / 住商辦用 / 工商辦用 / 其他

## 回應欄位

回傳是 array，每筆是單字母鍵。**每一筆都有 `lat` / `lon`**（WGS84），
這是這個 API 最有價值的地方 —— 官方免費批次 CSV（plvr.land.moi.gov.tw）**沒有座標**，
所以半徑搜尋只能靠這個即時 API，不然就得自己 geocode 每一筆成交案件。

| 鍵 | 意義 |
|---|---|
| `a` | 地址。買賣是「補零版#正常版」兩段用 `#` 分隔；租賃/預售只有一段 |
| `b` | 建物型態（完整敘述，如「住宅大樓(11層含以上有電梯)」） |
| `bn` | 社區 / 建案名稱 |
| `bs` | 主建物佔比（如 `56.65%`） |
| `bu` | 棟及號（預售屋） |
| `e` | 交易 / 租賃日期，民國 `115/07/03` |
| `tp` | 總價 / 總租金（元，含千分位逗號） |
| `p` | 單價（`unit=2` 時為元/坪；等於總價÷總面積，`msg` 欄會說明算法） |
| `cp` | 車位總價（萬元） |
| `s` | 面積（坪） |
| `t` | 交易標的（房地(土地+建物) / 租賃房屋 / 車位 …） |
| `f` | 樓層 / 總樓層 |
| `g` | 屋齡（年） |
| `v` | **格局**，如 `3房2廳2衛` |
| `j` / `k` / `l` | 交易筆棟數：土地筆數 / 建物棟數 / 車位個數。**不是房廳衛** —— 很容易看成 2/1/0 就當作 2房1廳，實際上格局在 `v` |
| `pu` | 主要用途 · `ma` 主要建材 · `AA11` 都市土地使用分區 |
| `el` 電梯 · `m` 管理組織 · `ms` 管理員 · `fn` 附屬設備 | |
| `rperiod` | 租期 `1150621~1200620` · `rtype` 出租型態 |
| `note` | 備註（含裝潢費、毛胚屋、親友交易…） |
| `lat` / `lon` | 座標 |
| `sq` | 明細查詢用的加密 id · `commid` 社區 id · `reid` 預售備查編號 |

## 其他已知端點

從 bundle 裡撈到、尚未逐一驗證的：

```
/SERVICE/QueryPrice/detail/     單筆明細（帶 sq）
/SERVICE/QueryPrice/history/    同標的歷史交易
/SERVICE/QueryPrice/community/  社區交易列表（帶 commid）
/SERVICE/QueryPrice/SaleList/   /SaleData/  /SaleBuild/   預售建案相關
/SERVICE/QueryPrice/Excel/      匯出
/SERVICE/QueryPrice/3dmap/      地圖模式
/SERVICE/QueryPrice/RetHert/    /SrmDetail/  /paydetail/  /PrintData/
/SERVICE/StatPrice/             統計
```

要用的話，加密與 token 流程一樣，只是 payload 欄位不同 —— 用瀏覽器開官網、
在 console 裡看 `String(window.loadQueryPrice)` 之類的全域函式就能讀到參數組法。

## 壞掉的時候怎麼修

症狀與對策：

- **回 HTTP 500 或 HTML** → payload 欄位名或順序不對。開官網 console 執行
  `String(window.loadQueryPrice)` 對一次 `dataObj` 的欄位。
- **回空陣列但官網查得到** → 多半是 `ptype` 給錯（租賃特別容易）。
  檢查 `document.getElementById('rent_ptype').value`。
- **token 一直 401** → 先 `GET /` 拿 cookie 再打 `setToken.jsp`，且兩者要同一個 session。
- **解密相關全壞** → passphrase 可能不再是 host。在 console 裡跑：
  ```js
  window.webpackJsonp_name_.push([[9999],{"__p__":function(m,e,req){window.__req=req}},[["__p__"]]]);
  // 然後 __req(247) 是 AES、__req(212) 是 MD5、__req(211) 是 Base64、__req(332) 是 Utf8
  ```
  module 編號會隨改版變動，用 `Object.keys(M)` 認特徵：
  `{encrypt,decrypt}` 是 AES、有 `_map` 的是 Base64。

## 另一條路：官方批次開放資料

若只要整批歷史資料、不需要即時或座標：

```bash
curl -o lvr.zip "https://plvr.land.moi.gov.tw/DownloadSeason?season=114S1&type=zip&fileName=lvr_landcsv.zip"
```

解出來是每縣市三組 CSV：`{縣市代碼}_lvr_land_a.csv`（買賣）、`_b`（預售）、`_c`（租賃），
外加 `_build` / `_land` / `_park` 明細。**免費版沒有經緯度**，只有完整門牌。
`season` 格式 `114S1`（民國年 + 季）。
