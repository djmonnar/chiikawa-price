# 먼작귀 최저가 노트

치이카와(먼작귀) 굿즈를 국내 쇼핑몰 + 일본 쇼핑몰에서 매일 2번(08:17, 18:17) 수집해서
**한국 도착 기준 최저가**를 비교하고, 최저가 갱신·목표가 도달 시 텔레그램으로 알려주는 매입용 대시보드입니다.

- 서버 비용 0원: GitHub Actions(수집) + GitHub Pages(화면)
- 국내: 11번가 오픈API 상품검색 + (선택) SerpApi로 네이버 통합검색 쇼핑 칸 + (선택) Serper로 구글쇼핑(쿠팡·SSG·에이블리 등) + (선택) Apify로 쿠팡 검색
  (네이버쇼핑 검색 API는 2026-07-31 종료되어 더 이상 못 씀)
- 일본: 라쿠텐 시장, Yahoo!쇼핑 → `(엔화가 + 일본 내 배송비) × 환율 + 배대지비`로 환산
- 가품 의심 표시: 같은 상품군 중간값의 40% 미만 가격은 자동으로 숨김

## 폴더 구조
```
config/keywords.json    추적할 상품 목록 (한/일 검색어, 목표가, 판매가)
config/settings.json    배대지비, 일본 배송비, 제외 단어 등
scripts/collect.py      수집 스크립트 (외부 라이브러리 없음)
docs/index.html         대시보드 화면 (GitHub Pages)
docs/data/              수집 결과 (자동 커밋)
.github/workflows/      매일 자동 실행 설정
```

## 설치 (약 20분)

### 1. API 키 발급 (전부 무료)
| 용도 | 발급처 | 필요한 값 |
|---|---|---|
| 국내 | openapi.11st.co.kr → 11번가 아이디로 로그인 → **API 신청** | 32자리 OpenAPI Key (180일마다 갱신) |
| 네이버쇼핑 칸 (선택) | serpapi.com 가입 (무료 월 250회) → serpapi.com/manage-api-key | Private API Key |
| 구글쇼핑 (선택) | serper.dev 가입 (카드 없이 무료 2,500회, 한 번만 줌) → serper.dev/api-keys | API Key |
| 쿠팡 (선택) | apify.com 가입 (카드 없이 무료 월 $5) → console.apify.com/settings/integrations | Personal API token |
| 라쿠텐 (선택) | 라쿠텐 회원 로그인 → webservice.rakuten.co.jp/app/create (새 앱 등록) | アプリケーションID(UUID 형식) + アクセスキー(`pk_`로 시작) — 둘 다 필요 |
| 야후쇼핑 (선택) | e.developer.yahoo.co.jp/register (일본 휴대폰 번호 필요) | Client ID |
| 알림 (선택) | 텔레그램 @BotFather → /newbot | 봇 토큰 + 내 chat id |

> 네이버 검색 API의 **쇼핑** 검색은 2026-07-31에 종료됐어요(키가 있어도 `SE05` 오류). 코드는 남겨 뒀지만 첫 오류 뒤 바로 건너뜁니다.

텔레그램 chat id: 만든 봇에게 아무 메시지나 보낸 뒤
`https://api.telegram.org/bot<토큰>/getUpdates` 를 열면 `"chat":{"id":숫자}` 가 보입니다.
여자친구분 폰으로 받으려면 여자친구분이 봇에게 메시지를 보내고 그 id를 쓰면 돼요.

### 2. GitHub에 올리기
1. 새 저장소 생성 (예: `chiikawa-price`) 후 이 폴더 전체 업로드
2. **Settings → Secrets and variables → Actions → New repository secret** 에 아래 이름으로 등록
   `ELEVENST_API_KEY`, `SERPAPI_KEY`, `SERPER_API_KEY`, `APIFY_TOKEN`, `RAKUTEN_APP_ID`, `RAKUTEN_ACCESS_KEY`, `YAHOO_APP_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. **Settings → Pages** → Branch `main`, 폴더 `/docs` → Save
4. `config/settings.json` 의 `rakuten_origin` 을 사이트 주소(현재 `https://chiikawa-price.vercel.app`)로 맞추고, 라쿠텐 앱 등록 때 **アプリケーションタイプ: Webアプリケーション**, **許可されたWebサイト: `chiikawa-price.vercel.app`** 로 등록 (2026년 새 API는 이 주소의 Referer 헤더를 확인함). 2026-05-13 이전에 만든 앱 키는 새 API에서 안 되니 새로 등록해야 해요. 키가 틀리면 수집 로그에 `⚠ [라쿠텐] …` 으로 무엇을 확인할지 찍힙니다
5. **Actions 탭 → 먼작귀 최저가 수집 → Run workflow** 로 첫 실행

이후 `https://아이디.github.io/chiikawa-price/` 에서 확인. 홈 화면에 추가하면 앱처럼 쓸 수 있어요.

## 상품 추가·수정
`config/keywords.json` 에 블록 하나 추가:
```json
{
  "id": "shisa-plush",              // 영문 고유값
  "name": "시사 인형",               // 화면 표시 이름
  "ko": ["먼작귀 시사 인형"],         // 국내 검색어 (여러 개 가능)
  "ja": ["ちいかわ シーサー ぬいぐるみ"], // 일본 검색어
  "must_ko": [["치이카와", "먼작귀", "chiikawa"], ["시사", "shisa"]],
                                     // 줄마다 하나는 꼭 있어야 함 (줄끼리는 AND) → 엉뚱한 상품 제거
  "must_ja": ["シーサー"],
  "google_q": "chiikawa shisa plush", // (선택) 구글쇼핑만 따로 쓸 검색어. 없으면 ko 첫 검색어
  "target_krw": 15000,              // 이 가격 이하면 알림
  "sell_price": 25000               // 매장 판매가 → 마진% 계산
}
```

## 알아둘 점
- **일본 도착가는 추정치**예요. 라쿠텐·야후 판매자 대부분은 한국 직배송을 안 해서 배대지 기준으로 계산합니다. 합배송하면 1개당 배대지비가 내려가니 `jp_forwarding_krw_per_item` 을 실제 단가에 맞게 조정하세요.
- **판매 목적 수입은 개인 소액면세(자가사용) 대상이 아니에요.** 기본값은 부가세 10%만 도착가에 반영하고(`jp_import_tax_rate: 0.1`), 관세는 품목(HS코드)·원산지에 따라 달라서 관세사나 배대지에 확인한 뒤 올려주세요.
- 11번가 가격은 '할인모음가'(받을 수 있는 할인을 모은 가격)가 있으면 그걸 씁니다. 배송비가 숫자로 오면 더하고, '무료배송' 같은 글자면 그대로 표시만 해요.
- 11번가 오픈API 키는 **유효기간이 180일**이에요(2026-06-30부터). 11번가 공지로는 API를 계속 쓰면 별도 조치 없이 자동 연장되고, 갱신해도 키 값은 그대로예요. 혹시 만료되면 수집 로그에 `[11번가] … 003` 이 찍히니 https://openapi.11st.co.kr/openapi/OpenApiServiceRegister.tmall 에서 갱신만 누르면 돼요(Secret은 안 바꿔도 됨).
- **SerpApi·Apify는 무료 한도 안에서 나눠 써요.** 수집할 때마다 남은 한도(SerpApi 검색 횟수, Apify 금액)를 확인해서 갱신일까지 남은 수집 횟수로 나누고, 그만큼의 상품만 새로 조회합니다. 나머지 상품은 `docs/data/cache.json`에 남은 지난 결과(72시간 이내)를 쓰고, 화면에 "N시간 전 가격"으로 표시돼요. 무료 기준으로 상품 8개면 네이버 칸은 하루 1번, 쿠팡은 1~2일에 1번꼴로 새로 조회됩니다.
- SerpApi 네이버는 통합검색 첫 화면의 '네이버 쇼핑' 칸(광고 포함 몇 개)만 가져와요. 가격비교 전체 목록이 아니라서 진짜 최저가가 빠질 수 있어요.
- Serper 구글쇼핑은 가입 때 받은 무료 2,500회를 다 쓰면 끝이에요(매달 다시 채워지지 않음). 그래서 상품마다 하루 1번(`serper_refresh_hours`)만 조회해 상품 8개 기준 약 10달 쓸 수 있어요. 쿠팡·SSG·에이블리·텐바이텐·직구몰 등이 원화로 나오고, 일본 쇼핑몰(ちいかわマーケット 등)은 국제배송비가 빠진 가격이라 뺍니다.
- Apify는 카드를 등록하지 않아도 무료 플랜(월 $5 크레딧)으로 쓸 수 있어요. 크레딧이 떨어지면 그달은 멈출 뿐 요금이 나가지 않아요.
- Apify 쿠팡은 다른 개발자가 만든 수집기(`piotrv1001~coupang-listings-scraper`, 상품 1개당 약 $0.0025)를 빌려 쓰는 방식이에요. 쿠팡 화면이 바뀌면 한동안 안 될 수 있고, 쇼핑몰 약관은 대개 자동 수집을 막고 있다는 점은 알고 쓰세요. 수집기는 `settings.json`의 `apify_coupang_actor`로 바꿀 수 있어요.
- 쿠팡 공식 API(쿠팡 파트너스)는 최종 승인(누적 판매 15만원 이상) 후에 받을 수 있어요.
- 공개 저장소의 Pages는 주소를 아는 사람은 누구나 볼 수 있어요 (검색엔진 노출은 막아둠).
- 정품 재판매(병행수입)는 일반적으로 가능하지만, 가품 판매는 상표권 침해입니다. 가품의심 표시가 없더라도 판매처를 꼭 확인하세요.

## 로컬 테스트
```bash
python scripts/collect.py --mock          # API 없이 샘플 데이터 생성
cd docs && python -m http.server 8000     # http://localhost:8000
```
샘플 데이터는 배포 전에 `docs/data/` 에서 지워주세요 (첫 Actions 실행 전까지는 화면에 '불러오지 못했어요'가 뜨는 게 정상).
