#!/usr/bin/env python3
"""內政部不動產交易實價查詢服務網 (lvr.land.moi.gov.tw) 即時查詢 client。

官網的查詢 API 把條件 JSON 用 CryptoJS AES 加密後放進 query string：

    GET /SERVICE/QueryPrice/{md5(json)}?q={base64(cryptojs_aes(json, "lvr.land.moi.gov.tw"))}

passphrase 就是 `window.location.host`。呼叫前要先跟 /jsp/setToken.jsp 拿一次性 token
並帶著同一個 JSESSIONID。回傳的每一筆都帶 lat/lon，所以半徑搜尋不需要另外 geocode 成交案件。

用法：
    python3 lvr.py --city A --town A02 --type biz --start 114/1 --end 115/7
    python3 lvr.py --city A --town A02 --type rent --json out.json
    python3 lvr.py --list-towns A
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.cookiejar
import json
import ssl
import sys
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twcrypto import cryptojs_encrypt  # noqa: E402


def tw_gov_ssl_context() -> ssl.SSLContext:
    """政府網站的憑證鏈缺 Subject Key Identifier，會被 Python 3.13+ 預設開啟的
    VERIFY_X509_STRICT 擋下（curl 不會，因為它沒開這個旗標）。這裡只關掉 RFC 5280
    的嚴格檢查，憑證鏈與主機名稱驗證照常執行。"""
    ctx = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx

HOST = "lvr.land.moi.gov.tw"
BASE = f"https://{HOST}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# qryType
BIZ = "biz"              # 不動產買賣
RENT = "rent"            # 不動產租賃
PRESALE = "sale"         # 預售屋買賣
PRESALE_CASE = "saleRemark"  # 預售屋建案備查

# 交易標的 (ptype) —— 買賣/預售用
PTYPE = {"1": "房地", "2": "房地(含車位)", "3": "土地", "4": "建物", "5": "車位"}
# 建物型態 (ftype) —— 買賣用
FTYPE_BIZ = {
    "01": "公寓", "02": "透天厝", "03": "店面(店鋪)", "04": "辦公大樓", "05": "住宅大樓",
    "06": "華廈", "07": "套房", "08": "工廠", "09": "廠辦", "10": "農舍", "11": "倉庫",
}
# 建物型態 (ftype) —— 租賃用（多兩個代碼）
FTYPE_RENT = dict(FTYPE_BIZ, **{"04": "商辦大樓", "01": "公寓(無電梯)", "12": "其他", "L": "土地", "P": "車位"})
# 出租型態 (rent_type)
RENT_TYPE = {"1": "整棟(戶)出租", "2": "分層出租", "3": "獨立套房", "4": "分租套房", "5": "分租雅房"}
# 租賃附加條件 (rent_order)
RENT_ORDER = {"01": "含車位", "02": "電梯", "03": "附屬設備", "04": "管理員", "05": "管理組織",
              "06": "包租代管服務"}


class LVRError(RuntimeError):
    pass


class LVRClient:
    """維持 JSESSIONID 的查詢 client。一個 instance 可重複查多次。"""

    def __init__(self, timeout: int = 60, delay: float = 0.8):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=tw_gov_ssl_context()))
        self.timeout = timeout
        self.delay = delay
        self._primed = False

    # -- 低階 ------------------------------------------------------------
    def _get(self, url: str, referer: str = BASE + "/") -> bytes:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-TW,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        })
        with self.opener.open(req, timeout=self.timeout) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        return raw

    def _prime(self) -> None:
        """先打首頁拿 JSESSIONID。"""
        if not self._primed:
            self._get(BASE + "/")
            self._primed = True

    def _token(self) -> str:
        self._prime()
        raw = self._get(BASE + "/jsp/setToken.jsp").decode("utf-8", "replace").strip()
        try:
            tok = json.loads(raw)["token"]
        except Exception as exc:  # noqa: BLE001
            raise LVRError(f"取 token 失敗: {raw[:200]}") from exc
        if tok == "401":
            raise LVRError("token 回 401，session 失效")
        return tok

    # -- 參考資料 --------------------------------------------------------
    def cities(self) -> list[dict[str, Any]]:
        """全部縣市代碼。"""
        self._prime()
        return json.loads(self._get(f"{BASE}/SERVICE/CITY").decode("utf-8"))

    def towns(self, city_code: str) -> list[dict[str, Any]]:
        """某縣市的鄉鎮市區代碼。"""
        self._prime()
        return json.loads(self._get(f"{BASE}/SERVICE/CITY/{city_code}").decode("utf-8"))

    def resolve(self, city_name: str, town_name: str | None = None) -> tuple[str, str]:
        """中文縣市/鄉鎮 → 代碼。'台北市' 與 '臺北市' 都收。"""
        norm = lambda s: (s or "").replace("台", "臺").strip()  # noqa: E731
        cities = self.cities()
        city = next((c for c in cities if norm(c["title"]) == norm(city_name)), None)
        if city is None:
            city = next((c for c in cities if norm(city_name).startswith(norm(c["title"]))), None)
        if city is None:
            raise LVRError(f"找不到縣市：{city_name}（可用：{[c['title'] for c in cities]}）")
        if not town_name:
            return city["code"], ""
        towns = self.towns(city["code"])
        town = next((t for t in towns if norm(t["title"]) == norm(town_name)), None)
        if town is None:
            raise LVRError(f"{city['title']} 找不到 {town_name}（可用：{[t['title'] for t in towns]}）")
        return city["code"], town["code"]

    # -- 查詢 ------------------------------------------------------------
    def query(
        self,
        qry_type: str = BIZ,
        city: str = "",
        town: str = "",
        start: str = "",
        end: str = "",
        ptype: str = "1,2,3,4,5",
        ftype: str = "",
        doorno: str = "",
        community: str = "",
        price_s: str = "", price_e: str = "",
        unit_price_s: str = "", unit_price_e: str = "",
        area_s: str = "", area_e: str = "",
        buildyear_s: str = "", buildyear_e: str = "",
        pattern: str = "",
        floor: str = "",
        rent_type: str = "",
        rent_order: str = "",
        purpose: str = "",
        extra: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """查一次。`start`/`end` 用民國 "114/1" 或 "1141" 格式。

        回傳原始欄位的 list；用 normalize() 轉成好懂的 schema。
        """
        sy, sm = _split_ym(start)
        ey, em = _split_ym(end)
        if qry_type == RENT and (not ptype or ptype == "1,2,3,4,5"):
            ptype = "1,2,4,6,7"  # 官網 #rent_ptype 的預設值
        token = self._token()

        # 欄位順序必須跟官網 JS 的 $.extend(defaults, dataObj) 一致 ——
        # URL path 是這串 JSON 的 md5，順序變了 md5 就對不上。
        payload: dict[str, str] = {
            "ptype": ptype, "starty": sy, "startm": sm, "endy": ey, "endm": em,
            "qryType": qry_type, "city": city, "town": town, "p_build": community,
            "ftype": ftype, "price_s": price_s, "price_e": price_e,
            "unit_price_s": unit_price_s, "unit_price_e": unit_price_e,
            "area_s": area_s, "area_e": area_e,
            "build_s": "", "build_e": "",
            "buildyear_s": buildyear_s, "buildyear_e": buildyear_e,
            "doorno": urllib.parse.quote(doorno), "pattern": pattern,
            "community": urllib.parse.quote(community),
            "floor": floor, "rent_type": rent_type, "rent_order": rent_order,
            "urban": "", "urbantext": "", "nurban": "", "aa12": "",
            "p_purpose": urllib.parse.quote(purpose),
            "p_unusual_yn": "N", "p_unusualcode": "", "QB41": "",
            "show_avg": "N", "tmoney_unit": "1", "pmoney_unit": "1", "unit": "2",
            "token": token,
        }
        if extra:
            payload.update(extra)

        js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.md5(js.encode("utf-8")).hexdigest()
        ct = cryptojs_encrypt(js, HOST)
        import base64
        q = base64.b64encode(ct.encode("utf-8")).decode("ascii")
        url = f"{BASE}/SERVICE/QueryPrice/{digest}?q={urllib.parse.quote(q)}"

        time.sleep(self.delay)
        raw = self._get(url).decode("utf-8", "replace")
        if raw.lstrip().startswith("<"):
            raise LVRError(f"查詢失敗（伺服器回 HTML）：{raw[:200]}")
        data = json.loads(raw)
        return data if isinstance(data, list) else []


def _split_ym(ym: str) -> tuple[str, str]:
    """"114/1" / "1141" / "114-01" → ("114", "1")。空字串回 ("", "")。"""
    if not ym:
        return "", ""
    s = ym.replace("-", "/").replace(".", "/")
    if "/" in s:
        y, m = s.split("/", 1)
    else:
        y, m = s[:3], s[3:]
    return y.strip(), str(int(m.strip() or 1))


# --- 正規化 ---------------------------------------------------------------

def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize(rec: dict[str, Any], qry_type: str = BIZ) -> dict[str, Any]:
    """把官網的單字母欄位轉成看得懂的 schema。

    欄位對應（實測 biz / rent / sale 三種回應歸納）：
      a  地址（買賣是 "補零版#正常版"，租賃/預售只有一段）   b   建物型態（完整敘述）
      bn 社區/建案名稱          bs  主建物佔比                bu  棟及號（預售）
      e  交易/租賃日期(民國)    tp  總價/總租金（元）          p   單價（元/坪，unit=2 時）
      s  面積（坪）             cp  車位總價（萬元）           t   交易標的
      f  樓層/總樓層            g   屋齡（年）                 v   格局「3房2廳2衛」
      j/k/l 交易筆棟數：土地筆數 / 建物棟數 / 車位個數（**不是**房廳衛，別搞混）
      pu 主要用途               ma  主要建材                   AA11 都市土地使用分區
      el 電梯  m 管理組織  ms 管理員  fn 附屬設備  rperiod 租期  rtype 出租型態
      note 備註                 lat/lon 座標                   sq  明細查詢加密 id
      commid 社區 id            reid 預售備查編號              city/town 代碼
    """
    addr = str(rec.get("a") or "")
    if "#" in addr:
        addr = addr.split("#", 1)[1] or addr.split("#", 1)[0]
    total = _num(rec.get("tp"))
    area = _num(rec.get("s"))
    unit_raw = _num(rec.get("p"))  # 元/坪
    if unit_raw is None and total and area:
        unit_raw = total / area
    out = {
        "來源": "內政部實價登錄",
        "類型": {"biz": "買賣", "rent": "租賃", "sale": "預售屋",
                 "saleRemark": "預售建案"}.get(qry_type, qry_type),
        "地址": addr,
        "社區": rec.get("bn") or "",
        "日期": rec.get("e") or "",
        "總價元": total,
        "面積坪": area,
        "建物型態": rec.get("b") or rec.get("t") or "",
        "交易標的": rec.get("t") or "",
        "樓層": rec.get("f") or "",
        "屋齡": _num(rec.get("g")),
        "格局": rec.get("v") or "",
        "土地筆數": _num(rec.get("j")), "建物棟數": _num(rec.get("k")),
        "車位個數": _num(rec.get("l")),
        "主要用途": rec.get("pu") or "",
        "主建物佔比": rec.get("bs") or "",
        "電梯": rec.get("el") or "",
        "管理組織": rec.get("m") or "",
        "車位總價萬元": _num(rec.get("cp")),
        "備註": rec.get("note") or "",
        "lat": rec.get("lat"), "lon": rec.get("lon"),
        "社區id": rec.get("commid") or "",
        "明細id": rec.get("sq") or "",
        "_raw": rec,
    }
    if qry_type == RENT:
        out["月租金元"] = total
        out["單價元每坪"] = round(unit_raw) if unit_raw else None
        out["租期"] = rec.get("rperiod") or ""
        out["出租型態"] = rec.get("rtype") or ""
        out["附屬設備"] = rec.get("fn") or ""
        out["管理員"] = rec.get("ms") or ""
    else:
        out["單價萬元每坪"] = round(unit_raw / 10000, 2) if unit_raw else None
        out["總價萬元"] = round(total / 10000, 1) if total else None
    return out


# --- CLI ------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="內政部實價登錄即時查詢")
    ap.add_argument("--city", help="縣市代碼(A) 或中文(臺北市)")
    ap.add_argument("--town", default="", help="鄉鎮市區代碼(A02) 或中文(大安區)")
    ap.add_argument("--type", default=BIZ, choices=[BIZ, RENT, PRESALE, PRESALE_CASE],
                    help="biz=買賣 rent=租賃 sale=預售屋 saleRemark=預售建案")
    ap.add_argument("--start", default="", help="民國起月，如 114/1")
    ap.add_argument("--end", default="", help="民國迄月，如 115/7")
    ap.add_argument("--ptype", default="1,2,3,4,5")
    ap.add_argument("--ftype", default="", help="建物型態代碼，逗號分隔（05=住宅大樓）")
    ap.add_argument("--doorno", default="", help="門牌關鍵字（路名）")
    ap.add_argument("--community", default="", help="社區/建案名稱")
    ap.add_argument("--json", help="輸出檔（預設 stdout）")
    ap.add_argument("--raw", action="store_true", help="輸出原始欄位不正規化")
    ap.add_argument("--limit", type=int, default=0, help="只輸出前 N 筆")
    ap.add_argument("--list-cities", action="store_true")
    ap.add_argument("--list-towns", metavar="CITY")
    args = ap.parse_args()

    c = LVRClient()
    if args.list_cities:
        print(json.dumps(c.cities(), ensure_ascii=False, indent=2))
        return 0
    if args.list_towns:
        code = args.list_towns
        if len(code) > 1:
            code, _ = c.resolve(code)
        print(json.dumps(c.towns(code), ensure_ascii=False, indent=2))
        return 0
    if not args.city:
        ap.error("需要 --city")

    city, town = args.city, args.town
    if not city[0].isalpha() or len(city) > 1:
        city, town = c.resolve(args.city, args.town)
    elif town and len(town) > 3:
        _, town = c.resolve(args.city, args.town)

    rows = c.query(qry_type=args.type, city=city, town=town, start=args.start,
                   end=args.end, ptype=args.ptype, ftype=args.ftype,
                   doorno=args.doorno, community=args.community)
    out = rows if args.raw else [normalize(r, args.type) for r in rows]
    if args.limit:
        out = out[:args.limit]
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
        print(f"{len(out)} 筆 → {args.json}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
