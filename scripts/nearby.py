#!/usr/bin/env python3
"""給一個地址，查附近的成交行情與現在開價。

流程：
  1. 地址 → 座標（geocode.py，可插拔 provider）
  2. 內政部實價登錄查該行政區的買賣/租賃/預售（回傳自帶 lat/lon）
  3. 用 haversine 半徑篩出「附近」，算統計
  4. 抓房仲網站的現售/出租開價（listings.py）
  5. 全部吐成一份 JSON，交給 report.py 產 HTML

用法：
    python3 nearby.py "台北市大安區忠孝東路四段45號"
    python3 nearby.py "台北市大安區忠孝東路四段45號" --radius 300 --months 36 --json out.json
    python3 nearby.py "..." --no-listings          # 只看實價登錄
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import listings as LST  # noqa: E402
from geocode import GeocodeError, geocode, haversine, load_config, parse_address  # noqa: E402
from lvr import BIZ, PRESALE, RENT, LVRClient, normalize  # noqa: E402


def roc_ym(dt: datetime.date) -> str:
    return f"{dt.year - 1911}/{dt.month}"


def months_ago(n: int) -> datetime.date:
    today = datetime.date.today()
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12
        y -= 1
    return datetime.date(y, m, 1)


def _stats(values: list[float]) -> dict[str, Any] | None:
    vals = sorted(v for v in values if v is not None and v > 0)
    if not vals:
        return None
    def pct(p: float) -> float:
        i = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * p))))
        return round(vals[i], 2)
    return {
        "筆數": len(vals),
        "中位數": round(statistics.median(vals), 2),
        "平均": round(statistics.fmean(vals), 2),
        "最低": round(vals[0], 2),
        "最高": round(vals[-1], 2),
        "P25": pct(0.25),
        "P75": pct(0.75),
    }


def _季(roc_date: str) -> str:
    """"114/07/03" → "114Q3"。"""
    parts = (roc_date or "").split("/")
    if len(parts) < 2 or not parts[1].isdigit():
        return ""
    return f"{parts[0]}Q{(int(parts[1]) - 1) // 3 + 1}"


def _屋齡桶(age: float | None) -> str:
    if age is None:
        return "未知"
    for lo, hi, name in [(0, 5, "5年內"), (5, 10, "5-10年"), (10, 20, "10-20年"),
                         (20, 30, "20-30年"), (30, 999, "30年以上")]:
        if lo <= age < hi:
            return name
    return "未知"


def collect_lvr(center: dict[str, Any], radius: int, months: int,
                include_presale: bool = True, delay: float = 0.8,
                keep_raw: bool = False) -> dict[str, Any]:
    p = center["parsed"]
    client = LVRClient(delay=delay)
    city, town = client.resolve(p["city"], p["town"])
    start = roc_ym(months_ago(months))
    end = roc_ym(datetime.date.today())

    kinds = [(BIZ, "買賣"), (RENT, "租賃")] + ([(PRESALE, "預售屋")] if include_presale else [])
    out: dict[str, Any] = {"查詢區間": f"{start} ~ {end}", "行政區": f"{p['city']}{p['town']}",
                           "半徑公尺": radius, "資料": {}, "全區筆數": {}}
    for qt, label in kinds:
        try:
            raw = client.query(qry_type=qt, city=city, town=town, start=start, end=end)
        except Exception as exc:  # noqa: BLE001
            out["資料"][label] = []
            out.setdefault("錯誤", []).append(f"{label}: {exc}")
            continue
        out["全區筆數"][label] = len(raw)
        rows = []
        for r in raw:
            if r.get("lat") is None or r.get("lon") is None:
                continue
            d = haversine(center["lat"], center["lon"], float(r["lat"]), float(r["lon"]))
            if d > radius:
                continue
            n = normalize(r, qt)
            n["距離公尺"] = round(d)
            if not keep_raw:
                n.pop("_raw", None)  # 原始單字母欄位佔了輸出檔九成體積
            rows.append(n)
        rows.sort(key=lambda x: x["距離公尺"])
        out["資料"][label] = rows
    return out


def summarize(lvr_data: dict[str, Any]) -> dict[str, Any]:
    s: dict[str, Any] = {}
    for label, rows in lvr_data["資料"].items():
        if not rows:
            s[label] = {"筆數": 0}
            continue
        if label == "租賃":
            key, unit = "單價元每坪", "元/坪/月"
            totals = [r.get("月租金元") for r in rows]
            total_label, total_unit = "月租金", "元"
        else:
            key, unit = "單價萬元每坪", "萬元/坪"
            totals = [r.get("總價萬元") for r in rows]
            total_label, total_unit = "總價", "萬元"
        block: dict[str, Any] = {
            "筆數": len(rows),
            "單價": _stats([r.get(key) for r in rows]),
            "單價單位": unit,
            total_label: _stats([t for t in totals if t]),
            f"{total_label}單位": total_unit,
            "坪數": _stats([r.get("面積坪") for r in rows]),
        }
        by_type: dict[str, list[float]] = {}
        for r in rows:
            t = (r.get("建物型態") or "未分類").split("(")[0]
            if r.get(key):
                by_type.setdefault(t, []).append(r[key])
        block["依建物型態"] = {k: _stats(v) for k, v in sorted(
            by_type.items(), key=lambda kv: -len(kv[1]))}
        by_age: dict[str, list[float]] = {}
        for r in rows:
            if r.get(key):
                by_age.setdefault(_屋齡桶(r.get("屋齡")), []).append(r[key])
        block["依屋齡"] = {k: _stats(v) for k, v in by_age.items()}
        by_q: dict[str, list[float]] = {}
        for r in rows:
            q = _季(r.get("日期", ""))
            if q and r.get(key):
                by_q.setdefault(q, []).append(r[key])
        block["依季度"] = {k: _stats(v) for k, v in sorted(by_q.items())}
        s[label] = block
    return s


def rent_yield(summary: dict[str, Any]) -> dict[str, Any] | None:
    """用中位數估算租金報酬率（年）。租金是元/坪/月，房價是萬元/坪。"""
    buy = (summary.get("買賣") or {}).get("單價")
    rent = (summary.get("租賃") or {}).get("單價")
    if not buy or not rent:
        return None
    price_per_ping = buy["中位數"] * 10000
    annual_rent = rent["中位數"] * 12
    if price_per_ping <= 0:
        return None
    return {
        "年化租金報酬率%": round(annual_rent / price_per_ping * 100, 2),
        "說明": (f"以買賣單價中位數 {buy['中位數']} 萬/坪、"
                 f"租金中位數 {rent['中位數']} 元/坪/月 推估，未扣稅費與空置。"),
    }


def _write(path: str, text: str) -> None:
    """寫檔並自動建立上層目錄 —— 使用者照著 README 打 `--json out/x.json`
    時，out/ 通常還不存在，不該因此炸掉。"""
    p = Path(path)
    if p.parent != Path(""):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="地址 → 附近行情（實價登錄 + 現售/租賃開價）")
    ap.add_argument("address")
    ap.add_argument("--radius", type=int, default=500, help="公尺，預設 500")
    ap.add_argument("--months", type=int, default=24, help="回溯月數，預設 24")
    ap.add_argument("--provider", default="", help="指定 geocode provider")
    ap.add_argument("--no-listings", action="store_true", help="不抓房仲網站")
    ap.add_argument("--no-presale", action="store_true", help="不查預售屋")
    ap.add_argument("--site", action="append", choices=list(LST.SITES))
    ap.add_argument("--pages", type=int, default=1, help="每個房仲網站抓幾頁")
    ap.add_argument("--keep-raw", action="store_true", help="保留實登原始單字母欄位（檔案會大很多）")
    ap.add_argument("--json", help="輸出檔，預設 stdout")
    args = ap.parse_args()

    cfg = load_config()
    print(f"定位「{args.address}」…", file=sys.stderr)
    try:
        center = geocode(args.address, args.provider, cfg)
    except GeocodeError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    print(f"  → {center['lat']:.6f}, {center['lon']:.6f}"
          f"（{center['provider']} / {center['precision']}）", file=sys.stderr)

    print(f"查實價登錄（半徑 {args.radius}m、近 {args.months} 個月）…", file=sys.stderr)
    lvr_data = collect_lvr(center, args.radius, args.months, not args.no_presale,
                           keep_raw=args.keep_raw)
    for k, v in lvr_data["資料"].items():
        print(f"  {k}: 半徑內 {len(v)} 筆 / 全區 {lvr_data['全區筆數'].get(k, '?')} 筆",
              file=sys.stderr)

    market: list[dict[str, Any]] = []
    errors: list[str] = []
    if not args.no_listings:
        p = center["parsed"]
        print("抓房仲網站開價…", file=sys.stderr)
        market, errors = LST.collect(p["city"], p["town"], args.site, args.pages)
        for r in market:  # 有座標的順便算距離並用半徑再篩一次
            if r.get("lat") and r.get("lon"):
                r["距離公尺"] = round(haversine(center["lat"], center["lon"],
                                                float(r["lat"]), float(r["lon"])))
        near = [r for r in market if r.get("距離公尺") is None or r["距離公尺"] <= args.radius]
        print(f"  半徑內/全部：{len(near)}/{len(market)}", file=sys.stderr)

    summary = summarize(lvr_data)
    payload = {
        "查詢地址": args.address,
        "定位": center,
        "參數": {"半徑公尺": args.radius, "回溯月數": args.months},
        "產生時間": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "實價登錄": lvr_data,
        "統計": summary,
        "租金報酬率": rent_yield(summary),
        "市場開價": {"資料": market, "失敗": errors},
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json:
        _write(args.json, text)
        print(f"→ {args.json}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
