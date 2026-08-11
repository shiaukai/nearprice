# nearprice — 台灣不動產行情查詢 skill

給一個地址，一次拿到三種價格：

- **已成交** — 內政部實價登錄（買賣 / 租賃 / 預售屋），即時查官網 API
- **現在賣方要價** — 永慶、信義、591 售屋
- **現在房東要價** — 591 租屋、信義租屋

再算出中位數、P25–P75 區間、季度趨勢、依建物型態與屋齡的分布、推估租金報酬率，
產出一份自足的 HTML 報告。

這是一個 [Claude Code](https://claude.com/claude-code) skill，也可以純當 CLI 工具用。

---

## 安裝

**不需要 pip install 任何東西。** 只用 Python 3 標準函式庫，連 AES 都是自帶的純
Python 實作（macOS 內建的 python3 是 externally-managed，不該為了查房價先弄一個 venv）。

### 給 AI 裝（最省事）

把下面整段貼給 Claude Code：

```
幫我安裝這個 skill：https://github.com/shiaukai/nearprice
照它 README 的「手動安裝」做，裝完跑 --doctor 確認，然後告訴我要不要設金鑰。
```

### 手動安裝

repo 的根目錄**就是** skill 本體，所以 clone 到 skills 目錄即可，不用搬檔案：

```bash
# 全域安裝（任何專案都能用）
git clone https://github.com/shiaukai/nearprice.git ~/.claude/skills/nearprice

# 或只給某個專案用
git clone https://github.com/shiaukai/nearprice.git <你的專案>/.claude/skills/nearprice
```

建立設定檔：

```bash
cd ~/.claude/skills/nearprice
cp config.example.json config.json
cp .env.example .env
python3 scripts/geocode.py --doctor
```

**新增 skill 後要開新的 Claude Code session 才會被掃到。**

更新：

```bash
cd ~/.claude/skills/nearprice && git pull
```

`config.json` 與 `.env` 都在 `.gitignore` 裡，`git pull` 不會蓋掉你的設定。

---

## 設定金鑰（`.env`）

**不填金鑰也能跑**，會自動用免費的 `lvr` 備援定位（拿同一條路上的實登成交案件座標
反推）。缺點是長路段會失準 —— 實測台中「台灣大道三段」差了 1876 公尺，圓心跑掉
整份半徑統計就沒有意義。要準確就設一個真正的定位服務。

`.env` 長這樣（從 `.env.example` 複製）：

```bash
# Google Geocoding API —— 推薦的主力定位服務
GOOGLE_MAPS_API_KEY=你的金鑰

# 自架的 Locus Geo API —— 沒有這個服務就留空，provider 會自動跳過
LOCUS_API_KEY=
```

取得 Google 金鑰：Google Cloud Console 建專案 → **開啟帳單** → 啟用 **Geocoding API**
（只要這一個，不需要 Maps JavaScript 或 Places）→ 建 API key。
建議在 key 上加「API 限制」只允許 Geocoding API。一個地址只打 1 次。

金鑰查找順序：**環境變數 → `.env` → `config.json`**。

> ⚠️ **寫在 `~/.zshrc` 不會生效。** 腳本跑在非互動 shell，zsh 只 source `~/.zshenv`
> 不 source `~/.zshrc`。你手動跑得起來、Claude 自動跑卻 401，然後**靜默退到備援
> provider** —— 很難察覺。症狀是報告的定位欄顯示 `lvr` 而不是你設定的那個。
>
> 要用環境變數就放 `~/.zshenv`，不然就用 `.env`（也可放 `~/.config/nearprice/.env`）。

隨時用 `--doctor` 確認目前狀態，它**不會發出任何網路請求**：

```
$ python3 scripts/geocode.py --doctor
嘗試順序   : google → lvr → nominatim

  ✓ google     可用   金鑰=.env 的 GOOGLE_MAPS_API_KEY
  ✓ lvr        可用   零設定備援（拿同路段實登成交案件反推）
  ✓ nominatim  可用   只到路段層級，誤差可達數百公尺

實際會用到：google（失敗時往後退）
```

---

## 用法

### 在 Claude Code 裡

skill 本身不執行任何東西，它是一份 Markdown 說明 + 一包腳本；跑 Python 的是 Claude，
它讀 `SKILL.md` 之後用 Bash 執行 `scripts/` 底下的腳本。

**兩種呼叫方式**——講人話讓 Claude 自己判斷，或用斜線指令直接叫：

```
幫我查 台北市大安區忠孝東路四段45號 附近的行情
```
```
/nearprice 台北市大安區忠孝東路四段45號
/nearprice 新北市板橋區文化路一段266號 --radius 800 --months 36
```

斜線指令是 skill 自動產生的（Claude Code 已把 custom commands 併進 skills，
`SKILL.md` 的 `name` 就是指令名），不需要另外寫 `.claude/commands/` 檔案。

`SKILL.md` 的 `allowed-tools` 有預先授權這些腳本，所以跑起來不會一直跳權限確認。

### 能裝在 claude.ai / Cowork 嗎？

技術上可以，但**預設會跑不動**——這個 skill 的每一項功能都要打外網，而 claude.ai
的程式碼執行沙箱有網域白名單，預設只放行套件庫（npm / PyPI）、GitHub、Ubuntu 與
Anthropic 自家服務，不含本工具需要的任何一個網域。

| 介面 | 能不能裝 | 網路 |
|---|---|---|
| **Claude Code** | ✅ 直接 clone | 完整網路，跟你電腦上任何程式一樣 |
| **claude.ai** | ⚠️ Settings → Features 上傳 zip（Pro/Max/Team/Enterprise 且已開 code execution） | 依帳號／管理員設定而定，**預設擋掉** |
| **Cowork / cloud session** | ⚠️ 讀的是 claude.ai 啟用的 skill，不讀 `~/.claude/skills/` | 同上 |
| **Claude API** | ❌ | 完全無網路，這個 skill 不可能運作 |

要在 claude.ai／Cowork 用，得把下列網域加進白名單（或開「完整網路存取」）：

```
lvr.land.moi.gov.tw          內政部實價登錄（必要）
maps.googleapis.com          Google 定位
nominatim.openstreetmap.org  備援定位
sale.591.com.tw  rent.591.com.tw  www.sinyi.com.tw  buy.yungching.com.tw
```

另外注意兩點：

- **skill 不會跨介面同步。** Claude Code 的 skill 是檔案系統上的，跟 claude.ai
  和 API 各自獨立，要用就得分別上傳。
- `${CLAUDE_SKILL_DIR}` 是 Claude Code 專屬的代換，在 claude.ai 會是字面文字。
  `SKILL.md` 已經寫了 fallback 指示，但體驗不如 Claude Code。

#### 沙箱沒網路時的變通做法

要注意「Claude 自己的瀏覽工具」和「程式碼沙箱」是兩套不同的網路 —— 前者連得到
內政部，後者預設連不到。而實登的查詢請求是**無狀態的 GET**，加密與雜湊都是純運算，
所以可以拆成兩半：

```bash
# 這一步不需要網路，在沙箱裡算就好
python3 scripts/lvr.py --city A --town A02 --type biz --start 115/1 --end 115/3 --ftype 05 --print-url
```

把印出來的 URL 交給有網路的一方（chat 裡就是 Claude 的網頁抓取工具）去抓，
回來的就是完整 JSON。定位改用 `lvr` 備援（同樣是這條路），房仲開價則直接讓 Claude
去讀各站的搜尋頁。這樣在 chat 裡也能得到結果，只是要多幾步、且資料量大時會吃 context。

本專案的 frontmatter 只用 Agent Skills spec 允許的欄位，所以打包上傳不會因為
未知欄位而報錯。要打包成可上傳的 zip：

```bash
./scripts/package.sh          # 產生 nearprice-skill.zip（不含金鑰）
```

### 純 CLI

```bash
mkdir -p out
python3 scripts/nearby.py "台北市大安區忠孝東路四段45號" \
  --radius 500 --months 24 --json out/nearby.json
python3 scripts/report.py out/nearby.json --html out/report.html
open out/report.html
```

一個地址大約 30–60 秒（十幾次網路請求，中間有禮貌性的 sleep）。

其他腳本也都能單獨用：

```bash
# 查整個行政區的買賣實登（住宅大樓）
python3 scripts/lvr.py --city 台北市 --town 大安區 --type biz --start 114/1 --end 115/7 --ftype 05

# 列出縣市 / 鄉鎮代碼
python3 scripts/lvr.py --list-towns 台北市

# 只抓某一站的開價
python3 scripts/listings.py --city 台北市 --town 大安區 --site yungching_buy

# 測地址定位
python3 scripts/geocode.py "台北市大安區忠孝東路四段45號"
```

---

## 為什麼不用官方的批次 CSV

內政部有提供免費的批次開放資料，但**沒有經緯度**，只有門牌字串。要做「這個地址半徑
500 公尺內」的查詢，就得自己把幾十萬筆門牌一筆筆 geocode。

這個專案改走官網的即時查詢 API —— 它**每一筆都回傳 lat/lon**，半徑搜尋因此才做得成。
API 的查詢條件是用 CryptoJS AES 加密塞在 query string 裡，passphrase 就是
`window.location.host`。完整的逆向規格（加密流程、參數順序、代碼表、回應欄位、
官網改版時的排查步驟）寫在 [`references/lvr-api.md`](references/lvr-api.md)。

## 定位 provider

依序嘗試，**沒設定的直接跳過，不發請求也不產生費用**。

| provider | 精度 | 費用 | 說明 |
|---|---|---|---|
| `google` | 門牌（ROOFTOP） | 付費，有免費額度 | **推薦主力**。只需啟用 Geocoding API |
| `locus` | 門牌 | 自架 | 作者自架的服務，沒有的人會被跳過 |
| `custom` | 看服務 | 看服務 | 任何其他自架服務，用設定檔描述即可，不用改程式 |
| `tgos` | 門牌 | 免費但限資格 | 官方 TGOS，限政府／法人／學術／業界申請 |
| `lvr` | 門牌附近 | **免費、零設定** | 保底方案，長路段會失準 |
| `nominatim` | 路段 | 免費 | 最後手段，誤差數百公尺 |

設定細節見 [`references/geocoding.md`](references/geocoding.md)。

## 專案結構

```
nearprice/                      ← clone 到 ~/.claude/skills/nearprice
├── SKILL.md                    Claude 讀的流程說明
├── config.example.json         複製成 config.json
├── .env.example                複製成 .env 填金鑰
├── references/
│   ├── lvr-api.md              內政部實登 API 逆向規格
│   ├── listings.md             各房仲網站的網址規則與實測可靠度
│   └── geocoding.md            geocode provider 設定
└── scripts/
    ├── nearby.py               主入口：地址 → 附近行情 JSON
    ├── report.py               JSON → HTML 報告
    ├── lvr.py                  內政部實登即時查詢 client
    ├── geocode.py              地址 → 座標（可插拔 provider）
    ├── listings.py             房仲網站開價抓取
    └── twcrypto.py             CryptoJS 相容 AES（純 stdlib）
```

## 已知限制

- 實登有申報落差（通常 1–2 個月），最新一兩個月資料偏少。
- 「單價」是總價 ÷ 總面積；含車位的案件單價會被拉低、純車位交易會出現極低單價。
  一律看**中位數與 P25–P75**，不要看平均。
- 信義買屋的分區查詢需要它自家 API 的 token，目前沒接，指定行政區時常常回 0 筆
  （細節見 `references/listings.md`）。591 售屋有 bot 防護。樂屋網／樂居／住商回 403。
  這些被擋時改用瀏覽器抓，schema 在同一份文件裡。
- 租金報酬率是中位數推估的粗估值，沒扣稅費與空置期，**不是投資建議**。
- 內政部官網改版就可能失效，排查步驟寫在 `references/lvr-api.md` 末尾。

## 使用須知

資料來源皆為公開資訊。抓取端已內建 1–2 秒的請求間隔，請不要拿掉，也不要把
`--pages` 開很大。本工具供個人查價參考，不構成投資建議。

各資料來源有各自的服務條款，實際使用前請自行確認；本專案的授權不涵蓋這些第三方資料。

## 授權

[MIT](LICENSE)
