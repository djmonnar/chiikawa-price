#!/usr/bin/env python3
"""먼작귀(치이카와) 굿즈 최저가 수집기.

국내: 네이버쇼핑 검색 API (쿠팡·11번가·G마켓·스마트스토어·해외직구 상품 포함)
일본: 라쿠텐 시장 API, Yahoo!쇼핑 API  → 엔화를 원화로 환산 + 배송비/배대지비 추정

결과는 docs/data/latest.json, docs/data/history.json 에 저장되고
최저가 갱신·목표가 도달 시 텔레그램으로 알림을 보냅니다.

환경변수 (GitHub Secrets):
  NAVER_CLIENT_ID, NAVER_CLIENT_SECRET   (필수, 국내 비교)
  RAKUTEN_APP_ID, RAKUTEN_ACCESS_KEY     (선택, 라쿠텐)
  YAHOO_APP_ID                           (선택, 야후쇼핑)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (선택, 알림)

사용: python scripts/collect.py          # 실제 수집
      python scripts/collect.py --mock   # API 없이 샘플 데이터로 테스트
"""
import html
import json
import os
import random
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config")
DATA = os.path.join(ROOT, "docs", "data")
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
def naver(query):
    cid, sec = os.getenv("NAVER_CLIENT_ID"), os.getenv("NAVER_CLIENT_SECRET")
    if not (cid and sec):
        return []
    out = []
    for sort in ("sim", "asc"):
        url = "https://openapi.naver.com/v1/search/shop.json?" + urllib.parse.urlencode(
            {"query": query, "display": 100, "sort": sort, "exclude": "used:rental"}
        )
        try:
            d = http_json(url, {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec})
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


def rakuten(query, fx, s):
    app, key = os.getenv("RAKUTEN_APP_ID"), os.getenv("RAKUTEN_ACCESS_KEY")
    if not app:
        return []
    out = []
    for sort in ("standard", "+itemPrice"):
        params = {"applicationId": app, "keyword": query, "hits": 30, "sort": sort,
                  "availability": 1, "format": "json", "formatVersion": 2}
        if key:
            params["accessKey"] = key
        url = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20220601?" + urllib.parse.urlencode(params)
        try:
            d = http_json(url, {"Origin": s.get("rakuten_origin", "https://example.com")})
        except Exception as e:  # noqa: BLE001
            log(f"[라쿠텐] {query} 실패:", e)
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
        out.append({"source": "naver", "region": "KR", "key": f"mock:kr:{group['id']}:{i}",
                    "title": f"{group['ko'][0]} 정품 {rnd.choice(['S', 'M', 'L', '20cm', '세트'])}",
                    "mall": rnd.choice(malls_kr), "price_orig": p, "currency": "KRW", "landed_krw": p,
                    "ship_note": "배송비 판매처별 상이", "link": "https://search.shopping.naver.com/", "image": None})
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

    if not mock and not os.getenv("NAVER_CLIENT_ID"):
        log("⚠ NAVER_CLIENT_ID 가 없어 국내 가격은 건너뜁니다.")

    for g in groups:
        if mock:
            raw = mock_offers(g, fx, s)
        else:
            raw = []
            for q in g.get("ko", []):
                raw += naver(q)
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

    if alerts:
        telegram(f"📉 먼작귀 최저가 알림 ({now:%m/%d %H:%M})\n\n" + "\n\n".join(alerts))
    log(f"완료: {len(result)}개 그룹, 알림 {len(alerts)}건")


if __name__ == "__main__":
    main()
