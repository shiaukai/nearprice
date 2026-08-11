#!/usr/bin/env python3
"""各大房仲/平台的「現在開價」抓取（純 stdlib）。

實價登錄是「已成交」的歷史；這裡補的是「現在市場開價」——賣方開價與出租開價。
兩者一起看才知道現在的議價空間。

各來源的可靠度（實測）：
  sinyi_buy    信義買屋   __NEXT_DATA__ JSON，欄位乾淨且含經緯度   ★★★
  sinyi_rent   信義租屋   舊版 HTML 卡片，需文字解析              ★★
  rent591      591 租屋   Nuxt SSR HTML 卡片                      ★★
  yungching_buy 永慶買屋  Angular SSR HTML 卡片                   ★★
  sale591      591 售屋   有 bot 防護，連續請求會被擋              ★
被擋或改版時，改用 SKILL.md 說的瀏覽器路徑，把結果存成同樣 schema 的 JSON 再餵給 report.py。

用法：
    python3 listings.py --site sinyi_buy --city 台北市 --town 大安區
    python3 listings.py --all --city 台北市 --town 大安區 --json listings.json
"""

from __future__ import annotations

import argparse
import gzip
import html as htmllib
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Callable

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 591 的 regionid / 信義的英文 slug / 永慶的中文
CITY_591 = {
    "臺北市": 1, "基隆市": 2, "新北市": 3, "新竹市": 4, "新竹縣": 5, "桃園市": 6,
    "苗栗縣": 7, "臺中市": 8, "彰化縣": 10, "南投縣": 11, "嘉義市": 12, "嘉義縣": 13,
    "雲林縣": 14, "臺南市": 15, "高雄市": 17, "屏東縣": 19, "宜蘭縣": 21, "臺東縣": 22,
    "花蓮縣": 23, "澎湖縣": 24, "金門縣": 25, "連江縣": 26,
}
CITY_SINYI = {
    "臺北市": "Taipei-city", "新北市": "NewTaipei-city", "桃園市": "Taoyuan-city",
    "臺中市": "Taichung-city", "臺南市": "Tainan-city", "高雄市": "Kaohsiung-city",
    "基隆市": "Keelung-city", "新竹市": "Hsinchu-city", "新竹縣": "Hsinchu-county",
    "苗栗縣": "Miaoli-county", "彰化縣": "Changhua-county", "南投縣": "Nantou-county",
    "雲林縣": "Yunlin-county", "嘉義市": "Chiayi-city", "嘉義縣": "Chiayi-county",
    "屏東縣": "Pingtung-county", "宜蘭縣": "Yilan-county", "花蓮縣": "Hualien-county",
    "臺東縣": "Taitung-county", "澎湖縣": "Penghu-county", "金門縣": "Kinmen-county",
    "連江縣": "Lienchiang-county",
}
# 信義的行政區 slug 是連字號拼音（大安 = Da-an）。注意：實測他們 SSR 進 __NEXT_DATA__ 的
# list 是「全市」的快取，不吃網址上的行政區，真正的分區結果走 sinyiwebapi.sinyi.com.tw/
# filterObject.php（需要 token）。所以這裡靠回傳的 lat/lon 與地址在本地再篩一次。
SINYI_TOWN = {
    "大安區": "Da-an-district", "中正區": "Zhongzheng-district", "中山區": "Zhongshan-district",
    "松山區": "Songshan-district", "信義區": "Xinyi-district", "大同區": "Datong-district",
    "萬華區": "Wanhua-district", "文山區": "Wenshan-district", "南港區": "Nangang-district",
    "內湖區": "Neihu-district", "士林區": "Shilin-district", "北投區": "Beitou-district",
    "板橋區": "Banqiao-district", "新莊區": "Xinzhuang-district", "中和區": "Zhonghe-district",
    "永和區": "Yonghe-district", "三重區": "Sanchong-district", "新店區": "Xindian-district",
    "土城區": "Tucheng-district", "蘆洲區": "Luzhou-district", "汐止區": "Xizhi-district",
    "淡水區": "Tamsui-district", "林口區": "Linkou-district", "三峽區": "Sanxia-district",
    "樹林區": "Shulin-district", "鶯歌區": "Yingge-district",
}


def _norm(s: str) -> str:
    return (s or "").replace("台", "臺").strip()


def fetch(url: str, referer: str = "", timeout: int = 40, retries: int = 2) -> str:
    ctx = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer or url,
            "Connection": "close",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip":
                raw = gzip.decompress(raw)
            elif enc == "deflate":
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            return raw.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"抓取失敗 {url}: {last}")


def _text(fragment: str) -> str:
    """HTML 片段 → 用 | 分隔的可讀文字。"""
    s = re.sub(r"<(script|style)\b.*?</\1>", "", fragment, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", "|", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[\s　]+", " ", s)
    s = re.sub(r"(\s*\|\s*)+", "|", s)
    return s.strip("| ")


def _cards(html_doc: str, cls: str, require: str = "") -> list[str]:
    """粗切卡片：找 class 清單裡「剛好等於 cls」的開始標籤，切到下一張卡片為止。

    class token 必須完全相符 —— 用 \\b 會讓 "item" 誤中 "item-img"、"tag-item"。
    `require` 是額外要求出現在開始標籤裡的字串（例如 data-id=）。
    """
    out: list[str] = []
    pat = re.compile(r'<(?:li|div|article|section)\b[^>]*\bclass="([^"]*)"[^>]*>')
    starts = [m for m in pat.finditer(html_doc)
              if cls in m.group(1).split() and (not require or require in m.group(0))]
    for i, m in enumerate(starts):
        s = m.start()
        e = starts[i + 1].start() if i + 1 < len(starts) else min(s + 8000, len(html_doc))
        out.append(html_doc[s:e])
    return out


def _f(pattern: str, text: str, group: int = 1) -> str:
    m = re.search(pattern, text)
    return m.group(group).strip() if m else ""


def _num(s: str) -> float | None:
    s = re.sub(r"[^\d.]", "", s or "")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _join(v: Any) -> str:
    """有些站的欄位是 list（例如信義的 houselandtypeShow）。"""
    if isinstance(v, (list, tuple)):
        return "、".join(str(x) for x in v if x)
    return str(v or "")


def _row(**kw: Any) -> dict[str, Any]:
    base = {
        "來源": "", "類型": "", "標題": "", "地址": "", "社區": "",
        "總價萬元": None, "月租金元": None, "單價萬元每坪": None,
        "坪數": None, "格局": "", "樓層": "", "屋齡": None, "型態": "",
        "連結": "", "lat": None, "lon": None,
    }
    base.update(kw)
    return base


# --- 信義房屋 --------------------------------------------------------------

def sinyi_buy(city: str, town: str = "", pages: int = 1) -> list[dict[str, Any]]:
    """信義買屋。頁面內嵌 __NEXT_DATA__，欄位最乾淨而且有經緯度。"""
    cslug = CITY_SINYI.get(_norm(city))
    if not cslug:
        raise ValueError(f"信義不支援的縣市：{city}")
    tslug = SINYI_TOWN.get(_norm(town), "")
    seg = f"{cslug}/{tslug}" if tslug else cslug
    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        url = f"https://www.sinyi.com.tw/buy/list/{seg}/default-desc/{page}"
        doc = fetch(url)
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', doc, re.S)
        if not m:
            break
        try:
            data = json.loads(m.group(1))
            items = data["props"]["initialReduxState"]["buyReducer"]["list"]
        except (KeyError, json.JSONDecodeError):
            break
        for it in items:
            total = _num(str(it.get("totalPrice") or ""))
            ping = _num(str(it.get("pingUsed") or it.get("areaBuilding") or ""))
            rows.append(_row(
                來源="信義房屋", 類型="售",
                標題=it.get("name") or "", 地址=it.get("address") or "",
                社區=it.get("commName") or "",
                總價萬元=total, 坪數=ping,
                單價萬元每坪=round(total / ping, 2) if total and ping else None,
                格局=it.get("layout") or "", 樓層=str(it.get("floor") or ""),
                屋齡=_num(str(it.get("age") or "")),
                型態=_join(it.get("houselandtypeShow") or it.get("houselandtype")),
                連結=f"https://www.sinyi.com.tw/buy/house/{it.get('houseNo')}" if it.get("houseNo") else "",
                lat=it.get("latitude"), lon=it.get("longitude"),
            ))
        time.sleep(1.0)
    return rows


def sinyi_rent(city: str, town: str = "", pages: int = 1) -> list[dict[str, Any]]:
    """信義租屋（舊版模板，只能解析卡片文字）。"""
    cslug = CITY_SINYI.get(_norm(city))
    if not cslug:
        raise ValueError(f"信義不支援的縣市：{city}")
    tslug = SINYI_TOWN.get(_norm(town), "")
    seg = f"{cslug}/{tslug}" if tslug else cslug
    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        doc = fetch(f"https://www.sinyi.com.tw/rent/list/{seg}/default-desc/{page}")
        for card in _cards(doc, "search_result_item"):
            t = _text(card)
            hid = _f(r'href="houseno/([A-Z0-9]+)"', card)
            # num-text 可能是「社區 / 地址」也可能只有地址
            parts = [p.strip() for p in _f(r'class="num num-text">([^<]+)<', card).split("/")]
            addr = next((p for p in reversed(parts) if "市" in p or "縣" in p), parts[-1] if parts else "")
            comm = parts[0] if len(parts) > 1 else ""
            rows.append(_row(
                來源="信義房屋", 類型="租",
                標題=_f(r'class="item_title"[^>]*>([^<]+)<', card),
                地址=addr, 社區=comm,
                月租金元=_num(_f(r'class="price_new">\s*<span class="num">([\d,]+)</span>\s*元/月', card)),
                坪數=_num(_f(r'<span class="num">([\d.]+)</span>\s*坪', card)),
                格局=_f(r"(\d+房\d*廳?\d*衛?)", t),
                樓層=_f(r'<span class="num">(\d+/\d+)</span>\s*樓', card),
                型態=_f(r'class="big-chinese">([^<]+)<', card),
                連結=f"https://www.sinyi.com.tw/rent/houseno/{hid}" if hid else "",
            ))
        time.sleep(1.0)
    return [r for r in rows if r["坪數"] or r["月租金元"]]


# --- 591 -------------------------------------------------------------------

def rent591(city: str, town: str = "", pages: int = 1) -> list[dict[str, Any]]:
    """591 租屋。SSR 出來的卡片，img alt 裡剛好有「月租 xx,xxx 元/月」。"""
    rid = CITY_591.get(_norm(city))
    if not rid:
        raise ValueError(f"591 不支援的縣市：{city}")
    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        url = f"https://rent.591.com.tw/list?region={rid}&firstRow={(page - 1) * 30}"
        doc = fetch(url, referer="https://rent.591.com.tw/")
        for card in _cards(doc, "item", require="data-id="):
            hid = _f(r'data-id="(\d+)"', card)
            if not hid:
                continue
            alt = _f(r'alt="([^"]{10,300})"', card)
            t = _text(card)
            rent = _num(_f(r"月租\s*([\d,]+)\s*元", alt)) or _num(_f(r"([\d,]{4,})\s*元/月", t))
            ping = _num(_f(r"([\d.]+)\s*坪", t))
            addr = _f(r"\|([^|]*?[區鄉鎮市][^|]{0,20})\|?$", t) or _f(r"\|([^|]+區-[^|]+)\|", t)
            rows.append(_row(
                來源="591", 類型="租",
                標題=_f(r"\|([^|]{6,60})\|", t), 地址=addr,
                月租金元=rent, 坪數=ping,
                格局=_f(r"(\d+房\d*廳?\d*衛?)", t) or _f(r"(整層住家|獨立套房|分租套房|雅房)", t),
                樓層=_f(r"(\d+F/\d+F)", t),
                型態=_f(r"(整層住家|獨立套房|分租套房|雅房|店面|辦公|廠房|車位|土地)", t),
                連結=f"https://rent.591.com.tw/{hid}",
            ))
        time.sleep(1.5)
    seen, out = set(), []
    for r in rows:
        if r["連結"] not in seen and (r["月租金元"] or r["坪數"]):
            seen.add(r["連結"])
            out.append(r)
    return out


def sale591(city: str, town: str = "", pages: int = 1) -> list[dict[str, Any]]:
    """591 售屋。有 bot 防護，連續請求容易拿到空頁；失敗時走瀏覽器路徑。"""
    rid = CITY_591.get(_norm(city))
    if not rid:
        raise ValueError(f"591 不支援的縣市：{city}")
    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        doc = fetch(f"https://sale.591.com.tw/?regionid={rid}&firstRow={(page - 1) * 30}",
                    referer="https://sale.591.com.tw/")
        if len(doc) < 5000:
            raise RuntimeError("591 售屋回傳空頁（多半是被 bot 防護擋下）——改用瀏覽器抓取")
        for card in _cards(doc, "item"):
            hid = _f(r'href="[^"]*?/(\d{6,})"', card) or _f(r'data-id="(\d+)"', card)
            if not hid:
                continue
            t = _text(card)
            total = _num(_f(r"([\d,.]+)\s*萬", t))
            ping = _num(_f(r"([\d.]+)\s*坪", t))
            rows.append(_row(
                來源="591", 類型="售",
                標題=_f(r"\|([^|]{6,60})\|", t),
                地址=_f(r"\|([^|]*?[區鄉鎮市][^|]{0,20})\|", t),
                總價萬元=total, 坪數=ping,
                單價萬元每坪=round(total / ping, 2) if total and ping else None,
                格局=_f(r"(\d+房\d*廳?\d*衛?)", t),
                樓層=_f(r"(\d+F?/\d+F?)", t),
                屋齡=_num(_f(r"屋齡\s*([\d.]+)", t)),
                型態=_f(r"(電梯大樓|公寓|華廈|透天厝|別墅|套房|店面|辦公|廠辦|土地|車位)", t),
                連結=f"https://sale.591.com.tw/{hid}",
            ))
        time.sleep(2.0)
    return rows


# --- 永慶房屋 --------------------------------------------------------------

def yungching_buy(city: str, town: str = "", pages: int = 1) -> list[dict[str, Any]]:
    """永慶買屋。Angular SSR，卡片內有 caseName / address / regArea / floor 等 class。

    網址吃的是「台北市」不是「臺北市」——用正體「臺」會 404，所以這裡反向正規化。
    """
    yc = lambda s: (s or "").replace("臺", "台").strip()  # noqa: E731
    seg = urllib.parse.quote(f"{yc(city)}-{yc(town)}" if town else yc(city))
    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        suffix = f"?pg={page}" if page > 1 else ""
        doc = fetch(f"https://buy.yungching.com.tw/region/{seg}_c/{suffix}")
        for card in _cards(doc, "search-result-list-item"):
            name = _f(r'class="caseName"[^>]*>([^<]+)<', card)
            if not name:
                continue
            total = _num(_f(r'class="price"[^>]*>([\d,.]+)<', card))   # 萬元
            ping = _num(_f(r'class="regArea"[^>]*>建坪([\d.]+)<', card))
            link = _f(r'href="((?:house|sale)/[^"]+)"', card)
            rows.append(_row(
                來源="永慶房屋", 類型="售",
                標題=name,
                地址=_f(r'class="address"[^>]*>([^<]+)<', card),
                社區=_f(r'class="community"[^>]*>\s*([^<]+?)\s*<', card),
                總價萬元=total, 坪數=ping,
                單價萬元每坪=round(total / ping, 2) if total and ping else None,
                格局=_f(r'class="room"[^>]*>([^<]+)<', card),
                樓層=_f(r'class="floor"[^>]*>([^<]+)<', card),
                屋齡=_num(_f(r">([\d.]+)年<", card)),
                型態=_f(r'class="caseType"[^>]*>([^<]+)<', card),
                連結=f"https://buy.yungching.com.tw/{link}" if link else "",
            ))
        time.sleep(1.5)
    return rows


SITES: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "sinyi_buy": sinyi_buy,
    "sinyi_rent": sinyi_rent,
    "rent591": rent591,
    "sale591": sale591,
    "yungching_buy": yungching_buy,
}


def collect(city: str, town: str = "", sites: list[str] | None = None,
            pages: int = 1, strict_town: bool = True) -> tuple[list[dict[str, Any]], list[str]]:
    """抓多個站，回 (資料, 失敗訊息)。單站失敗不影響其他站。

    有些站（信義）的網址帶了行政區但回傳仍是全市，所以這裡用地址再篩一次；
    地址讀不到的列會保留，交給 nearby.py 用經緯度半徑決定。
    """
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for name in (sites or list(SITES)):
        fn = SITES.get(name)
        if fn is None:
            errors.append(f"{name}: 未知來源")
            continue
        try:
            got = fn(city, town, pages)
            if town and strict_town:
                t = _norm(town)
                got = [r for r in got if not r["地址"] or t in _norm(r["地址"])]
            rows.extend(got)
            print(f"  {name}: {len(got)} 筆", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            print(f"  {name}: 失敗 —— {exc}", file=sys.stderr)
    return rows, errors


def _write(path: str, text: str) -> None:
    """寫檔並自動建立上層目錄 —— 使用者照著 README 打 `--json out/x.json`
    時，out/ 通常還不存在，不該因此炸掉。"""
    p = Path(path)
    if p.parent != Path(""):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="房仲網站現售/租賃開價抓取")
    ap.add_argument("--city", required=True)
    ap.add_argument("--town", default="")
    ap.add_argument("--site", action="append", choices=list(SITES), help="可重複；預設全部")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--json", help="輸出檔")
    args = ap.parse_args()

    sites = None if (args.all or not args.site) else args.site
    rows, errors = collect(args.city, args.town, sites, args.pages)
    payload = {"查詢": {"city": args.city, "town": args.town}, "筆數": len(rows),
               "資料": rows, "失敗": errors}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json:
        _write(args.json, text)
        print(f"{len(rows)} 筆 → {args.json}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
