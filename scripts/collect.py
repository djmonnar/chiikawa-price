#!/usr/bin/env python3
"""먼작귀(치이카와) 굿즈 최저가 수집기.

국내: 11번가 오픈API 상품검색
      SerpApi 네이버 통합검색의 쇼핑 칸 (무료 월 250회)
      Apify 쿠팡 검색 액터 (무료 월 $5 크레딧)
      (네이버쇼핑 검색 API는 2026-07-31 종료 — 키가 있어도 SE05 오류라 첫 실패 후 건너뜀)
일본: 라쿠텐 시장 API, Yahoo!쇼핑 API  → 엔화를 원화로 환산 + 배송비/배대지비 추정

SerpApi·Apify는 쓴 만큼 한도가 줄어서, 남은 한도를 다음 갱신일까지 남은 실행 횟수로 나눠
이번 실행에 몇 개 상품만 새로 조회합니다. 나머지 상품은 docs/data/cache.json 에 남은
지난 결과(기본 72시간 이내)를 그대로 씁니다.

결과는 docs/data/latest.json, docs/data/history.json 에 저장되고
최저가 갱신·목표가 도달 시 텔레그램으로 알림을 보냅니다.

환경변수 (GitHub Secrets):
  ELEVENST_API_KEY                       (국내, 11번가 오픈API 32자리 키 — 180일마다 갱신)
  SERPAPI_KEY                            (선택, 국내 네이버쇼핑 칸 — serpapi.com)
  APIFY_TOKEN                            (선택, 국내 쿠팡 — apify.com)
  NAVER_CLIENT_ID, NAVER_CLIENT_SECRET   (예전 네이버쇼핑용 — 종료됨, 없어도 됨)
  RAKUTEN_APP_ID, RAKUTEN_ACCESS_KEY     (선택, 라쿠텐)
  YAHOO_APP_ID                           (선택, 야후쇼핑)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (선택, 알림)

사용: python scripts/collect.py          # 실제 수집
      python scripts/collect.py --mock   # API 없이 샘플 데이터로 테스트
"""
import hashlib
import html
import json
import math
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config")
DATA = os.path.join(ROOT, "docs", "data")
CACHE = os.path.join(DATA, "cache.json")
KST = timezone(timedelta(hours=9))
UA = "chiikawa-price-bot/1.0"


# ---------------------------------------------------------------- utils
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def http_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def clean_title(t):
    return html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 환율
def get_fx(settings):
    try:
        d = http_json("https://open.er-api.com/v6/latest/JPY")
        rate = float(d["rates"]["KRW"])
        if 3 < rate < 30:
            return round(rate, 3), "open.er-api.com"
    except Exception as e:  # noqa: BLE001
        log("환율 조회 실패, 기본값 사용:", e)
    return settings["fx_fallback_jpy_krw"], "기본값(settings.json)"


# ---------------------------------------------------------------- 수집기
_SKIP = set()  # 이번 실행에서 더 부르지 않을 소스 (키 오류·서비스 종료)


def naver(query):
    cid, sec = os.getenv("NAVER_CLIENT_ID"), os.getenv("NAVER_CLIENT_SECRET")
    if not (cid and sec) or "naver" in _SKIP:
        return []
    out = []
    for sort in ("sim", "asc"):
        url = "https://openapi.naver.com/v1/search/shop.json?" + urllib.parse.urlencode(
            {"query": query, "display": 100, "sort": sort, "exclude": "used:rental"}
        )
        try:
            d = http_json(url, {"X-Naver-Client-Id": cid.strip(), "X-Naver-Client-Secret": sec.strip()})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if "SE05" in body:  # 2026-07-31 쇼핑 검색 API 종료 → 같은 오류만 반복되니 이번 실행은 그만 부름
                log("[네이버] 쇼핑 검색 API가 종료되어(SE05) 이번 수집에서는 건너뜁니다.")
                _SKIP.add("naver")
                return out
            log(f"[네이버] {query} 실패: HTTP {e.code}", body)
            continue
        except Exception as e:  # noqa: BLE001
            log(f"[네이버] {query} 실패:", e)
            continue
        for it in d.get("items", []):
            price = int(it.get("lprice") or 0)
            if price <= 0:
                continue
            out.append({
                "source": "naver",
                "region": "KR",
                "key": f"naver:{it.get('productId')}",
                "title": clean_title(it.get("title")),
                "mall": it.get("mallName") or "네이버",
                "price_orig": price,
                "currency": "KRW",
                "landed_krw": price,
                "ship_note": "배송비 판매처별 상이",
                "link": it.get("link"),
                "image": it.get("image"),
            })
        time.sleep(0.15)
    return out


def _num(text):
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


_11ST_ENC = []  # 검색어 인코딩: 결과가 나온 쪽(utf-8/cp949)을 찾으면 계속 사용


def _11st_call(key, query, sort, enc):
    url = "https://openapi.11st.co.kr/openapi/OpenApiService.tmall?" + urllib.parse.urlencode(
        {"key": key, "apiCode": "ProductSearch", "keyword": query.encode(enc, "ignore"),
         "pageNum": 1, "pageSize": 50, "sortCd": sort}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("cp949", "replace")
    return ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text))


def elevenst(query):
    """11번가 오픈API 상품검색. 응답은 EUC-KR XML이고, 오류도 HTTP 200 + <ErrorResponse>로 온다."""
    key = (os.getenv("ELEVENST_API_KEY") or "").strip()
    if not key or "11st" in _SKIP:
        return []
    out = []
    for sort in ("CP", "L"):  # 인기도순, 낮은가격순
        root = None
        for enc in list(_11ST_ENC or ["utf-8", "cp949"]):
            try:
                root = _11st_call(key, query, sort, enc)
            except Exception as e:  # noqa: BLE001
                log(f"[11번가] {query} 실패:", e)
                root = None
                break
            if root.tag == "ErrorResponse":
                code, msg = root.findtext("ErrorCode"), root.findtext("ErrorMessage")
                log(f"[11번가] {query} 실패: {code} {msg} {root.findtext('ErrorDetail') or ''}".strip())
                if code in ("003", "004", "005"):  # 키 미등록·일일 한도 초과·접근 거부 → 이번 실행 중단
                    _SKIP.add("11st")
                    return out
                root = None
                continue
            if root.find(".//Product") is not None:
                if not _11ST_ENC:
                    _11ST_ENC.append(enc)
                    log(f"[11번가] 검색어 인코딩 {enc} 사용")
                break
            time.sleep(0.3)
        if root is None:
            continue
        for it in root.iter("Product"):
            price = _num(it.findtext("ProductPrice"))
            sale = _num(it.findtext("SalePrice"))  # 할인모음가
            if 0 < sale <= price or (sale > 0 and not price):
                price = sale
            if price <= 0:
                continue
            ship = (it.findtext("Delivery") or "").strip()
            fee = _num(ship) if re.fullmatch(r"[\d,]+\s*원?", ship) else 0
            img = next((it.findtext(t) for t in ("ProductImage300", "ProductImage200", "ProductImage",
                                                 "ImageUrl", "ProductImage100") if it.findtext(t)), None)
            out.append({
                "source": "11st",
                "region": "KR",
                "key": f"11st:{it.findtext('ProductCode')}",
                "title": clean_title(it.findtext("ProductName")),
                "mall": (it.findtext("Seller") or "").strip() or "11번가",
                "price_orig": price,
                "currency": "KRW",
                "landed_krw": price + fee,
                "ship_note": (f"배송비 {fee:,}원 포함" if fee else ship) or "배송비 판매처별 상이",
                "link": it.findtext("DetailPageUrl") or it.findtext("ProductDetailUrl"),
                "image": img,
            })
        time.sleep(0.3)
    return out


_RAKUTEN_OK_VER = []  # 이번 실행에서 응답이 온 API 버전 (한 번 찾으면 계속 사용)


def rakuten(query, fx, s):
    app, key = os.getenv("RAKUTEN_APP_ID"), os.getenv("RAKUTEN_ACCESS_KEY")
    if not app:
        return []
    # 2026년 새 API는 앱 등록 때 넣은 '許可されたWebサイト'와 같은 주소의 Referer 헤더를 확인함
    site = s.get("rakuten_origin", "https://example.com").rstrip("/")
    headers = {"Origin": site, "Referer": site + "/"}
    out = []
    for sort in ("standard", "+itemPrice"):
        params = {"applicationId": app, "keyword": query, "hits": 30, "sort": sort,
                  "availability": 1, "format": "json", "formatVersion": 2}
        if key:
            params["accessKey"] = key
        d = None
        for ver in list(_RAKUTEN_OK_VER or s.get("rakuten_api_versions") or ["20260701", "20220601"]):
            url = f"https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/{ver}?" + urllib.parse.urlencode(params)
            try:
                d = http_json(url, headers)
            except urllib.error.HTTPError as e:
                log(f"[라쿠텐 {ver}] {query} 실패: HTTP {e.code}", e.read().decode("utf-8", "replace")[:200])
                d = None
            except Exception as e:  # noqa: BLE001
                log(f"[라쿠텐 {ver}] {query} 실패:", e)
                d = None
            if d is not None and isinstance(d.get("Items"), list):
                if not _RAKUTEN_OK_VER:
                    _RAKUTEN_OK_VER.append(ver)
                    log(f"[라쿠텐] API 버전 {ver} 사용")
                break
            if d is not None:
                log(f"[라쿠텐 {ver}] {query} 응답에 Items 없음:", str(d)[:200])
                d = None
            time.sleep(1.1)  # 초당 1회 제한
        if d is None:
            continue
        for it in d.get("Items", []):
            jpy = int(it.get("itemPrice") or 0)
            if jpy <= 0:
                continue
            free = it.get("postageFlag") == 0
            imgs = it.get("mediumImageUrls") or []
            img = imgs[0] if imgs and isinstance(imgs[0], str) else (imgs[0].get("imageUrl") if imgs else None)
            out.append(jp_offer("rakuten", f"rakuten:{it.get('itemCode')}", it.get("itemName"),
                                it.get("shopName"), jpy, free, it.get("itemUrl"), img, fx, s))
        time.sleep(1.1)  # 초당 1회 제한
    return out


def yahoo(query, fx, s):
    app = os.getenv("YAHOO_APP_ID")
    if not app:
        return []
    out = []
    for sort in ("-score", "+price"):
        url = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch?" + urllib.parse.urlencode(
            {"appid": app, "query": query, "results": 50, "sort": sort, "in_stock": "true", "condition": "new"}
        )
        try:
            d = http_json(url)
        except Exception as e:  # noqa: BLE001
            log(f"[야후] {query} 실패:", e)
            continue
        for it in d.get("hits", []):
            jpy = int(it.get("price") or 0)
            if jpy <= 0:
                continue
            free = "無料" in ((it.get("shipping") or {}).get("name") or "")
            out.append(jp_offer("yahoo", f"yahoo:{it.get('code')}", it.get("name"),
                                (it.get("seller") or {}).get("name"), jpy, free, it.get("url"),
                                (it.get("image") or {}).get("medium"), fx, s))
        time.sleep(1.1)
    return out


def jp_offer(source, key, title, mall, jpy, free_ship, link, image, fx, s):
    ship_jpy = 0 if free_ship else s["jp_domestic_shipping_jpy"]
    tax = s.get("jp_import_tax_rate", 0)
    landed = round((jpy + ship_jpy) * fx * (1 + tax) + s["jp_forwarding_krw_per_item"])
    note = ("일본 내 무료배송" if free_ship else f"일본 내 배송 약 ¥{ship_jpy}") + \
        f" + 배대지 약 {s['jp_forwarding_krw_per_item']:,}원" + (f" + 세금 {tax*100:.0f}%" if tax else "")
    return {
        "source": source, "region": "JP", "key": key, "title": clean_title(title),
        "mall": mall or source, "price_orig": jpy, "currency": "JPY",
        "landed_krw": landed, "ship_note": note, "link": link, "image": image,
    }


# ---------------------------------------------------------------- 한도가 있는 소스 (SerpApi·Apify)
def _amount(v):
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    return _num(str(v or ""))


def _parse_time(text):
    if not text:
        return None
    try:
        t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _runs_left(end, s):
    """한도 갱신 시점까지 남은 수집 횟수 (이번 실행 포함)."""
    now = datetime.now(timezone.utc)
    if not end or end <= now:  # 갱신일을 모르면 이번 달 말일까지로 계산
        end = (now.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return max(1, math.ceil((end - now).total_seconds() / 86400 * s.get("runs_per_day", 2)))


def _share(left_units, unit_per_group, end, s, n_groups):
    """남은 한도를 남은 실행 횟수로 나눠, 이번에 새로 조회할 상품 수를 정한다."""
    if left_units <= 0 or unit_per_group <= 0:
        return 0, _runs_left(end, s)
    runs = _runs_left(end, s)
    n = round(left_units / unit_per_group / runs)
    return max(0, min(n, int(left_units // unit_per_group), n_groups)), runs


def serpapi_quota(s, n_groups):
    key = (os.getenv("SERPAPI_KEY") or "").strip()
    if not key:
        return 0
    try:  # 계정 조회는 검색 횟수에 포함되지 않음
        d = http_json("https://serpapi.com/account.json?" + urllib.parse.urlencode({"api_key": key}))
    except urllib.error.HTTPError as e:
        log(f"[SerpApi] 계정 조회 실패: HTTP {e.code}", e.read().decode("utf-8", "replace")[:200])
        return 0
    except Exception as e:  # noqa: BLE001
        log("[SerpApi] 계정 조회 실패:", e)
        return 0
    left = _amount(d.get("total_searches_left") if d.get("total_searches_left") is not None
                   else d.get("plan_searches_left"))
    n, runs = _share(left, 1, _parse_time(d.get("plan_renewal_date")), s, n_groups)
    log(f"[SerpApi] 남은 검색 {left}회, 갱신까지 약 {runs}번 수집 → 이번에 상품 {n}개 조회")
    return n


def apify_quota(s, n_groups):
    token = (os.getenv("APIFY_TOKEN") or "").strip()
    if not token:
        return 0
    try:
        d = http_json("https://api.apify.com/v2/users/me/limits", {"Authorization": f"Bearer {token}"})["data"]
        used = float(d["current"]["monthlyUsageUsd"])
        cap = float(d["limits"]["maxMonthlyUsageUsd"])
        end = _parse_time(d["monthlyUsageCycle"]["endAt"])
    except urllib.error.HTTPError as e:
        log(f"[Apify] 사용량 조회 실패: HTTP {e.code}", e.read().decode("utf-8", "replace")[:200])
        return 0
    except Exception as e:  # noqa: BLE001
        log("[Apify] 사용량 조회 실패:", e)
        return 0
    cap = min(cap, float(s.get("apify_max_usd_per_month", 4.5)))
    per_group = int(s.get("apify_items_per_keyword", 10)) * float(s.get("apify_usd_per_item", 0.0025)) + 0.001
    n, runs = _share(cap - used, per_group, end, s, n_groups)
    log(f"[Apify] 이번 달 ${used:.2f} / ${cap:.2f} 사용, 갱신까지 약 {runs}번 수집 → 이번에 상품 {n}개 조회")
    return n


def serpapi_naver(query):
    """SerpApi로 네이버 통합검색을 받아 '네이버 쇼핑' 칸의 상품만 쓴다. 실패하면 None (지난 결과 유지)."""
    key = (os.getenv("SERPAPI_KEY") or "").strip()
    if not key or "serpapi" in _SKIP:
        return None
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(
        {"engine": "naver", "query": query, "where": "nexearch", "api_key": key})
    try:
        d = http_json(url, timeout=90)
    except urllib.error.HTTPError as e:
        log(f"[SerpApi] {query} 실패: HTTP {e.code}", e.read().decode("utf-8", "replace")[:200])
        if e.code in (401, 403, 429):  # 키 오류·한도 초과 → 이번 실행 중단
            _SKIP.add("serpapi")
        return None
    except Exception as e:  # noqa: BLE001
        log(f"[SerpApi] {query} 실패:", e)
        return None
    if d.get("error"):
        if "any results" in d["error"]:  # 검색은 됐는데 결과가 없음
            log(f"[SerpApi] {query}: 결과 없음")
            return []
        log(f"[SerpApi] {query} 실패:", d["error"])
        return None
    items = d.get("shopping_results") or []
    if isinstance(items, dict):
        items = items.get("items") or items.get("results") or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        prices = [p for p in (_amount(it.get("coupon_price")), _amount(it.get("price"))) if p > 0]
        if not prices:
            continue
        price = min(prices)
        free = bool(it.get("free_shipping"))
        fee = 0 if free else _amount(it.get("shipping"))
        link = it.get("link") or ""
        mid = re.search(r"nv_mid=(\d+)", link)
        key_id = mid.group(1) if mid else hashlib.md5(f"{it.get('title')}|{it.get('stores')}".encode()).hexdigest()[:12]
        stores = it.get("stores")
        mall = ", ".join(map(str, stores))[:30] if isinstance(stores, list) else str(stores or "")
        out.append({
            "source": "naver",
            "region": "KR",
            "key": f"naver:{key_id}",
            "title": clean_title(it.get("title")),
            "mall": mall or "네이버쇼핑",
            "price_orig": price,
            "currency": "KRW",
            "landed_krw": price + fee,
            "ship_note": "무료배송" if free else (f"배송비 {fee:,}원 포함" if fee else "배송비 판매처별 상이"),
            "link": link,
            "image": it.get("thumbnail"),
        })
    log(f"[SerpApi] {query}: 쇼핑 칸 {len(out)}건")
    return out


def apify_coupang(query, s):
    """Apify 쿠팡 검색 액터를 한 번 돌려 결과를 바로 받는다. 실패하면 None (지난 결과 유지)."""
    token = (os.getenv("APIFY_TOKEN") or "").strip()
    if not token or "apify" in _SKIP:
        return None
    actor = s.get("apify_coupang_actor", "piotrv1001~coupang-listings-scraper")
    n = int(s.get("apify_items_per_keyword", 10))
    max_usd = round(n * float(s.get("apify_usd_per_item", 0.0025)) * 2 + 0.01, 3)  # 한 번 실행에 쓸 수 있는 최대 금액
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?" + urllib.parse.urlencode(
        {"timeout": 240, "maxItems": n, "maxTotalChargeUsd": max_usd, "clean": 1})
    body = json.dumps({"searchTerms": [query], "maxResults": n,
                       "sortBy": s.get("apify_coupang_sort", "relevance")}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "User-Agent": UA, "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            items = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log(f"[Apify 쿠팡] {query} 실패: HTTP {e.code}", e.read().decode("utf-8", "replace")[:300])
        if e.code in (401, 402, 403):  # 토큰 오류·크레딧 소진 → 이번 실행 중단
            _SKIP.add("apify")
        return None
    except Exception as e:  # noqa: BLE001
        log(f"[Apify 쿠팡] {query} 실패:", e)
        return None
    if not isinstance(items, list):
        log(f"[Apify 쿠팡] {query} 응답 형식이 달라요:", str(items)[:200])
        return None
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("isSoldOut") or it.get("soldOut"):
            continue
        price = _amount(it.get("salesPrice") or it.get("price"))
        if price <= 0:
            continue
        if it.get("isRocket"):
            mall, note = "쿠팡 로켓배송", "로켓배송 (와우 회원 무료, 일반 19,800원 이상 무료)"
        elif it.get("isRocketMerchant"):
            mall, note = "쿠팡 판매자로켓", "판매자로켓"
        else:
            mall, note = "쿠팡", "배송비 판매자별 상이"
        out.append({
            "source": "coupang",
            "region": "KR",
            "key": f"coupang:{it.get('productId') or it.get('itemId') or it.get('vendorItemId')}",
            "title": clean_title(it.get("title") or it.get("name")),
            "mall": mall,
            "price_orig": price,
            "currency": "KRW",
            "landed_krw": price,
            "ship_note": note,
            "link": it.get("url") or it.get("productUrl"),
            "image": it.get("imageUrl") or it.get("image"),
        })
    log(f"[Apify 쿠팡] {query}: {len(out)}건")
    return out


def pick_groups(groups, n, slot):
    """가장 오래전에 조회한 상품부터 n개."""
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    ordered = sorted(groups, key=lambda g: _parse_time((slot.get(g["id"]) or {}).get("at")) or oldest)
    return {g["id"] for g in ordered[:n]}


def paid_offers(g, fetch, slot, fresh, s):
    """이번 차례인 상품은 새로 조회하고, 아니면 cache.json의 지난 결과를 쓴다."""
    now = datetime.now(timezone.utc)
    q = (g.get("ko") or [None])[0]
    if q and g["id"] in fresh:
        got = fetch(q)
        if got is not None:
            slot[g["id"]] = {"at": now.isoformat(timespec="minutes"), "offers": [dict(o) for o in got]}
            return got
    ent = slot.get(g["id"]) or {}
    at = _parse_time(ent.get("at"))
    if not at or now - at > timedelta(hours=s.get("paid_cache_max_age_hours", 72)):
        return []
    return [{**o, "fetched_at": ent["at"]} for o in ent.get("offers", [])]


# ---------------------------------------------------------------- 필터·정리
def relevant(offer, group, s):
    """must_ko/must_ja: [["먼작귀","치이카와"], ["인형"]] → 각 줄에서 하나 이상 포함 (줄끼리는 AND)."""
    t = offer["title"].lower()
    jp = offer["region"] == "JP"
    excl = s["exclude_words"] + (group.get("exclude_ja" if jp else "exclude_ko") or [])
    if any(w.lower() in t for w in excl):
        return False
    must = group.get("must_ja" if jp else "must_ko") or []
    if must and isinstance(must[0], str):
        must = [must]
    return all(any(m.lower() in t for m in row) for row in must)


def finalize(offers, s):
    seen, uniq = set(), []
    for o in sorted(offers, key=lambda x: x["landed_krw"]):
        if o["key"] in seen:
            continue
        seen.add(o["key"])
        uniq.append(o)
    if len(uniq) >= 5:
        med = statistics.median(o["landed_krw"] for o in uniq)
        for o in uniq:
            o["suspicious"] = o["landed_krw"] < med * s["suspicious_ratio"]
    return uniq[: s["max_offers_per_group"]]


# ---------------------------------------------------------------- 모의 데이터
def mock_offers(group, fx, s):
    rnd = random.Random(group["id"] + datetime.now(KST).strftime("%Y%m%d"))
    base = group.get("target_krw", 10000)
    malls_kr = ["쿠팡", "11번가", "G마켓", "치이카와 공식 스토어", "토이저러스", "스마트스토어 먼작귀샵"]
    malls_jp = ["ちいかわらんど楽天市場店", "キャラクターショップ", "ホビーストック"]
    out = []
    for i in range(8):
        p = int(base * rnd.uniform(0.8, 1.8) // 100 * 100)
        out.append({"source": "11st", "region": "KR", "key": f"mock:kr:{group['id']}:{i}",
                    "title": f"{group['ko'][0]} 정품 {rnd.choice(['S', 'M', 'L', '20cm', '세트'])}",
                    "mall": rnd.choice(malls_kr), "price_orig": p, "currency": "KRW", "landed_krw": p,
                    "ship_note": "배송비 판매처별 상이", "link": "https://search.11st.co.kr/", "image": None})
    out.append({**out[0], "key": f"mock:kr:{group['id']}:fake", "title": f"{group['ko'][0]} 초특가",
                "mall": "해외직구몰", "price_orig": int(base * 0.3), "landed_krw": int(base * 0.3)})
    for i in range(5):
        jpy = int(base / fx * rnd.uniform(0.5, 1.1) // 10 * 10)
        out.append(jp_offer(rnd.choice(["rakuten", "yahoo"]), f"mock:jp:{group['id']}:{i}",
                            f"{group['ja'][0]} 公式", rnd.choice(malls_jp), jpy, rnd.random() < 0.4,
                            "https://www.rakuten.co.jp/", None, fx, s))
    return out


# ---------------------------------------------------------------- 알림
def telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        log("[알림] 텔레그램 미설정 — 메시지:\n" + text)
        return
    body = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=20)
    except Exception as e:  # noqa: BLE001
        log("텔레그램 전송 실패:", e)


# ---------------------------------------------------------------- main
def main():
    mock = "--mock" in sys.argv
    s = load_json(os.path.join(CONFIG, "settings.json"), {})
    groups = load_json(os.path.join(CONFIG, "keywords.json"), [])
    history = load_json(os.path.join(DATA, "history.json"), {})
    alerts_state = load_json(os.path.join(DATA, "alert_state.json"), {})

    fx, fx_src = (s["fx_fallback_jpy_krw"], "샘플") if mock else get_fx(s)
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    if mock and not history:  # 샘플 모드: 그래프 확인용 30일치 가짜 추이
        for g in groups:
            rnd, base = random.Random(g["id"]), g.get("target_krw", 10000) * 1.15
            history[g["id"]] = []
            for d in range(30, 0, -1):
                base *= rnd.uniform(0.97, 1.03)
                history[g["id"]].append({"date": (now - timedelta(days=d)).strftime("%Y-%m-%d"),
                                         "min": int(base), "kr": int(base * 1.05), "jp": int(base)})
    result, alerts = [], []

    if not mock and not os.getenv("ELEVENST_API_KEY"):
        log("⚠ ELEVENST_API_KEY 가 없어 국내 가격(11번가)은 건너뜁니다.")

    # 한도가 있는 소스: (조회 함수, cache.json 칸, 이번에 새로 조회할 상품 id들)
    cache = {} if mock else load_json(CACHE, {})
    paid = []
    if not mock:
        for name, quota, fetch in (("serpapi", serpapi_quota, serpapi_naver),
                                   ("apify", apify_quota, lambda q: apify_coupang(q, s))):
            slot = cache.setdefault(name, {})
            for gid in [k for k in slot if k not in {g["id"] for g in groups}]:
                slot.pop(gid)  # keywords.json에서 빠진 상품은 지움
            paid.append((fetch, slot, pick_groups(groups, quota(s, len(groups)), slot)))

    for g in groups:
        if mock:
            raw = mock_offers(g, fx, s)
        else:
            raw = []
            for q in g.get("ko", []):
                raw += elevenst(q) + naver(q)
            for fetch, slot, fresh in paid:
                raw += paid_offers(g, fetch, slot, fresh, s)
            for q in g.get("ja", []):
                raw += rakuten(q, fx, s) + yahoo(q, fx, s)
        offers = finalize([o for o in raw if relevant(o, g, s)], s)
        clean = [o for o in offers if not o.get("suspicious")]
        best = clean[0] if clean else None
        best_kr = next((o for o in clean if o["region"] == "KR"), None)
        best_jp = next((o for o in clean if o["region"] == "JP"), None)

        # 가격 추이 기록 (하루 1건, 같은 날은 덮어쓰기)
        h = [e for e in history.get(g["id"], []) if e["date"] != today]
        prev_low = min((e["min"] for e in h), default=None)
        if best:
            h.append({"date": today, "min": best["landed_krw"],
                      "kr": best_kr["landed_krw"] if best_kr else None,
                      "jp": best_jp["landed_krw"] if best_jp else None})
        cutoff = (now - timedelta(days=s["history_days"])).strftime("%Y-%m-%d")
        history[g["id"]] = sorted([e for e in h if e["date"] >= cutoff], key=lambda e: e["date"])

        # 알림 판단: 역대 최저가 갱신 or 목표가 이하 (같은 가격 반복 알림 방지)
        if best:
            price = best["landed_krw"]
            last = alerts_state.get(g["id"])
            reasons = []
            if prev_low is not None and price < prev_low:
                reasons.append(f"역대 최저가 갱신 (이전 {prev_low:,}원)")
            if g.get("target_krw") and price <= g["target_krw"]:
                reasons.append(f"목표가 {g['target_krw']:,}원 이하")
            if reasons and (last is None or price < last):
                alerts_state[g["id"]] = price
                margin = ""
                if g.get("sell_price"):
                    m = (g["sell_price"] - price) / g["sell_price"] * 100
                    margin = f"\n판매가 {g['sell_price']:,}원 기준 마진 {m:.0f}%"
                alerts.append(
                    f"🐹 <b>{html.escape(g['name'])}</b> {price:,}원\n"
                    f"{' · '.join(reasons)}\n{html.escape(best['mall'])} — {html.escape(best['title'][:60])}"
                    f"{margin}\n<a href=\"{html.escape(best['link'] or '')}\">상품 보기</a>"
                )
            elif not reasons and last is not None and price > last * 1.1:
                alerts_state.pop(g["id"], None)  # 가격이 다시 올라가면 다음 하락 때 재알림

        result.append({
            "id": g["id"], "name": g["name"], "target_krw": g.get("target_krw"),
            "sell_price": g.get("sell_price"), "best": best, "best_kr": best_kr, "best_jp": best_jp,
            "all_time_low": min([e["min"] for e in history[g["id"]]], default=None),
            "offers": offers,
        })
        log(f"{g['name']}: {len(offers)}건, 최저 {best['landed_krw'] if best else '-'}원")

    latest = {"updated": now.isoformat(timespec="minutes"), "fx_jpy_krw": fx, "fx_source": fx_src,
              "settings": {k: s[k] for k in ("jp_domestic_shipping_jpy", "jp_forwarding_krw_per_item", "jp_import_tax_rate")},
              "mock": mock, "groups": result}
    save_json(os.path.join(DATA, "latest.json"), latest)
    save_json(os.path.join(DATA, "history.json"), history)
    save_json(os.path.join(DATA, "alert_state.json"), alerts_state)
    if not mock:
        save_json(CACHE, cache)

    if alerts:
        telegram(f"📉 먼작귀 최저가 알림 ({now:%m/%d %H:%M})\n\n" + "\n\n".join(alerts))
    log(f"완료: {len(result)}개 그룹, 알림 {len(alerts)}건")


if __name__ == "__main__":
    main()
