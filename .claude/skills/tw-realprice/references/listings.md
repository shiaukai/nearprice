# 房仲網站現售 / 出租開價

實價登錄是「已成交」；這裡補的是「現在的要價」。兩者的差距≈議價空間。
以下是 2026-08 實測結果。

## 實測可靠度

| provider | 站 | 方式 | 可靠度 | 備註 |
|---|---|---|---|---|
| `yungching_buy` | 永慶買屋 | Angular SSR，class 命名清楚 | ★★★ | 目前最穩，欄位齊全 |
| `rent591` | 591 租屋 | Nuxt SSR HTML 卡片 | ★★ | 卡片可解析；`img alt` 內含「月租 xx,xxx 元/月」 |
| `sinyi_rent` | 信義租屋 | 舊版 HTML 模板 | ★★ | 可解析，但網址上的行政區不生效（見下） |
| `sinyi_buy` | 信義買屋 | `__NEXT_DATA__` JSON | ★ | 欄位最乾淨且**有經緯度**，但 SSR 給的是全市快取，不吃行政區 |
| `sale591` | 591 售屋 | HTML | ★ | 有 bot 防護，連續請求會拿到空頁 |

`listings.py` 的 `collect()` 單站失敗不影響其他站，失敗原因會收進回傳的 errors。

## 各站網址規則

### 591

```
售屋  https://sale.591.com.tw/?regionid={id}&firstRow={(page-1)*30}
租屋  https://rent.591.com.tw/list?region={id}&firstRow={(page-1)*30}
```

`regionid`：1 臺北 3 新北 6 桃園 8 臺中 15 臺南 17 高雄 2 基隆 4 新竹市 5 新竹縣
7 苗栗 10 彰化 11 南投 12 嘉義市 13 嘉義縣 14 雲林 19 屏東 21 宜蘭 22 臺東
23 花蓮 24 澎湖 25 金門 26 連江（完整表在 `listings.py` 的 `CITY_591`）

591 的**內部 JSON API 已改成 AES 加密**，只能走 HTML。售屋站的 bot 防護較嚴，
連打幾次就開始回幾百 bytes 的空頁 —— 這時改用瀏覽器工具。

卡片結構：`<div class="item" data-id="21784238">`，注意 class 要**完全等於** `item`，
用 `\bitem\b` 會誤中 `item-img`、`tag-item`。

### 信義房屋

```
買屋  https://www.sinyi.com.tw/buy/list/{City-slug}/{District-slug}/default-desc/{page}
租屋  https://www.sinyi.com.tw/rent/list/{City-slug}/{District-slug}/default-desc/{page}
```

行政區 slug 是**連字號拼音**，大安區是 `Da-an-district` 不是 `Daan-district`。

**已知限制**：不論網址帶不帶行政區，SSR 進 `__NEXT_DATA__` 的
`props.initialReduxState.buyReducer.list` 都是**全市的快取**（`totalCnt` 恆為全市數）。
真正的分區查詢走：

```
POST https://sinyiwebapi.sinyi.com.tw/filterObject.php
body: {"filter":{...,"county":"Taipei-city","section":["Da-an-district"]},
       "page":1,"pageCnt":20,"sort":"0","isReturnTotal":true}
```

但它要一組認證 token（沒帶會回 `{"retCode":"308","retMsg":"認證Token錯誤"}`），
token 由同域的 `updateUserToken.php` / `getCommonData.php` 發。目前沒接。
所以 `listings.py` 的做法是：抓全市 SSR list，再用地址 + 經緯度在本地篩。
**信義買屋因此常常在指定行政區下回 0 筆，這是預期行為，不是壞掉。**

信義租屋是另一套舊模板，卡片是 `class="search_result_item"`：
- 連結 `href="houseno/C366801"` → `https://www.sinyi.com.tw/rent/houseno/C366801`
- 租金 `class="price_new"><span class="num">49,800</span>元/月`
- 地址 `class="num num-text">` 內容可能是「社區 / 地址」

### 永慶房屋

```
買屋  https://buy.yungching.com.tw/region/{縣市}-{行政區}_c/?pg={page}
```

**網址吃的是「台北市」不是「臺北市」**，用正體「臺」會 404。中文要 URL-encode。

卡片 `<li class="search-result-list-item">`，欄位 class 很好認：
`caseName` 標題 · `address` 地址 · `community` 社區 · `caseType` 型態 ·
`regArea` 建坪 · `mainArea` 主+陽 · `floor` 樓層 · `room` 格局 ·
`price` 總價（萬元） · `href="house/{id}"`
屋齡在一個沒有 class 的 `<span>` 裡，形如 `48.7年`。

### 樂屋網 / 樂居 / 住商

實測直接 HTTP 會拿到 403 或 404（bot 防護 / 路徑不同），**沒有實作**。
要納入就走瀏覽器路徑（下一節）。

## 瀏覽器路徑（被擋時用這個）

用 `mcp__Claude_Browser__navigate` 開搜尋頁，`get_page_text` 或 `read_page` 取內容，
自己整理成下面的 schema 存成 JSON，再併進 `nearby.py` 產出的
`out/nearby.json` 的 `市場開價.資料` 陣列，重跑 `report.py` 即可。

```json
{
  "來源": "樂屋網", "類型": "售",
  "標題": "", "地址": "", "社區": "",
  "總價萬元": 2680, "月租金元": null, "單價萬元每坪": 98.5,
  "坪數": 27.2, "格局": "3房2廳2衛", "樓層": "8/14", "屋齡": 12,
  "型態": "電梯大樓", "連結": "https://...", "lat": null, "lon": null
}
```

`類型` 只有 `售` / `租` 兩種值。金額單位務必照欄位名：`總價萬元` 是萬元、
`月租金元` 是元。有 `lat`/`lon` 的話 `nearby.py` 會順便算距離並套半徑篩選。

## 抓取禮儀

- `listings.py` 每個請求之間已經 sleep 1–2 秒，**不要拿掉**。
- 不要為了湊資料把 `--pages` 開很大；一般行情判斷 1–2 頁就夠。
- 這些是公開列表頁的公開資訊，僅供個人查價；不要拿來大量轉存或再散布。
