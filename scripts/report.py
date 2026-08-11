#!/usr/bin/env python3
"""把 nearby.py 的 JSON 變成一份自足的 HTML 報告（inline SVG，無外部相依）。

    python3 nearby.py "台北市大安區忠孝東路四段45號" --json out.json
    python3 report.py out.json --html report.html

配色用的是驗證過的三色分類色盤（blue / orange / aqua），淺色與深色各自選色，
所有圖都直接標數值並附完整表格 —— 淺色模式的 aqua 對比未達 3:1，需要這層補償。
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from pathlib import Path
from typing import Any

# 三個資料類別固定配色，順序不隨篩選改變
SERIES = {"買賣": 1, "租賃": 2, "預售屋": 3}

CSS = """
*, *::before, *::after { box-sizing: border-box; }
.viz-root {
  color-scheme: light;
  --surface-0: #ffffff; --surface-1: #fcfcfb; --surface-2: #f4f4f2;
  --border: #e2e2dd; --border-strong: #cfcfc8;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #7a7975;
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;
  --grid: #e8e8e4;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    color-scheme: dark;
    --surface-0: #131312; --surface-1: #1a1a19; --surface-2: #232322;
    --border: #34342f; --border-strong: #45453f;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #96958c;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
    --grid: #2c2c29;
  }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --surface-0: #131312; --surface-1: #1a1a19; --surface-2: #232322;
  --border: #34342f; --border-strong: #45453f;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #96958c;
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
  --grid: #2c2c29;
}
.viz-root {
  background: var(--surface-0); color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang TC",
    "Noto Sans TC", "Microsoft JhengHei", sans-serif;
  line-height: 1.6; margin: 0; padding: 1.5rem 1.25rem 4rem;
  max-width: 60rem; margin-inline: auto; -webkit-text-size-adjust: 100%;
}
h1 { font-size: 1.5rem; line-height: 1.3; margin: 0 0 .35rem; letter-spacing: -.01em; }
h2 { font-size: 1.05rem; margin: 2.25rem 0 .75rem; letter-spacing: -.005em; }
h3 { font-size: .9rem; margin: 1.25rem 0 .5rem; color: var(--text-secondary); font-weight: 600; }
p  { margin: .35rem 0; }
.sub { color: var(--text-secondary); font-size: .875rem; }
.muted { color: var(--text-muted); font-size: .8125rem; }
.tiles { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); margin: 1.25rem 0 0; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: .625rem; padding: .875rem .9rem; }
.tile .label { font-size: .75rem; color: var(--text-secondary); display: flex; align-items: center; gap: .4rem; }
.tile .swatch { width: .55rem; height: .55rem; border-radius: .15rem; flex: none; }
.tile .value { font-size: 1.5rem; font-weight: 650; letter-spacing: -.02em; margin-top: .15rem;
  font-variant-numeric: tabular-nums; }
.tile .unit { font-size: .8125rem; font-weight: 400; color: var(--text-secondary); margin-left: .15rem; }
.tile .foot { font-size: .75rem; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: .625rem;
  padding: 1rem .9rem .6rem; margin-top: .5rem; }
.legend { display: flex; flex-wrap: wrap; gap: .9rem; font-size: .8125rem; color: var(--text-secondary);
  margin: 0 0 .5rem .1rem; }
.legend span { display: inline-flex; align-items: center; gap: .35rem; }
.legend i { width: .7rem; height: .7rem; border-radius: .15rem; display: inline-block; }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
svg { display: block; max-width: 100%; height: auto; }
svg text { font-family: inherit; }
.bar { transition: opacity .12s; }
.bar:hover { opacity: .72; }
table { border-collapse: collapse; width: 100%; font-size: .8125rem; min-width: 40rem; }
th, td { text-align: left; padding: .45rem .55rem; border-bottom: 1px solid var(--border); white-space: nowrap; }
th { color: var(--text-secondary); font-weight: 600; position: sticky; top: 0; background: var(--surface-1); }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:hover { background: var(--surface-2); }
.tag { display: inline-block; font-size: .6875rem; padding: .05rem .35rem; border-radius: .25rem;
  border: 1px solid var(--border-strong); color: var(--text-secondary); }
a { color: var(--series-1); }
.note { background: var(--surface-2); border: 1px solid var(--border); border-radius: .5rem;
  padding: .75rem .9rem; font-size: .8125rem; color: var(--text-secondary); margin-top: .75rem; }
.note ul { margin: .35rem 0 0; padding-left: 1.1rem; }
"""


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def fmt(v: Any, digits: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{v:,.0f}"
    return str(v)


def tile(label: str, value: str, unit: str = "", foot: str = "", series: int = 0) -> str:
    sw = f'<span class="swatch" style="background:var(--series-{series})"></span>' if series else ""
    return (f'<div class="tile"><div class="label">{sw}{esc(label)}</div>'
            f'<div class="value">{esc(value)}<span class="unit">{esc(unit)}</span></div>'
            f'<div class="foot">{esc(foot)}</div></div>')


# --- 圖 --------------------------------------------------------------------

def histogram(values: list[float], series: int, unit: str, median: float | None,
              width: int = 860, height: int = 230) -> str:
    """單價分布。單一序列 → 不需要圖例，標題已經指明是什麼。"""
    vals = sorted(v for v in values if v)
    if len(vals) < 3:
        return '<p class="muted">資料量不足，無法繪製分布圖。</p>'
    # 砍掉頭尾極端值，否則一兩筆離群案件會把整張圖壓扁；被略去的筆數標在角落
    lo = vals[max(0, int(len(vals) * 0.02))]
    hi = vals[min(len(vals) - 1, int(len(vals) * 0.97))]
    if hi <= lo:
        lo, hi = vals[0], vals[-1]
    dropped = sum(1 for v in vals if v < lo or v > hi)
    bins = min(20, max(6, int(math.sqrt(len(vals)))))
    step = (hi - lo) / bins if hi > lo else 1
    counts = [0] * bins
    for v in vals:
        i = min(bins - 1, int((v - lo) / step)) if step else 0
        if 0 <= i < bins:
            counts[i] += 1
    top = max(counts) or 1
    ml, mr, mt, mb = 38, 14, 38, 34  # mt 留給中位數標籤一條專用帶，避免壓到長條上的數字
    pw, ph = width - ml - mr, height - mt - mb
    bw = pw / bins
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="單價分布直方圖，{len(vals)} 筆">']
    for gy in range(5):
        y = mt + ph - ph * gy / 4
        parts.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{width - mr}" y2="{y:.1f}" '
                     f'stroke="var(--grid)" stroke-width="1"/>')
        parts.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="10" '
                     f'fill="var(--text-muted)">{round(top * gy / 4)}</text>')
    for i, c in enumerate(counts):
        h = ph * c / top
        x = ml + i * bw + 1          # 2px 間隙：相鄰長條之間留出 surface
        y = mt + ph - h
        r = min(4.0, h / 2, (bw - 2) / 2)
        lab = f"{lo + i * step:.0f}–{lo + (i + 1) * step:.0f} {unit}：{c} 筆"
        parts.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{max(bw - 2, 1):.1f}" '
                     f'height="{max(h, 0.5):.1f}" rx="{r:.1f}" fill="var(--series-{series})">'
                     f'<title>{esc(lab)}</title></rect>')
        if c == top:
            # 最高的長條直接把筆數標在條內，才不會跟上方的中位數標籤打架
            parts.append(f'<text x="{x + (bw - 2) / 2:.1f}" y="{y + 14:.1f}" text-anchor="middle" '
                         f'font-size="11" font-weight="700" fill="var(--surface-1)">{c}</text>')
    if median and lo <= median <= hi:
        mx = ml + pw * (median - lo) / (hi - lo or 1)
        parts.append(f'<line x1="{mx:.1f}" y1="{mt - 4}" x2="{mx:.1f}" y2="{mt + ph}" '
                     f'stroke="var(--text-primary)" stroke-width="2" stroke-dasharray="4 3"/>')
        anchor = "start" if mx < width * 0.72 else "end"
        dx = 6 if anchor == "start" else -6
        parts.append(f'<text x="{mx + dx:.1f}" y="{mt - 10}" text-anchor="{anchor}" font-size="11" '
                     f'font-weight="600" fill="var(--text-primary)">中位數 {fmt(median)}</text>')
    parts.append(f'<line x1="{ml}" y1="{mt + ph}" x2="{width - mr}" y2="{mt + ph}" '
                 f'stroke="var(--border-strong)" stroke-width="1"/>')
    for i in (0, bins // 2, bins):
        x = ml + i * bw
        parts.append(f'<text x="{x:.1f}" y="{mt + ph + 16}" text-anchor="middle" font-size="10" '
                     f'fill="var(--text-secondary)">{fmt(lo + i * step, 0)}</text>')
    tail = f"{esc(unit)}（已略去 {dropped} 筆極端值）" if dropped else esc(unit)
    parts.append(f'<text x="{width - mr}" y="{height - 4}" text-anchor="end" font-size="10" '
                 f'fill="var(--text-muted)">{tail}</text>')
    parts.append("</svg>")
    return "".join(parts)


def trend(series_data: dict[str, dict[str, Any]], unit: str,
          width: int = 860, height: int = 250) -> str:
    """季度中位數折線。只放同單位的序列 —— 絕不做雙 Y 軸。"""
    series_data = {k: v for k, v in series_data.items() if len(v) >= 2}
    if not series_data:
        return '<p class="muted">季度資料不足，無法繪製趨勢圖。</p>'
    quarters = sorted({q for v in series_data.values() for q in v})
    if len(quarters) < 2:
        return '<p class="muted">季度資料不足，無法繪製趨勢圖。</p>'
    allv = [p for v in series_data.values() for p in v.values()]
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.15 or (hi * 0.1 or 1)
    lo, hi = max(0, lo - pad), hi + pad
    ml, mr, mt, mb = 46, 74, 18, 34
    pw, ph = width - ml - mr, height - mt - mb
    xs = {q: ml + (pw * i / (len(quarters) - 1)) for i, q in enumerate(quarters)}
    def y(v: float) -> float:
        return mt + ph - ph * (v - lo) / (hi - lo or 1)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="各季單價中位數趨勢">']
    for gy in range(5):
        yy = mt + ph - ph * gy / 4
        parts.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{width - mr}" y2="{yy:.1f}" '
                     f'stroke="var(--grid)" stroke-width="1"/>')
        parts.append(f'<text x="{ml - 6}" y="{yy + 4:.1f}" text-anchor="end" font-size="10" '
                     f'fill="var(--text-muted)">{fmt(lo + (hi - lo) * gy / 4, 0)}</text>')
    for q in quarters:
        parts.append(f'<text x="{xs[q]:.1f}" y="{mt + ph + 16}" text-anchor="middle" font-size="10" '
                     f'fill="var(--text-secondary)">{esc(q)}</text>')
    for name, pts in series_data.items():
        s = SERIES.get(name, 1)
        seq = [(xs[q], y(pts[q]), q, pts[q]) for q in quarters if q in pts]
        d = " ".join(("M" if i == 0 else "L") + f"{x:.1f},{yy:.1f}"
                     for i, (x, yy, _, _) in enumerate(seq))
        parts.append(f'<path d="{d}" fill="none" stroke="var(--series-{s})" stroke-width="2" '
                     f'stroke-linecap="round" stroke-linejoin="round"/>')
        for x, yy, q, v in seq:
            # 2px surface ring 讓重疊的點還是分得開
            parts.append(f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="4.5" fill="var(--series-{s})" '
                         f'stroke="var(--surface-1)" stroke-width="2">'
                         f'<title>{esc(name)} {esc(q)}：{fmt(v)} {esc(unit)}</title></circle>')
        lx, ly, _, lv = seq[-1]
        parts.append(f'<text x="{lx + 9:.1f}" y="{ly + 4:.1f}" font-size="11" font-weight="600" '
                     f'fill="var(--text-primary)">{esc(name)} {fmt(lv)}</text>')
    parts.append(f'<line x1="{ml}" y1="{mt + ph}" x2="{width - mr}" y2="{mt + ph}" '
                 f'stroke="var(--border-strong)" stroke-width="1"/>')
    parts.append(f'<text x="{ml}" y="{height - 4}" font-size="10" '
                 f'fill="var(--text-muted)">中位數 {esc(unit)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def hbar(rows: list[tuple[str, dict[str, Any]]], series: int, unit: str,
         width: int = 860) -> str:
    """依建物型態的中位數橫條，值直接標在條末。"""
    rows = [(k, v) for k, v in rows if v][:8]
    if not rows:
        return '<p class="muted">無資料。</p>'
    rh, gap = 26, 8
    ml, mr, mt = 96, 96, 6
    height = mt + len(rows) * (rh + gap)
    pw = width - ml - mr
    top = max(v["中位數"] for _, v in rows) or 1
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="依建物型態的單價中位數">']
    for i, (name, st) in enumerate(rows):
        y = mt + i * (rh + gap)
        w = pw * st["中位數"] / top
        parts.append(f'<text x="{ml - 8}" y="{y + rh / 2 + 4}" text-anchor="end" font-size="11.5" '
                     f'fill="var(--text-primary)">{esc(name)}</text>')
        parts.append(f'<rect class="bar" x="{ml}" y="{y}" width="{max(w, 2):.1f}" height="{rh}" '
                     f'rx="4" fill="var(--series-{series})">'
                     f'<title>{esc(name)}：中位數 {fmt(st["中位數"])} {esc(unit)}'
                     f'（{st["筆數"]} 筆，P25 {fmt(st["P25"])}–P75 {fmt(st["P75"])}）</title></rect>')
        parts.append(f'<text x="{ml + w + 8:.1f}" y="{y + rh / 2 + 4}" font-size="11.5" '
                     f'font-weight="600" fill="var(--text-primary)">{fmt(st["中位數"])}'
                     f'<tspan fill="var(--text-muted)" font-weight="400"> · {st["筆數"]}筆</tspan></text>')
    parts.append("</svg>")
    return "".join(parts)


# --- 表 --------------------------------------------------------------------

def deals_table(rows: list[dict[str, Any]], kind: str, limit: int = 40) -> str:
    if not rows:
        return '<p class="muted">半徑內沒有符合的成交案件。</p>'
    rent = kind == "租賃"
    price_h = "月租金 (元)" if rent else "總價 (萬)"
    unit_h = "單價 (元/坪/月)" if rent else "單價 (萬/坪)"
    price_k = "月租金元" if rent else "總價萬元"
    unit_k = "單價元每坪" if rent else "單價萬元每坪"
    out = ['<div class="scroll"><table><thead><tr>'
           '<th class="n">距離</th><th>日期</th><th>地址</th><th>社區</th>'
           f'<th class="n">{price_h}</th><th class="n">{unit_h}</th>'
           '<th class="n">坪數</th><th>格局</th><th>型態</th>'
           '<th class="n">屋齡</th><th>樓層</th></tr></thead><tbody>']
    for r in rows[:limit]:
        out.append(
            f'<tr><td class="n">{fmt(r.get("距離公尺"), 0)}m</td><td>{esc(r.get("日期"))}</td>'
            f'<td>{esc(r.get("地址"))}</td><td>{esc(r.get("社區"))}</td>'
            f'<td class="n">{fmt(r.get(price_k), 0)}</td>'
            f'<td class="n">{fmt(r.get(unit_k), 1 if not rent else 0)}</td>'
            f'<td class="n">{fmt(r.get("面積坪"))}</td><td>{esc(r.get("格局"))}</td>'
            f'<td>{esc((r.get("建物型態") or "").split("(")[0])}</td>'
            f'<td class="n">{fmt(r.get("屋齡"), 0)}</td><td>{esc(r.get("樓層"))}</td></tr>')
    out.append("</tbody></table></div>")
    if len(rows) > limit:
        out.append(f'<p class="muted">僅列出距離最近的 {limit} 筆，共 {len(rows)} 筆。</p>')
    return "".join(out)


def market_table(rows: list[dict[str, Any]], limit: int = 60) -> str:
    if not rows:
        return '<p class="muted">沒有抓到市場開價資料。</p>'
    rows = sorted(rows, key=lambda r: (r.get("類型") or "", -(r.get("總價萬元") or 0)))
    out = ['<div class="scroll"><table><thead><tr><th>來源</th><th>類型</th><th>標題</th>'
           '<th>地址</th><th class="n">開價</th><th class="n">單價(萬/坪)</th>'
           '<th class="n">坪數</th><th>格局</th><th>樓層</th><th>連結</th>'
           '</tr></thead><tbody>']
    for r in rows[:limit]:
        price = (f'{fmt(r.get("總價萬元"), 0)} 萬' if r.get("總價萬元")
                 else (f'{fmt(r.get("月租金元"), 0)} 元/月' if r.get("月租金元") else "—"))
        link = (f'<a href="{esc(r.get("連結"))}" target="_blank" rel="noopener">看物件</a>'
                if r.get("連結") else "")
        out.append(
            f'<tr><td><span class="tag">{esc(r.get("來源"))}</span></td>'
            f'<td>{esc(r.get("類型"))}</td><td>{esc((r.get("標題") or "")[:24])}</td>'
            f'<td>{esc(r.get("地址"))}</td><td class="n">{price}</td>'
            f'<td class="n">{fmt(r.get("單價萬元每坪"))}</td>'
            f'<td class="n">{fmt(r.get("坪數"))}</td><td>{esc(r.get("格局"))}</td>'
            f'<td>{esc(r.get("樓層"))}</td><td>{link}</td></tr>')
    out.append("</tbody></table></div>")
    if len(rows) > limit:
        out.append(f'<p class="muted">僅列出 {limit} 筆，共 {len(rows)} 筆。</p>')
    return "".join(out)


# --- 組裝 ------------------------------------------------------------------

def build(data: dict[str, Any]) -> str:
    stats = data.get("統計") or {}
    lvr = data.get("實價登錄") or {}
    rows_by_kind = lvr.get("資料") or {}
    center = data.get("定位") or {}
    params = data.get("參數") or {}
    market = (data.get("市場開價") or {}).get("資料") or []
    market_errors = (data.get("市場開價") or {}).get("失敗") or []

    p = []
    p.append(f'<h1>{esc(data.get("查詢地址"))} 週邊行情</h1>')
    p.append(f'<p class="sub">半徑 {esc(params.get("半徑公尺"))} 公尺 · '
             f'近 {esc(params.get("回溯月數"))} 個月 · {esc(lvr.get("查詢區間"))} · '
             f'{esc(lvr.get("行政區"))}</p>')
    lat, lon = center.get("lat"), center.get("lon")
    coord = f"{lat:.6f}, {lon:.6f}" if isinstance(lat, float) and isinstance(lon, float) else "—"
    p.append(f'<p class="muted">定位：{esc(coord)}'
             f'（{esc(center.get("provider"))} — {esc(center.get("precision"))}）'
             f' · 產生於 {esc(data.get("產生時間"))}</p>')

    # KPI
    tiles = []
    buy = (stats.get("買賣") or {}).get("單價")
    if buy:
        tiles.append(tile("買賣單價中位數", fmt(buy["中位數"]), " 萬/坪",
                          f'P25–P75 {fmt(buy["P25"])}–{fmt(buy["P75"])} · {buy["筆數"]} 筆', 1))
    tot = (stats.get("買賣") or {}).get("總價")
    if tot:
        tiles.append(tile("買賣總價中位數", fmt(tot["中位數"], 0), " 萬",
                          f'{fmt(tot["最低"], 0)} – {fmt(tot["最高"], 0)} 萬', 1))
    rent = (stats.get("租賃") or {}).get("單價")
    if rent:
        tiles.append(tile("租金中位數", fmt(rent["中位數"], 0), " 元/坪/月",
                          f'P25–P75 {fmt(rent["P25"], 0)}–{fmt(rent["P75"], 0)} · {rent["筆數"]} 筆', 2))
    rtot = (stats.get("租賃") or {}).get("月租金")
    if rtot:
        tiles.append(tile("月租金中位數", fmt(rtot["中位數"], 0), " 元",
                          f'{fmt(rtot["最低"], 0)} – {fmt(rtot["最高"], 0)} 元', 2))
    pre = (stats.get("預售屋") or {}).get("單價")
    if pre:
        tiles.append(tile("預售屋單價中位數", fmt(pre["中位數"]), " 萬/坪",
                          f'{pre["筆數"]} 筆', 3))
    ry = data.get("租金報酬率")
    if ry:
        tiles.append(tile("推估年化租金報酬率", fmt(ry["年化租金報酬率%"], 2), " %",
                          "以中位數推估，未扣稅費"))
    p.append(f'<div class="tiles">{"".join(tiles)}</div>')

    # 分布
    if buy:
        p.append("<h2>買賣單價分布</h2>")
        p.append('<div class="card">' + histogram(
            [r.get("單價萬元每坪") for r in rows_by_kind.get("買賣", [])], 1, "萬/坪",
            buy["中位數"]) + "</div>")

    # 趨勢（同單位才放同一張圖）
    tr = {}
    for name in ("買賣", "預售屋"):
        q = (stats.get(name) or {}).get("依季度") or {}
        pts = {k: v["中位數"] for k, v in q.items() if v}
        if pts:
            tr[name] = pts
    if tr:
        p.append("<h2>單價季度趨勢</h2>")
        legend = "".join(f'<span><i style="background:var(--series-{SERIES[k]})"></i>{esc(k)}</span>'
                         for k in tr)
        p.append(f'<div class="card"><div class="legend">{legend}</div>'
                 + trend(tr, "萬元/坪") + "</div>")
    rq = (stats.get("租賃") or {}).get("依季度") or {}
    rpts = {k: v["中位數"] for k, v in rq.items() if v}
    if len(rpts) >= 2:
        p.append("<h3>租金季度趨勢（元/坪/月）</h3>")
        p.append('<div class="card">' + trend({"租賃": rpts}, "元/坪/月", height=200) + "</div>")

    # 依型態
    for name, unit, digits in (("買賣", "萬元/坪", 1), ("租賃", "元/坪/月", 0)):
        by = (stats.get(name) or {}).get("依建物型態") or {}
        items = [(k, v) for k, v in by.items() if v and v["筆數"] >= 2]
        if items:
            p.append(f"<h2>{name}單價 — 依建物型態</h2>")
            p.append('<div class="card">' + hbar(items, SERIES[name], unit) + "</div>")

    # 依屋齡
    by_age = (stats.get("買賣") or {}).get("依屋齡") or {}
    order = ["5年內", "5-10年", "10-20年", "20-30年", "30年以上", "未知"]
    age_items = [(k, by_age[k]) for k in order if by_age.get(k) and by_age[k]["筆數"] >= 2]
    if age_items:
        p.append("<h2>買賣單價 — 依屋齡</h2>")
        p.append('<div class="card">' + hbar(age_items, 1, "萬元/坪") + "</div>")

    # 明細
    for name in ("買賣", "租賃", "預售屋"):
        rows = rows_by_kind.get(name) or []
        if rows:
            p.append(f"<h2>{name}成交明細（{len(rows)} 筆，依距離排序）</h2>")
            p.append(deals_table(rows, name))

    p.append(f"<h2>目前市場開價（{len(market)} 筆）</h2>")
    p.append(market_table(market))

    notes = [
        "實價登錄為「已成交」的歷史資料，申報有時間落差（通常 1–2 個月），"
        "且同一筆可能因車位、裝潢、特殊關係人交易而讓單價失真——看中位數與 P25–P75 區間比看平均可靠。",
        "「單價」由總價 ÷ 總面積計算，含車位的案件會被拉低，車位單獨交易的案件則會拉高。",
        "市場開價是賣方/房東的「要價」，不是成交價；與實登中位數的差距可粗略當作議價空間參考。",
        f"座標來源：{esc(center.get('provider'))}（{esc(center.get('precision'))}）；"
        "定位精度會直接影響「半徑內」的篩選結果。",
    ]
    if market_errors:
        notes.append("以下來源本次抓取失敗：" + "；".join(esc(e) for e in market_errors))
    p.append('<div class="note"><strong>怎麼讀這份報告</strong><ul>'
             + "".join(f"<li>{n}</li>" for n in notes) + "</ul></div>")

    title = f'{data.get("查詢地址", "")} 週邊行情'
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{esc(title)}</title><style>{CSS}</style></head>'
            f'<body class="viz-root">{"".join(p)}</body></html>')


def _write(path: str, text: str) -> None:
    """寫檔並自動建立上層目錄 —— 使用者照著 README 打 `--json out/x.json`
    時，out/ 通常還不存在，不該因此炸掉。"""
    p = Path(path)
    if p.parent != Path(""):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="nearby.py 的 JSON → HTML 報告")
    ap.add_argument("json_file")
    ap.add_argument("--html", default="report.html")
    args = ap.parse_args()
    data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    _write(args.html, build(data))
    print(f"→ {args.html}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
