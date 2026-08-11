#!/usr/bin/env python3
"""無網路環境用的接力模式（claude.ai / Cowork / CI）。

問題：claude.ai 的程式碼沙箱有網域白名單，連不到 lvr.land.moi.gov.tw；
但 Claude **自己的**網頁抓取工具連得到。兩者是不同的網路路徑。

解法：把工作拆成「純運算」與「純抓取」兩半 ——

    1. plan   在沙箱裡算出要抓哪些 URL（加密與雜湊都不需要網路）
    2. （由具備網路的一方去抓，把回應各自存成 .json 檔）
    3. build  把抓回來的 JSON 併成跟 nearby.py 一模一樣的輸出

實登的 QueryPrice 是無狀態 GET，不驗證 token 或 session，所以這條路走得通。
圓心也不必另外 geocode —— 抓回來的紀錄本身就帶 lat/lon，從中挑門牌最接近的一筆即可。

用法：
    python3 relay.py check
    python3 relay.py plan "台北市大安區忠孝東路四段45號" --months 24
    python3 relay.py build "台北市大安區忠孝東路四段45號" \
        --biz biz.json --rent rent.json --sale sale.json --radius 500 --json out/nearby.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from geocode import center_from_lvr_rows, haversine, parse_address  # noqa: E402
from lvr import BIZ, PRESALE, RENT, LVRClient, normalize, tw_gov_ssl_context  # noqa: E402
from nearby import _write, months_ago, rent_yield, roc_ym, summarize  # noqa: E402

KINDS = [(BIZ, "買賣", "biz"), (RENT, "租賃", "rent"), (PRESALE, "預售屋", "sale")]

# 縣市 / 鄉鎮代碼表要靠 /SERVICE/CITY 查，沒網路時查不到。
# 這裡內建縣市代碼（22 筆，很少變動）；鄉鎮代碼仍需查表，所以 plan 也接受直接給代碼。
CITY_CODES = {
    "基隆市": "C", "臺北市": "A", "新北市": "F", "桃園市": "H", "新竹市": "O",
    "新竹縣": "J", "苗栗縣": "K", "臺中市": "B", "南投縣": "M", "彰化縣": "N",
    "雲林縣": "P", "嘉義市": "I", "嘉義縣": "Q", "臺南市": "D", "高雄市": "E",
    "屏東縣": "T", "宜蘭縣": "G", "花蓮縣": "U", "臺東縣": "V", "澎湖縣": "X",
    "金門縣": "W", "連江縣": "Z",
}


def _norm(s: str) -> str:
    return (s or "").replace("台", "臺").strip()


def check() -> int:
    """測沙箱能不能直接連到內政部，決定要走哪一條路徑。"""
    url = "https://lvr.land.moi.gov.tw/SERVICE/CITY"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nearprice/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=tw_gov_ssl_context()) as r:
            n = len(json.loads(r.read().decode("utf-8")))
        print(f"✓ 有對外連線（{url} 回 {n} 個縣市）")
        print("  → 直接用 nearby.py，不需要接力模式。")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 連不到 {url}")
        print(f"  {type(exc).__name__}: {str(exc)[:120]}")
        print("  → 走接力模式：relay.py plan → 由外部抓取 → relay.py build")
        return 1


def plan(address: str, months: int, town_code: str, ftype: str) -> int:
    """印出需要抓取的 URL 清單。這一步完全不連網。"""
    p = parse_address(address)
    if not p["city"]:
        print(f"錯誤：地址解析不出縣市 —— {address}", file=sys.stderr)
        return 1
    city = CITY_CODES.get(_norm(p["city"]))
    if not city:
        print(f"錯誤：不認得的縣市 {p['city']}", file=sys.stderr)
        return 1
    if not town_code:
        print(f"錯誤：無網路時查不到鄉鎮代碼，請用 --town-code 指定"
              f"（例如大安區是 A02）。有網路的一方可用："
              f" curl -s https://lvr.land.moi.gov.tw/SERVICE/CITY/{city}", file=sys.stderr)
        return 1

    start, end = roc_ym(months_ago(months)), roc_ym(datetime.date.today())
    c = LVRClient()
    out: dict[str, Any] = {"地址": address, "city": city, "town": town_code,
                           "區間": f"{start} ~ {end}", "要抓的": []}
    for qt, label, slug in KINDS:
        url = c.build_url(qry_type=qt, city=city, town=town_code,
                          start=start, end=end, ftype=ftype if qt != RENT else "")
        out["要抓的"].append({"類型": label, "存成": f"{slug}.json", "url": url})

    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("\n把上面每個 url 抓下來，回應原封不動存成對應的『存成』檔名，然後：\n"
          f'  python3 relay.py build "{address}" --biz biz.json --rent rent.json '
          "--sale sale.json --json out/nearby.json", file=sys.stderr)
    return 0


def _load(path: str | None) -> list[dict[str, Any]]:
    """讀抓回來的 JSON。容忍被包成 {"data": [...]} 或前後有雜訊的情況。"""
    if not path:
        return []
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 抓取工具有時會在 JSON 前後加說明文字，切出最外層陣列再試一次
        i, j = raw.find("["), raw.rfind("]")
        if i < 0 or j < 0:
            raise
        data = json.loads(raw[i:j + 1])
    if isinstance(data, dict):
        for k in ("data", "results", "items", "body"):
            if isinstance(data.get(k), list):
                return data[k]
        return []
    return data if isinstance(data, list) else []


def build(address: str, files: dict[str, str | None], radius: int, months: int,
          out_path: str | None) -> int:
    """把抓回來的 JSON 併成跟 nearby.py 一樣的輸出。這一步也不連網。"""
    raw_by_kind = {label: _load(files.get(slug)) for _, label, slug in KINDS}
    total = sum(len(v) for v in raw_by_kind.values())
    if not total:
        print("錯誤：三個檔案都沒有資料，確認抓回來的內容是 JSON 陣列", file=sys.stderr)
        return 1

    # 圓心：從抓回來的紀錄裡挑門牌最接近的一筆（買賣資料通常最密）
    center = None
    for label in ("買賣", "租賃", "預售屋"):
        center = center_from_lvr_rows(address, raw_by_kind[label])
        if center:
            break
    if not center:
        print(f"錯誤：在抓回來的資料裡找不到「{address}」所在路段的成交案件，"
              "無法定出圓心。可放寬 --months 重抓，或確認地址的路名正確。", file=sys.stderr)
        return 1
    print(f"圓心：{center['lat']:.6f}, {center['lon']:.6f}"
          f"（比對到 {center['matched']}）", file=sys.stderr)

    data: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for qt, label, _ in KINDS:
        rows = []
        for r in raw_by_kind[label]:
            if r.get("lat") is None or r.get("lon") is None:
                continue
            d = haversine(center["lat"], center["lon"], float(r["lat"]), float(r["lon"]))
            if d > radius:
                continue
            n = normalize(r, qt)
            n.pop("_raw", None)
            n["距離公尺"] = round(d)
            rows.append(n)
        rows.sort(key=lambda x: x["距離公尺"])
        data[label] = rows
        counts[label] = len(raw_by_kind[label])
        print(f"  {label}: 半徑內 {len(rows)} 筆 / 抓回 {counts[label]} 筆", file=sys.stderr)

    lvr_data = {"查詢區間": f"近 {months} 個月",
                "行政區": f"{center['parsed']['city']}{center['parsed']['town']}",
                "半徑公尺": radius, "資料": data, "全區筆數": counts}
    summary = summarize(lvr_data)
    payload = {
        "查詢地址": address,
        "定位": center,
        "參數": {"半徑公尺": radius, "回溯月數": months},
        "產生時間": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "實價登錄": lvr_data,
        "統計": summary,
        "租金報酬率": rent_yield(summary),
        "市場開價": {"資料": [], "失敗": ["接力模式未抓房仲網站；"
                                          "可由具備網路的一方讀各站搜尋頁後補進來"]},
        "_模式": "relay（外部抓取）",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out_path:
        _write(out_path, text)
        print(f"→ {out_path}", file=sys.stderr)
    else:
        print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="無網路環境的接力模式")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="測沙箱有沒有對外連線")

    pp = sub.add_parser("plan", help="算出要抓哪些 URL（不連網）")
    pp.add_argument("address")
    pp.add_argument("--months", type=int, default=24)
    pp.add_argument("--town-code", default="", help="鄉鎮代碼，如 A02（大安區）")
    pp.add_argument("--ftype", default="", help="建物型態代碼，如 05=住宅大樓")

    bp = sub.add_parser("build", help="把抓回來的 JSON 併成 nearby.json（不連網）")
    bp.add_argument("address")
    bp.add_argument("--biz"); bp.add_argument("--rent"); bp.add_argument("--sale")
    bp.add_argument("--radius", type=int, default=500)
    bp.add_argument("--months", type=int, default=24)
    bp.add_argument("--json", dest="out")

    a = ap.parse_args()
    if a.cmd == "check":
        return check()
    if a.cmd == "plan":
        return plan(a.address, a.months, a.town_code, a.ftype)
    return build(a.address, {"biz": a.biz, "rent": a.rent, "sale": a.sale},
                 a.radius, a.months, a.out)


if __name__ == "__main__":
    sys.exit(main())
