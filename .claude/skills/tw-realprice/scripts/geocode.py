#!/usr/bin/env python3
"""地址 → 座標。可插拔的 provider，設定檔決定用哪一個。

provider 依序嘗試（可在設定檔用 "providers" 指定順序或只留一個）：

  google     Google Geocoding API（需 GOOGLE_MAPS_API_KEY）。**一般使用者的主力**，
             門牌級精度、涵蓋全台、不用自己架服務。要付費，但有免費額度。
  locus      自架的 Locus Geo API（`POST /resolve`，帶 X-API-Key）。作者自架的服務，
             沒有這台服務的人用不到 —— 沒設定就會自動跳過，改用 google。
  custom     其他任何自架服務。用設定檔描述 URL 樣板與回應取值路徑，不用改程式。
  tgos       官方 TGOS 全國門牌地址定位服務（需 appId / apiKey）。
  lvr        用內政部實價登錄自己的資料反推：查同路段的成交案件，取門牌號最接近的
             那一筆的座標。零設定、可離線於官方 API，精度到門牌附近幾十公尺。
  nominatim  OpenStreetMap。台灣只到「路/段」層級，查不到門牌號，當最後手段。

設定檔位置（先找到的先用）：
  $TWRP_CONFIG
  ./.claude/skills/tw-realprice/config.json
  ~/.config/tw-realprice/config.json

custom provider 的設定長這樣：

  {
    "providers": ["custom", "lvr", "nominatim"],
    "custom": {
      "url": "https://my-tgos.example.com/geocode?addr={address}",
      "method": "GET",
      "headers": {"Authorization": "Bearer xxx"},
      "lat_path": "data.0.lat",
      "lon_path": "data.0.lon",
      "score_path": "data.0.matchScore"
    }
  }

`{address}` 會被 URL-encode 後代入。POST 的話再加 "body": "{\"addr\":\"{address}\"}"。
lat_path / lon_path 用點號走 JSON，數字代表 list 索引。

用法：
    python3 geocode.py "臺北市大安區忠孝東路四段45號"
    python3 geocode.py --provider lvr "臺北市大安區信義路四段30巷23號"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


class GeocodeError(RuntimeError):
    pass


# --- 設定 -----------------------------------------------------------------

def load_config() -> dict[str, Any]:
    for p in [os.environ.get("TWRP_CONFIG"),
              SKILL_DIR / "config.json",
              Path.home() / ".config" / "tw-realprice" / "config.json"]:
        if p and Path(p).is_file():
            try:
                return json.loads(Path(p).read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise GeocodeError(f"設定檔 {p} 不是合法 JSON: {exc}") from exc
    return {}


def _dotenv(name: str) -> str:
    """從 .env 檔讀一個變數。

    需要這層是因為：Claude / 腳本跑在**非互動 shell**，zsh 只會 source `~/.zshenv`，
    不會 source `~/.zshrc` —— 金鑰寫在 `.zshrc` 裡的話，手動跑得起來、自動跑卻 401。
    """
    for p in [SKILL_DIR / ".env", Path.home() / ".config" / "tw-realprice" / ".env"]:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip("'\"")
    return ""


def resolve_secret(cfg_block: dict[str, Any], env_key: str = "api_key_env",
                   inline_key: str = "api_key", default_env: str = "") -> tuple[str, str]:
    """依序找金鑰：環境變數 → .env 檔 → 設定檔。回 (金鑰, 來源說明)。"""
    name = cfg_block.get(env_key, default_env)
    if name and os.environ.get(name):
        return os.environ[name], f"環境變數 {name}"
    if name:
        val = _dotenv(name)
        if val:
            return val, f".env 的 {name}"
    if cfg_block.get(inline_key):
        return str(cfg_block[inline_key]), "config.json"
    return "", "（找不到金鑰）"


def _dig(obj: Any, path: str) -> Any:
    """用 "data.0.lat" 這種路徑取值。"""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _http(url: str, method: str = "GET", headers: dict[str, str] | None = None,
          body: str | None = None, timeout: int = 30) -> str:
    ctx = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    req = urllib.request.Request(
        url, method=method,
        data=body.encode("utf-8") if body else None,
        headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")


# --- 地址解析 --------------------------------------------------------------

def parse_address(addr: str) -> dict[str, str]:
    """把中文地址拆成 縣市/鄉鎮市區/路名/段/巷/弄/號。解析不到的就留空。"""
    a = (addr or "").replace("台", "臺").strip()
    a = re.sub(r"^\d{3,6}\s*", "", a)  # 去郵遞區號
    out = {"city": "", "town": "", "road": "", "section": "", "lane": "",
           "alley": "", "number": "", "rest": a}
    m = re.match(r"^(.{2,3}[市縣])", a)
    if m:
        out["city"] = m.group(1)
        a = a[m.end():]
    m = re.match(r"^(.{1,4}?[區鄉鎮市])", a)
    if m:
        out["town"] = m.group(1)
        a = a[m.end():]
    m = re.match(r"^(.+?[路街道大道])", a)
    if m:
        out["road"] = m.group(1)
        a = a[m.end():]
    m = re.match(r"^([一二三四五六七八九十\d]+)段", a)
    if m:
        out["section"] = m.group(1)
        a = a[m.end():]
    m = re.search(r"(\d+)\s*巷", a)
    if m:
        out["lane"] = m.group(1)
    m = re.search(r"(\d+)\s*弄", a)
    if m:
        out["alley"] = m.group(1)
    m = re.search(r"(\d+)(?:\s*-\s*\d+)?\s*號", a)
    if m:
        out["number"] = m.group(1)
    return out


def _cn2int(s: str) -> int | None:
    """「四」→4、「十九」→19。實登資料的段/樓層是國字。"""
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    if not s or any(c not in CN_DIGITS for c in s):
        return None
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + CN_DIGITS[s[1]]
    if len(s) == 1:
        return CN_DIGITS[s]
    if len(s) == 3 and s[1] == "十":
        return CN_DIGITS[s[0]] * 10 + CN_DIGITS[s[2]]
    if len(s) == 2 and s[1] == "十":
        return CN_DIGITS[s[0]] * 10
    return None


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """兩點距離（公尺）。"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --- providers -------------------------------------------------------------

# 台灣本島 + 離島的合理座標範圍，用來確認抓到的是 WGS84 而不是 TWD97 之類的投影座標
TW_LAT = (21.5, 26.5)
TW_LON = (118.0, 122.5)

_LAT_KEYS = ("lat", "latitude", "y", "wgs84_lat", "lat_wgs84")
_LON_KEYS = ("lon", "lng", "long", "longitude", "x", "wgs84_lon", "lon_wgs84")
_HINT_KEYS = ("match", "match_type", "matchtype", "score", "level", "precision",
              "source", "provider", "confidence", "quality", "matched_address",
              "formatted_address", "full_address", "formatted", "found", "place_id")


def _find_latlon(obj: Any) -> tuple[float, float, dict[str, Any]] | None:
    """在任意 JSON 結構裡找出第一組看起來像 WGS84 座標的值。

    服務端的欄位名稱不保證（lat/latitude/y…），而且 OpenAPI 沒宣告 response schema，
    所以與其寫死路徑，不如照「鍵名 + 值域」去認 —— 值域檢查同時擋掉 TWD97
    （那種 x≈300000、y≈2700000 的數字不會落在台灣的經緯度範圍裡）。
    """
    stack: list[Any] = [obj]
    while stack:
        cur = stack.pop(0)
        if isinstance(cur, dict):
            lower = {str(k).lower(): k for k in cur}
            lat_k = next((lower[k] for k in _LAT_KEYS if k in lower), None)
            lon_k = next((lower[k] for k in _LON_KEYS if k in lower), None)
            if lat_k and lon_k:
                try:
                    lat, lon = float(cur[lat_k]), float(cur[lon_k])
                except (TypeError, ValueError):
                    lat = lon = None  # type: ignore[assignment]
                if lat is not None and lon is not None:
                    if TW_LAT[0] <= lat <= TW_LAT[1] and TW_LON[0] <= lon <= TW_LON[1]:
                        return lat, lon, cur
                    # 有些服務把 x/y 反過來放
                    if TW_LAT[0] <= lon <= TW_LAT[1] and TW_LON[0] <= lat <= TW_LON[1]:
                        return lon, lat, cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _locus(addr: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """自架的 Locus Geo API：`POST /resolve`，body `{"items":[{"id","address"}]}`。

    金鑰優先讀環境變數（預設 `LOCUS_API_KEY`），沒有才讀設定檔的 `api_key` ——
    這樣金鑰可以不用落在檔案裡。
    """
    c = cfg.get("locus") or {}
    base = (c.get("base") or "").rstrip("/")
    if not base:
        return None
    key, _ = resolve_secret(c, default_env="LOCUS_API_KEY")
    headers = {"Content-Type": "application/json"}
    if key:
        headers[c.get("api_key_header", "X-API-Key")] = key
    headers.update(c.get("headers") or {})

    body = json.dumps({"items": [{"id": "q", "address": addr}]}, ensure_ascii=False)
    raw = _http(f"{base}{c.get('path', '/resolve')}", "POST", headers, body,
                int(c.get("timeout", 30)))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeocodeError(f"locus 回的不是 JSON: {raw[:200]}") from exc

    # 已知回應形狀：
    #   {"results":[{"id","found","precision","lat","lon","confidence","source",
    #                "place_id","formatted"}]}
    # 先照這個形狀讀；讀不到才退回通用偵測，這樣服務改版也不會直接壞掉。
    first = (data.get("results") or [{}])[0] if isinstance(data, dict) else {}
    if isinstance(first, dict) and first.get("found") is False:
        raise GeocodeError(
            f"locus 找不到「{addr}」"
            f"{'（' + str(first.get('reason')) + '）' if first.get('reason') else ''}")

    got = _find_latlon(data)
    if not got:
        raise GeocodeError(
            f"locus 沒有回傳有效的台灣 WGS84 座標: {json.dumps(data, ensure_ascii=False)[:300]}")
    lat, lon, node = got

    # precision 是服務端的比對層級（house / street / …），直接沿用比自己猜準
    level = str(node.get("precision") or first.get("precision") or "").strip()
    conf = str(node.get("confidence") or first.get("confidence") or "").strip()
    src = str(node.get("source") or first.get("source") or "").strip()
    level_zh = {"house": "門牌", "street": "路段", "road": "路段",
                "district": "行政區", "city": "縣市"}.get(level, level or "門牌")
    desc = level_zh + (f" / {conf}" if conf else "") + (f" / {src}" if src else "")
    return {
        "lat": lat, "lon": lon, "provider": "locus",
        "precision": desc,
        "score": conf or None,
        "matched": node.get("formatted") or first.get("formatted") or "",
        "hints": {k: node[k] for k in node
                  if str(k).lower() in _HINT_KEYS and node[k] not in (None, "")},
    }


def _google(addr: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Google Geocoding API。沒有金鑰就回 None，讓鏈路往下走（不會產生費用）。

    這是「別人 clone 這個 repo 之後的預設主力」—— locus 是作者自架的，一般人沒有。
    """
    g = cfg.get("google") or {}
    if g.get("enabled") is False:
        return None
    key, _ = resolve_secret(g, default_env="GOOGLE_MAPS_API_KEY")
    if not key:
        return None

    qs = urllib.parse.urlencode({
        "address": addr,
        "key": key,
        # 限定台灣並用中文回，避免同名地址跑到別的國家
        "components": "country:TW",
        "region": g.get("region", "tw"),
        "language": g.get("language", "zh-TW"),
    })
    url = g.get("url", "https://maps.googleapis.com/maps/api/geocode/json")
    data = json.loads(_http(f"{url}?{qs}", timeout=int(g.get("timeout", 30))))

    status = data.get("status")
    if status == "ZERO_RESULTS":
        return None                      # 查無此址，安靜往下一個 provider
    if status != "OK":
        # REQUEST_DENIED 幾乎都是「Geocoding API 沒啟用」或「帳單沒開」，
        # OVER_QUERY_LIMIT 是超額 —— 這兩個要講清楚，不然使用者只看到「定位失敗」
        raise GeocodeError(
            f"Google Geocoding 回 {status}"
            f"{'：' + data['error_message'] if data.get('error_message') else ''}")

    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    loc = (r.get("geometry") or {}).get("location") or {}
    try:
        lat, lon = float(loc["lat"]), float(loc["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (TW_LAT[0] <= lat <= TW_LAT[1] and TW_LON[0] <= lon <= TW_LON[1]):
        raise GeocodeError(f"Google 回的座標不在台灣範圍內：{lat}, {lon}")

    # location_type 就是 Google 的比對層級，直接沿用
    lt = (r.get("geometry") or {}).get("location_type", "")
    level_zh = {
        "ROOFTOP": "門牌",
        "RANGE_INTERPOLATED": "門牌（號碼內插推算）",
        "GEOMETRIC_CENTER": "路段中心",
        "APPROXIMATE": "概略位置",
    }.get(lt, lt or "未知")
    partial = bool(r.get("partial_match"))
    if partial:
        level_zh += "／部分比對"
    return {
        "lat": lat, "lon": lon, "provider": "google",
        "precision": f"{level_zh} / google",
        "score": lt or None,
        "matched": r.get("formatted_address") or "",
        "hints": {"location_type": lt, "partial_match": partial,
                  "types": r.get("types") or [], "place_id": r.get("place_id")},
    }


def _custom(addr: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    c = cfg.get("custom") or {}
    if not c.get("url"):
        return None
    enc = urllib.parse.quote(addr)
    url = c["url"].replace("{address}", enc)
    body = c.get("body")
    if body:
        body = body.replace("{address}", addr.replace('"', '\\"'))
    raw = _http(url, c.get("method", "GET"), c.get("headers"), body,
                int(c.get("timeout", 30)))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeocodeError(f"custom provider 回的不是 JSON: {raw[:200]}") from exc
    lat = _dig(data, c.get("lat_path", "lat"))
    lon = _dig(data, c.get("lon_path", "lon"))
    if lat is None or lon is None:
        return None
    return {"lat": float(lat), "lon": float(lon), "provider": "custom",
            "precision": "門牌",
            "score": _dig(data, c["score_path"]) if c.get("score_path") else None,
            "raw": data}


def _tgos(addr: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """官方 TGOS 全國門牌地址定位服務。需要在設定檔給 appId / apiKey。"""
    t = cfg.get("tgos") or {}
    if not (t.get("appId") and t.get("apiKey")):
        return None
    url = (t.get("url") or "https://addr.tgos.tw/addrservice/AddressLocate")
    qs = urllib.parse.urlencode({
        "oAPPId": t["appId"], "oAPIKey": t["apiKey"],
        "oAddress": addr, "oSRS": t.get("srs", "EPSG:4326"),
        "oFuzzyType": t.get("fuzzyType", "2"), "oResultDataType": "JSON",
        "oFuzzyBuffer": "0", "oIsOnlyFullMatch": "false",
        "oIsLockCounty": "false", "oIsLockTown": "false",
        "oIsSameNumber_SubNumber": "true", "oCanIgnoreVillage": "true",
        "oCanIgnoreNeighborhood": "true", "oReturnMaxCount": "1",
    })
    data = json.loads(_http(f"{url}?{qs}"))
    items = data.get("AddressList") or []
    if not items:
        return None
    it = items[0]
    return {"lat": float(it["Y"]), "lon": float(it["X"]), "provider": "tgos",
            "precision": "門牌", "score": it.get("MATCH_TYPE"), "raw": it}


def _lvr(addr: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """用實價登錄自己的資料反推座標。

    同一條路上的成交案件本身就帶座標，找門牌號最接近的那一筆即可。
    好處是完全不需要另一組 API key，而且座標系統跟後面要比對的資料完全一致。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lvr import LVRClient  # noqa: PLC0415

    p = parse_address(addr)
    if not (p["city"] and p["town"] and p["road"]):
        return None
    c = LVRClient()
    city, town = c.resolve(p["city"], p["town"])
    years = cfg.get("lvr_geocode_years", 3)
    end_y = int(cfg.get("lvr_geocode_end_year") or _roc_year())
    rows: list[dict[str, Any]] = []
    for qt in ("biz", "rent"):
        try:
            rows += c.query(qry_type=qt, city=city, town=town,
                            start=f"{end_y - years}/1", end=f"{end_y}/12",
                            doorno=p["road"])
        except Exception:  # noqa: BLE001, S110  單一查詢失敗就換下一種
            pass
        if rows:
            break
    if not rows:
        return None

    want_sec = _cn2int(p["section"])
    want_no = int(p["number"]) if p["number"].isdigit() else None
    best, best_cost = None, 1e18
    for r in rows:
        a = str(r.get("a") or "")
        a = a.split("#", 1)[-1]
        if p["road"] not in a:
            continue
        got_sec = _cn2int(_f1(r"([一二三四五六七八九十]+)段", a))
        if want_sec and got_sec and want_sec != got_sec:
            continue
        got_lane = _f1(r"(\d+)巷", a)
        got_no = _f1(r"(\d+)號", a)
        cost = 0.0
        if want_no and got_no:
            cost += abs(int(got_no) - want_no)
        else:
            cost += 500
        if p["lane"] and got_lane:
            cost += 0 if p["lane"] == got_lane else 200
        elif p["lane"] != bool(got_lane):
            cost += 50
        if r.get("lat") and cost < best_cost:
            best, best_cost = r, cost
    if not best:
        return None
    return {"lat": float(best["lat"]), "lon": float(best["lon"]), "provider": "lvr",
            "precision": "門牌附近（同路段最近成交案件）",
            "score": round(best_cost, 1),
            "matched": str(best.get("a") or "").split("#")[-1]}


def _f1(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    return m.group(1) if m else ""


def _roc_year() -> int:
    import datetime
    return datetime.date.today().year - 1911


def _nominatim(addr: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """OSM。台灣只查得到「路/段」，門牌號查不到，所以先把號拿掉再查。"""
    p = parse_address(addr)
    q = f"{p['road']}{p['section']}段, {p['town']}, {p['city']}" if p["road"] else addr
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"format": "jsonv2", "limit": 1, "countrycodes": "tw", "q": q})
    ua = cfg.get("nominatim_user_agent", "tw-realprice-skill/1.0")
    data = json.loads(_http(url, headers={"User-Agent": ua}))
    if not data:
        return None
    return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"]),
            "provider": "nominatim", "precision": "路段（誤差可達數百公尺）",
            "score": None, "matched": data[0].get("display_name", "")}


PROVIDERS = {"locus": _locus, "google": _google, "custom": _custom, "tgos": _tgos,
             "lvr": _lvr, "nominatim": _nominatim}
# locus 排最前面只是「有就用」—— 沒設定會直接回 None，一般使用者實際上是從 google 開始。
DEFAULT_ORDER = ["locus", "google", "custom", "tgos", "lvr", "nominatim"]


def geocode(addr: str, provider: str = "", cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """回 {lat, lon, provider, precision, ...}。全部失敗就丟 GeocodeError。"""
    cfg = cfg if cfg is not None else load_config()
    order = [provider] if provider else (cfg.get("providers") or DEFAULT_ORDER)
    tried: list[str] = []
    for name in order:
        fn = PROVIDERS.get(name)
        if fn is None:
            continue
        try:
            got = fn(addr, cfg)
        except Exception as exc:  # noqa: BLE001
            tried.append(f"{name}({exc})")
            continue
        if got:
            got["address"] = addr
            got["parsed"] = parse_address(addr)
            return got
        tried.append(f"{name}(無結果)")
    raise GeocodeError(f"「{addr}」定位失敗；嘗試過：{', '.join(tried) or '無可用 provider'}")


def probe_locus(addr: str, cfg: dict[str, Any]) -> int:
    """把 Locus /resolve 的原始回應原封不動印出來，用來確認欄位形狀與金鑰是否有效。"""
    c = cfg.get("locus") or {}
    base = (c.get("base") or "").rstrip("/")
    if not base:
        print("錯誤：config.json 沒有設定 locus.base", file=sys.stderr)
        return 1
    key, src = resolve_secret(c, default_env="LOCUS_API_KEY")
    print(f"endpoint : {base}{c.get('path', '/resolve')}", file=sys.stderr)
    print(f"金鑰來源 : {src}{f'（長度 {len(key)}）' if key else ''}", file=sys.stderr)
    if not key:
        print("提示 : 非互動 shell 讀不到 ~/.zshrc。把 export 移到 ~/.zshenv，"
              f"或建 {SKILL_DIR / '.env'}", file=sys.stderr)
    headers = {"Content-Type": "application/json"}
    if key:
        headers[c.get("api_key_header", "X-API-Key")] = key
    headers.update(c.get("headers") or {})
    body = json.dumps({"items": [{"id": "q", "address": addr}]}, ensure_ascii=False)
    try:
        raw = _http(f"{base}{c.get('path', '/resolve')}", "POST", headers, body,
                    int(c.get("timeout", 30)))
    except Exception as exc:  # noqa: BLE001
        print(f"請求失敗：{exc}", file=sys.stderr)
        return 1
    try:
        data = json.loads(raw)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(raw)
        return 1
    found = _find_latlon(data)
    if found:
        lat, lon, _ = found
        print(f"\n偵測到座標：{lat:.6f}, {lon:.6f}", file=sys.stderr)
    else:
        print("\n偵測不到台灣範圍內的 WGS84 座標 —— 檢查回應欄位或座標系統。", file=sys.stderr)
    return 0


def doctor(cfg: dict[str, Any]) -> int:
    """列出每個 provider 的設定狀態，讓人一眼看出「為什麼用到備援」。"""
    order = cfg.get("providers") or DEFAULT_ORDER
    print(f"設定檔     : {_config_path() or '（找不到，使用預設值）'}")
    print(f"嘗試順序   : {' → '.join(order)}\n")
    rows: list[tuple[str, str, str]] = []
    for name in order:
        c = cfg.get(name) or {}
        if name == "locus":
            key, src = resolve_secret(c, default_env="LOCUS_API_KEY")
            ok = bool(c.get("base") and key)
            rows.append((name, "可用" if ok else "跳過",
                         f"base={c.get('base') or '未設'}, 金鑰={src}"))
        elif name == "google":
            key, src = resolve_secret(c, default_env="GOOGLE_MAPS_API_KEY")
            off = c.get("enabled") is False
            rows.append((name, "停用" if off else ("可用" if key else "跳過"),
                         f"金鑰={src}" + ("（enabled=false）" if off else "")))
        elif name == "custom":
            rows.append((name, "可用" if c.get("url") else "跳過",
                         f"url={c.get('url') or '未設'}"))
        elif name == "tgos":
            ok = bool(c.get("appId") and c.get("apiKey"))
            rows.append((name, "可用" if ok else "跳過", "需要 appId + apiKey"))
        elif name == "lvr":
            rows.append((name, "可用", "零設定備援（拿同路段實登成交案件反推）"))
        elif name == "nominatim":
            rows.append((name, "可用", "只到路段層級，誤差可達數百公尺"))
        else:
            rows.append((name, "未知", "不是有效的 provider 名稱"))
    mark = {"可用": "✓", "跳過": "－", "停用": "✗", "未知": "?"}
    for name, state, note in rows:
        print(f"  {mark.get(state, '?')} {name:<10} {state:<4} {note}")
    live = [n for n, s, _ in rows if s == "可用"]
    print(f"\n實際會用到：{live[0] if live else '（無）'}"
          f"{'（失敗時往後退）' if len(live) > 1 else ''}")
    return 0


def _config_path() -> str:
    for p in [os.environ.get("TWRP_CONFIG"),
              SKILL_DIR / "config.json",
              Path.home() / ".config" / "tw-realprice" / "config.json"]:
        if p and Path(p).is_file():
            return str(p)
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="台灣地址定位")
    ap.add_argument("address", nargs="?", default="")
    ap.add_argument("--provider", default="", choices=[""] + list(PROVIDERS))
    ap.add_argument("--parse-only", action="store_true", help="只做地址拆解不定位")
    ap.add_argument("--probe-locus", action="store_true",
                    help="印出 Locus /resolve 的原始回應（確認金鑰與欄位形狀）")
    ap.add_argument("--doctor", action="store_true",
                    help="列出每個 provider 的設定狀態，不發送任何請求")
    args = ap.parse_args()
    cfg = load_config()
    if args.doctor:
        return doctor(cfg)
    if not args.address:
        ap.error("需要地址（或用 --doctor）")
    if args.parse_only:
        print(json.dumps(parse_address(args.address), ensure_ascii=False, indent=2))
        return 0
    if args.probe_locus:
        return probe_locus(args.address, cfg)
    try:
        print(json.dumps(geocode(args.address, args.provider, cfg),
                         ensure_ascii=False, indent=2))
    except GeocodeError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
