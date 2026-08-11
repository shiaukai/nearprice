# tw-realprice — 台灣不動產行情查詢 skill

給一個地址，一次拿到三種價格：

- **已成交** — 內政部實價登錄（買賣 / 租賃 / 預售屋），即時查官網 API
- **現在賣方要價** — 永慶、信義、591 售屋
- **現在房東要價** — 591 租屋、信義租屋

再算出中位數、P25–P75 區間、季度趨勢、依建物型態與屋齡的分布、推估租金報酬率，
產出一份自足的 HTML 報告。

這是一個 [Claude Code](https://claude.com/claude-code) skill，也可以純當 CLI 工具用。

## 為什麼不用官方的批次 CSV

內政部有提供免費的批次開放資料，但**沒有經緯度**，只有門牌字串。要做「這個地址半徑
500 公尺內」的查詢，就得自己把幾十萬筆門牌一筆筆 geocode。

這個專案改走官網的即時查詢 API —— 它**每一筆都回傳 lat/lon**，半徑搜尋因此才做得成。
API 的查詢條件是用 CryptoJS AES 加密塞在 query string 裡，passphrase 就是
`window.location.host`。完整的逆向規格（加密流程、參數順序、代碼表、回應欄位、
官網改版時的排查步驟）寫在 [`references/lvr-api.md`](.claude/skills/tw-realprice/references/lvr-api.md)。

## 安裝

**不需要 pip install 任何東西** —— 只用 Python 3 標準函式庫，連 AES 都是自帶的純
Python 實作（macOS 內建的 python3 是 externally-managed，不該為了查房價先弄一個 venv）。

```bash
git clone <this-repo> && cd <this-repo>
cp .claude/skills/tw-realprice/config.example.json .claude/skills/tw-realprice/config.json
cp .claude/skills/tw-realprice/.env.example       .claude/skills/tw-realprice/.env
```

編輯 `.env` 填入 `GOOGLE_MAPS_API_KEY`，然後確認設定：

```bash
python3 .claude/skills/tw-realprice/scripts/geocode.py --doctor
```

`--doctor` 不會發出任何網路請求，只列出每個 provider 的狀態。

> **不填金鑰也能跑。** 沒有任何金鑰時會自動用免費的 `lvr` 備援定位（拿同一條路上的
> 實登成交案件座標反推）。缺點是長路段會失準 —— 實測台中「台灣大道三段」差了
> 1876 公尺，圓心跑掉整份半徑統計就沒有意義。正式使用請設一個真正的定位服務。

## 用法

### 在 Claude Code 裡

skill 本身不執行任何東西，它是一份 Markdown 說明 + 一包腳本；跑 Python 的是 Claude，
它讀 `SKILL.md` 之後用 Bash 執行 `scripts/` 底下的腳本。

```
幫我查 台北市大安區忠孝東路四段45號 附近的行情
```

或直接 `/tw-realprice <地址>`。新增 skill 後要開新的 session 才會被掃到。

想在任何專案都能用，就 symlink 到全域（`.env` 會跟著 symlink 走）：

```bash
mkdir -p ~/.claude/skills && ln -s "$PWD/.claude/skills/tw-realprice" ~/.claude/skills/tw-realprice
```

### 純 CLI

```bash
mkdir -p out
python3 .claude/skills/tw-realprice/scripts/nearby.py "台北市大安區忠孝東路四段45號" \
  --radius 500 --months 24 --json out/nearby.json
python3 .claude/skills/tw-realprice/scripts/report.py out/nearby.json --html out/report.html
open out/report.html
```

一個地址大約 30–60 秒（十幾次網路請求，中間有禮貌性的 sleep）。

其他腳本也都能單獨用：

```bash
# 查整個行政區的買賣實登（住宅大樓）
python3 .../scripts/lvr.py --city 台北市 --town 大安區 --type biz --start 114/1 --end 115/7 --ftype 05

# 列出縣市 / 鄉鎮代碼
python3 .../scripts/lvr.py --list-towns 台北市

# 只抓某一站的開價
python3 .../scripts/listings.py --city 台北市 --town 大安區 --site yungching_buy

# 測地址定位
python3 .../scripts/geocode.py "台北市大安區忠孝東路四段45號"
```

## 定位 provider

依序嘗試，**沒設定的直接跳過，不發請求也不產生費用**。

| provider | 精度 | 費用 | 說明 |
|---|---|---|---|
| `google` | 門牌（ROOFTOP） | 付費，有免費額度 | **推薦主力**。只需啟用 Geocoding API，一個地址打 1 次 |
| `locus` | 門牌 | 自架 | 作者自架的服務，沒有的人會被跳過 |
| `custom` | 看服務 | 看服務 | 任何其他自架服務，用設定檔描述即可，不用改程式 |
| `tgos` | 門牌 | 免費但限資格 | 官方 TGOS，限政府／法人／學術／業界申請 |
| `lvr` | 門牌附近 | **免費、零設定** | 保底方案，長路段會失準 |
| `nominatim` | 路段 | 免費 | 最後手段，誤差數百公尺 |

設定細節見 [`references/geocoding.md`](.claude/skills/tw-realprice/references/geocoding.md)。

## 金鑰放哪

查找順序：**環境變數 → `.env` → `config.json`**。

> ⚠️ **放 `~/.zshrc` 不會生效。** 腳本跑在非互動 shell，zsh 只 source `~/.zshenv`。
> 你手動跑得起來、Claude 自動跑卻 401，然後**靜默退到備援 provider** —— 很難察覺。
> 症狀是報告的定位欄顯示 `lvr` 而不是你設定的那個。用 `--doctor` 確認。

`.env`、`config.json` 都已列入 `.gitignore`。

## 專案結構

```
.claude/skills/tw-realprice/
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
