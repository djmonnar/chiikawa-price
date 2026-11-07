# 먼작귀 최저가 노트

치이카와(먼작귀) 굿즈를 국내 쇼핑몰 + 일본 쇼핑몰에서 매일 2번(08:17, 18:17) 수집해서
**한국 도착 기준 최저가**를 비교하고, 최저가 갱신·목표가 도달 시 텔레그램으로 알려주는 매입용 대시보드입니다.

- 서버 비용 0원: GitHub Actions(수집) + GitHub Pages(화면)
- 국내: 네이버쇼핑 API → 쿠팡·11번가·G마켓·스마트스토어·해외직구 상품까지 한 번에
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
| 국내 (필수) | developers.naver.com → 애플리케이션 등록 → 사용 API: **검색** | Client ID, Client Secret |
| 라쿠텐 (선택) | webservice.rakuten.co.jp → アプリID発行 | アプリID, アクセスキー |
| 야후쇼핑 (선택) | e.developer.yahoo.co.jp/register | Client ID |
| 알림 (선택) | 텔레그램 @BotFather → /newbot | 봇 토큰 + 내 chat id |

텔레그램 chat id: 만든 봇에게 아무 메시지나 보낸 뒤
`https://api.telegram.org/bot<토큰>/getUpdates` 를 열면 `"chat":{"id":숫자}` 가 보입니다.
여자친구분 폰으로 받으려면 여자친구분이 봇에게 메시지를 보내고 그 id를 쓰면 돼요.

### 2. GitHub에 올리기
1. 새 저장소 생성 (예: `chiikawa-price`) 후 이 폴더 전체 업로드
2. **Settings → Secrets and variables → Actions → New repository secret** 에 아래 이름으로 등록
   `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `RAKUTEN_APP_ID`, `RAKUTEN_ACCESS_KEY`, `YAHOO_APP_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. **Settings → Pages** → Branch `main`, 폴더 `/docs` → Save
4. `config/settings.json` 의 `rakuten_origin` 을 Pages 주소(`https://아이디.github.io`)로 변경 (라쿠텐 API가 Origin 헤더를 요구함)
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
  "must_ko": ["시사"],               // 제목에 이 중 하나는 꼭 있어야 함 (엉뚱한 상품 제거)
  "must_ja": ["シーサー"],
  "target_krw": 15000,              // 이 가격 이하면 알림
  "sell_price": 25000               // 매장 판매가 → 마진% 계산
}
```

## 알아둘 점
- **일본 도착가는 추정치**예요. 라쿠텐·야후 판매자 대부분은 한국 직배송을 안 해서 배대지 기준으로 계산합니다. 합배송하면 1개당 배대지비가 내려가니 `jp_forwarding_krw_per_item` 을 실제 단가에 맞게 조정하세요.
- **판매 목적 수입은 개인 소액면세(자가사용) 대상이 아니에요.** 기본값은 부가세 10%만 도착가에 반영하고(`jp_import_tax_rate: 0.1`), 관세는 품목(HS코드)·원산지에 따라 달라서 관세사나 배대지에 확인한 뒤 올려주세요.
- 네이버 API는 판매처별 배송비를 주지 않아서 국내 가격은 상품가 기준입니다.
- 네이버 검색 API 한도: 하루 25,000회. 기본 설정(8개 상품 × 하루 2회)은 약 60회라 여유 많아요.
- 공개 저장소의 Pages는 주소를 아는 사람은 누구나 볼 수 있어요 (검색엔진 노출은 막아둠).
- 정품 재판매(병행수입)는 일반적으로 가능하지만, 가품 판매는 상표권 침해입니다. 가품의심 표시가 없더라도 판매처를 꼭 확인하세요.

## 로컬 테스트
```bash
python scripts/collect.py --mock          # API 없이 샘플 데이터 생성
cd docs && python -m http.server 8000     # http://localhost:8000
```
샘플 데이터는 배포 전에 `docs/data/` 에서 지워주세요 (첫 Actions 실행 전까지는 화면에 '불러오지 못했어요'가 뜨는 게 정상).
