# CLAUDE.md

이 파일은 Claude Code가 이 프로젝트에서 작업할 때 참고하는 컨텍스트입니다.

## 프로젝트 개요

**Stock Monitor + LLM Advisor** — 개인용 주식 모니터링 웹 앱. 차트, 공시, 뉴스, 커뮤니티 데이터를 통합하고, 현재 보고 있는 종목의 컨텍스트를 자동으로 LLM에 주입해서 자연스럽게 상담할 수 있게 한다.

본인 1명이 사용. 자동매매 없음.

### 시장 확장 로드맵

- **Phase 1 (현재)**: 한국 주식 (KOSPI/KOSDAQ) — MVP, 인프라/UI/LLM 어셈블러 검증
- **Phase 2 (예정)**: 미국 주식 (NYSE/NASDAQ) — yfinance, SEC EDGAR, 영문 뉴스/Reddit 추가
- **Phase 3 이후**: 일본/홍콩, 유럽 (필요 시)

**중요**: Phase 1만 구현하더라도 코드/스키마는 처음부터 멀티마켓을 전제로 설계한다. 종목 식별자, 시간대, 통화, 데이터 소스 추상화를 한국 전용으로 박아두지 말 것. 자세한 규약은 아래 [도메인 지식](#도메인-지식) 참조.

## 현재 진행 상태

- [x] CLAUDE.md 초안 작성
- [ ] backend 스캐폴딩 (FastAPI, DB, Redis)
- [ ] DB 스키마 설계 (멀티마켓 전제)
- [ ] 시세 수집 워커 (한국)
- [ ] 공시/뉴스 수집 워커 (한국)
- [ ] LLM 컨텍스트 어셈블러
- [ ] 종목 발굴 — 스크리닝(조건 검색)
- [ ] 종목 발굴 — LLM 기반 추천
- [ ] 프론트엔드 차트/대시보드
- [ ] 프론트엔드 발굴 후보 UI
- [ ] 알림 시스템 (텔레그램)
- [ ] Phase 2: 미국장 데이터 소스 통합

## 기술 스택

### Backend
- Python 3.11+ / FastAPI / asyncio
- SQLAlchemy 2.0 (async) / Alembic
- PostgreSQL 16 + TimescaleDB
- Redis (cache + pub/sub)
- APScheduler (배치/스케줄링)
- httpx (외부 API 호출, async)

### Frontend
- Next.js 15 (App Router) / React 19
- TypeScript (strict mode)
- Tailwind CSS
- Lightweight Charts (TradingView)
- Zustand (상태관리)

### LLM
- Anthropic Claude API
- 메인: `claude-opus-4-7` (상담, 복잡한 분석)
- 경량: `claude-haiku-4-5` (종토방 감성 분류 등 배치)
- `anthropic` Python SDK 사용

### 외부 데이터

#### Phase 1 — 한국 (KR)
- **시세 (일봉)**: pykrx, FinanceDataReader
- **시세 (실시간)**: 네이버 금융 모바일 API (비공식)
- **공시**: DART OpenAPI
- **뉴스**: 네이버 금융 뉴스, RSS (한경, 매경, 연합인포맥스)
- **커뮤니티**: 네이버 종토방
- **재무지표/스크리닝**: pykrx (PER/PBR/배당수익률 등 fundamental), FinanceDataReader (StockListing으로 전체 종목 마스터), DART (재무제표 원본)
- **상장 종목 마스터**: KRX/FinanceDataReader (KOSPI/KOSDAQ 전 종목 리스트, 발굴 대상 풀)

#### Phase 2 — 미국 (US, 예정)
- **시세 (일봉)**: yfinance (무료, 광범위)
- **시세 (실시간/분봉)**: Finnhub 또는 TwelveData 무료 티어
- **공시**: SEC EDGAR (무료 공식 API, `sec-edgar-downloader` 등)
- **뉴스**: Finnhub news, Marketaux, RSS
- **커뮤니티**: Reddit (PRAW), StockTwits
- **재무지표/스크리닝**: yfinance `Ticker.info`, Finnhub `/stock/metric`, 또는 Financial Modeling Prep (FMP)
- **상장 종목 마스터**: NASDAQ/NYSE traded symbols 파일, 또는 Finnhub `/stock/symbol`

#### 시장 무관 / 공통
- **환율**: yfinance (`USDKRW=X`, `JPYKRW=X` 등)
- **거래소 캘린더**: `pandas_market_calendars` (Phase 2부터)

데이터 소스 어댑터는 `app/services/market/` 하위에 시장별로 분리하고, 상위 서비스는 추상화된 인터페이스에만 의존하게 한다.

### 인프라
- 시놀로지 NAS + Docker Compose
- Cloudflare Tunnel (외부 접속)
- Gitea (kovis.synology.me) — Git 저장소

## 디렉토리 구조

```
stock-advisor/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entry
│   │   ├── core/                # 설정, DB, Redis, 인증
│   │   ├── api/                 # REST 엔드포인트
│   │   ├── ws/                  # WebSocket 핸들러
│   │   ├── models/              # SQLAlchemy 모델
│   │   ├── schemas/             # Pydantic 스키마
│   │   ├── services/            # 비즈니스 로직
│   │   │   ├── market/          # 시세 (시장별 어댑터)
│   │   │   │   ├── base.py      # 추상 인터페이스
│   │   │   │   ├── kr.py        # 한국 (pykrx, 네이버)
│   │   │   │   └── us.py        # 미국 (yfinance, Phase 2)
│   │   │   ├── disclosure/      # 공시 (시장별)
│   │   │   │   ├── base.py
│   │   │   │   ├── kr.py        # DART
│   │   │   │   └── us.py        # SEC EDGAR (Phase 2)
│   │   │   ├── news.py
│   │   │   ├── community.py     # 종토방/Reddit/StockTwits 통합
│   │   │   ├── llm.py           # LLM 어셈블러
│   │   │   ├── fx.py            # 환율
│   │   │   ├── discovery/       # 종목 발굴
│   │   │   │   ├── base.py      # Candidate 도메인 모델
│   │   │   │   ├── screener.py  # 조건 기반 스크리닝
│   │   │   │   └── llm_suggest.py  # LLM 추천 발굴
│   │   │   ├── fundamentals/    # 재무지표 (시장별 어댑터)
│   │   │   │   ├── base.py
│   │   │   │   ├── kr.py        # pykrx fundamental
│   │   │   │   └── us.py        # yfinance/Finnhub (Phase 2)
│   │   │   └── alert.py
│   │   ├── workers/             # 백그라운드 워커
│   │   │   ├── price_poller.py
│   │   │   ├── disclosure_watcher.py
│   │   │   ├── news_collector.py
│   │   │   ├── board_crawler.py
│   │   │   ├── discovery_runner.py  # 스크리닝 주기 실행
│   │   │   └── alert_runner.py
│   │   └── llm/                 # LLM 추상화 (향후 다중 LLM 대비)
│   │       ├── base.py          # 추상 인터페이스
│   │       └── anthropic.py     # Claude 구현
│   ├── alembic/                 # DB 마이그레이션
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── app/                     # Next.js App Router
│   ├── components/
│   ├── lib/                     # API 클라이언트, 유틸
│   ├── stores/                  # Zustand
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## 코딩 규약

### Python (Backend)

- **타입 힌트 필수**. 모든 함수 시그니처에 타입 명시.
- **async 우선**. 동기 라이브러리는 `run_in_executor`로 감싸기.
- **Pydantic v2** 스키마로 입출력 검증.
- **에러 핸들링**: 도메인 예외는 `app/core/exceptions.py`에 정의. FastAPI exception handler에서 일관된 응답.
- **로깅**: `structlog` 사용. 구조화 JSON 로그. 민감 정보(잔액, 계좌번호) 로깅 금지.
- **import 순서**: ruff `I` 룰 (isort 호환)로 자동 정렬. stdlib → 3rd party → app local.
- **포맷**: ruff (line length 100), ruff format
- **테스트**: pytest + pytest-asyncio. 단위 테스트는 services 위주.

### TypeScript (Frontend)

- **strict mode**. `any` 사용 금지 (정말 필요하면 주석으로 사유 명시).
- **React Server Components 우선**. 클라이언트 컴포넌트는 인터랙션 필요 시만.
- **Tailwind**. 인라인 스타일 지양.
- **상태**: 서버 상태는 fetch + 캐싱, 클라이언트 상태만 Zustand.
- **API 클라이언트**: `lib/api.ts`에서 fetch 래퍼 일원화. 자동 타입 추론.
- **포맷**: prettier + eslint.

### Git

- **저장소**: Gitea (kovis.synology.me). GitHub 아님 → `gh` CLI 사용 불가, **PR/이슈는 Gitea 웹 UI에서 직접 생성**.
- **브랜치**: `main` (배포) ← `dev` (통합) ← `feature/*`, `bugfix/*`
- **커밋 메시지**: Conventional Commits (feat:, fix:, refactor:, chore:, docs:, test:)
- 한국어 또는 영어 모두 가능, 일관되게.

### 일반

- **변수명/함수명은 영어**. 한국어 주석은 OK.
- **민감 정보는 .env**. .env는 절대 커밋 안 함. .env.example로 템플릿만 관리.
- **DB 마이그레이션**: 직접 SQL 수정 X. 반드시 Alembic으로 생성.
- **종속성 추가 시**: 사유를 PR 또는 커밋 메시지에 명시. lockfile 갱신.

## 도메인 지식

### 시간대 규칙 (가장 중요)

- **DB에는 항상 UTC로 저장** (`TIMESTAMPTZ`). naive datetime 금지.
- **표시할 때만 사용자 시간대(KST)로 변환**. 변환은 프론트엔드 또는 API 직렬화 단계에서.
- 시장 시간 비교/판단은 **거래소 로컬 시간** 기준 → `zoneinfo` + `pandas_market_calendars` 사용.
- 미국장 시간대 주의: EST/EDT 서머타임 매년 두 번 바뀜. 직접 계산 X, 캘린더 라이브러리 사용.

### 종목 식별자 (멀티마켓 전제)

내부 표준 식별자는 **`{EXCHANGE}:{SYMBOL}`** 형식. 처음부터 이렇게 잡아둠.

- `KR:005930` — 삼성전자 (KOSPI)
- `KR:035720` — 카카오 (KOSPI)
- `US:AAPL` — Apple (NASDAQ)
- `US:BRK.B` — Berkshire Hathaway Class B

규칙:
- 한국 종목 코드는 6자리 숫자 문자열. 앞에 0 빼지 말 것. `005930` ≠ `5930`.
- 미국 티커는 대문자, `.` 포함 가능 (`BRK.B`, `BF.B`).
- 외부 API별 식별자(yfinance의 `005930.KS`, `7203.T` 등)와는 어댑터 내부에서 매핑. 상위 코드는 항상 내부 표준만 사용.
- DB `instruments` 테이블에 `exchange`, `symbol`, `country`, `currency`, `isin(nullable)` 컬럼을 필수로 포함.

### 시장 시간

거래소별 캘린더는 `pandas_market_calendars`로 일원화. 직접 하드코딩 X.

| 거래소 | 시간대 | 정규장 | 비고 |
|--------|--------|--------|------|
| KRX (KOSPI/KOSDAQ) | KST | 09:00 ~ 15:30 | 시간외 단일가: 15:40~16:00, 16:00~18:00 |
| NYSE | America/New_York | 09:30 ~ 16:00 | 서머타임 있음 |
| NASDAQ | America/New_York | 09:30 ~ 16:00 | 서머타임 있음 |

- 뉴스/커뮤니티는 24시간 수집.
- 시세 폴링 주기는 시장별 장중 여부에 따라 조절 (장중 1초, 시간외/휴장 시 폴링 중단 또는 저빈도).

### 통화 / 환율

- 모든 가격은 **원래 통화 그대로 저장** (`price` + `currency` 컬럼).
- 표시 통화 환산은 조회/표시 시점에. 환산용 환율은 `fx_rates` 테이블에 일자별 저장.
- 손익 계산: 매수 시점 환율과 평가 시점 환율을 구분해서 환차손익 분리.
- 환율 소스: yfinance (`USDKRW=X` 등). 일 1회 EOD 갱신.

### 데이터 소스 주의사항

#### 한국 (Phase 1)
- **pykrx**: 장 마감 후 일봉 데이터 조회용. 장중 실시간 X. 동기 라이브러리 → async 래퍼 필수.
- **네이버 금융 모바일 API**: 비공식. 안정적이지만 약관상 회색 지대. 본인용이라 OK. 외부 공개/배포 금지.
- **DART**: 분당 호출 제한 관대. corp_code로 종목코드 매핑 필요. corp_code는 일 1회 갱신.
- **종토방**: robots.txt 존중. 요청 간 sleep 1초. User-Agent 명시. **글 본문은 DB 외부 노출 금지** (저작권).

#### 미국 (Phase 2, 예정)
- **yfinance**: 무료지만 비공식 → Yahoo가 막을 가능성 상존. fallback 소스 1개 이상 확보.
- **SEC EDGAR**: 공식 API, 매우 안정적. User-Agent에 연락처 명시 의무.
- **Finnhub/TwelveData**: 무료 티어 rate limit (분/일) 엄격. 캐싱 필수.
- **Reddit (PRAW)**: 인증 필요. 토큰 발급. 글 본문 정책은 종토방과 동일하게 적용 (집계 지표만 LLM에 노출).

### LLM 컨텍스트 어셈블러

- `app/services/llm.py:assemble_context(instrument_id)`가 핵심 진입점.
- 호출 시점에 가장 최신 데이터로 컨텍스트 빌드. 캐시 사용 시 TTL ≤ 60초.
- 컨텍스트 크기 ~1500 토큰 목표. 초과 시 우선순위 낮은 정보부터 truncate.
- **컨텍스트 우선순위 (높음 → 낮음)**:
  1. 현재가, 등락률, 거래량
  2. 당일/최근 공시 헤드라인
  3. 최근 24h 뉴스 헤드라인 (본문 X, 헤드라인 + 요약만)
  4. 커뮤니티 감성 집계 지표 (긍정/부정 비율, 글 수 변화)
  5. 기술적 지표 요약 (이동평균, RSI 등)
  6. 과거 차트 요약 (1주/1개월 추세)
- **시장별 컨텍스트 차이**:
  - 한국: 공시(DART) + 종토방 감성 + 일반 뉴스
  - 미국 (Phase 2): SEC 파일링(10-K/10-Q/8-K) + earnings call 요약 + Reddit/StockTwits 감성
- **절대 컨텍스트에 넣지 말 것**: 종토방/Reddit 원문, 사용자 보유 수량/잔액.

### 종목 발굴 (Discovery)

관심종목(watchlist)에 아직 없는 종목 중 살펴볼 만한 것을 자동으로 골라내는 기능. **두 가지 발굴 방식**을 모두 지원하되, 결과는 watchlist와 **분리된 별도의 "발굴 후보(candidates)" 리스트**로 관리한다.

#### 발굴 방식

1. **스크리닝 (Screening)** — 사용자 정의 조건 기반 필터
   - 입력: `{ "market": "KR", "filters": [{"field": "per", "op": "<", "value": 10}, {"field": "op_margin", "op": ">", "value": 0.15}] }` 형태의 조건 정의
   - 처리: 시장별 fundamentals 어댑터에서 전 종목 지표를 받아 평가
   - 출력: 조건을 만족하는 종목 리스트
   - 주기: 일 1회 EOD 배치 또는 수동 트리거. 스크리너 정의는 DB(`screeners` 테이블)에 저장 → 재사용 가능

2. **LLM 추천 (Suggest)** — 현재 관심종목 컨텍스트를 LLM에 주고 유사/관련 종목 추천
   - 입력: 현재 watchlist + 사용자가 적은 투자 테마/관점 (예: "2차전지 공급망")
   - 처리: `claude-opus-4-7`에 종목 마스터(이름/섹터/주요 지표) 일부와 함께 질의. **종목 코드만 받아오고**, 추천 사유는 함께 저장
   - 주기: 수동 트리거 또는 주간 배치
   - 비용 관리: 종목 마스터 전체를 한 번에 넣으면 토큰 폭발 → 섹터 필터링 등으로 후보군 축소 후 LLM에 전달

#### 후보(candidates) 라이프사이클

```
[스크리너/LLM] → candidates 테이블 → 사용자 검토 → [승급/폐기/스누즈]
                                          ├── 승급: watchlist로 이동, 본격 데이터 수집 시작
                                          ├── 폐기: dismissed 표시 (재발견 시 알림 X)
                                          └── 스누즈: N일 후 다시 노출
```

- watchlist와 candidates는 **별도 테이블**. 후보 상태에서는 시세/공시 본격 수집 안 함 (리소스 절약).
- 동일 종목이 다른 스크리너에서 또 걸려도 중복 발화 안 함 (`source` 컬럼에 누적만).
- `candidate` 레코드는 발굴 시점 메타데이터 보존: 발굴 소스(어떤 스크리너 / LLM 추천), 발굴 시점 지표 스냅샷, 추천 사유 텍스트.

#### 데이터 모델 요지

- `screeners`: 스크리너 정의 (이름, 조건 JSON, market, 활성 여부)
- `candidates`: 발굴된 종목 후보 (instrument_id, source, score, reason, discovered_at, status[new/snoozed/promoted/dismissed])
- `fundamentals_snapshot`: 발굴 시점의 지표 스냅샷 (나중에 "그때 PER이 얼마였더라" 재현용)

#### 시장별 차이

- 한국(Phase 1): pykrx의 fundamental + FinanceDataReader의 StockListing으로 전체 풀 확보
- 미국(Phase 2): yfinance/Finnhub로 동일 패턴. 단 종목 수가 수천 개 → 1차 필터를 거래소/시총으로 축소

#### 안전장치

- LLM 추천 결과는 **항상 사람 검토 거쳐서 watchlist에 들어감** — 자동 승급 금지. LLM이 환각한 ticker가 직접 추적되는 사고 방지.
- 발굴된 종목의 상장폐지/거래정지 여부를 fundamentals 어댑터에서 검증 후 후보로 등록.

### 알림 룰

- DB에 룰 정의 저장. `alert_runner` 워커가 1분마다 평가.
- 동일 룰 중복 발화 방지: `last_triggered_at` + cooldown.
- 알림 채널: 텔레그램. 향후 확장 가능하게 추상화.
- 시장별 장중 시간에만 발화하는 룰 옵션 (예: 한국장 룰은 KRX 정규장 시간에만).

## Claude Code 작업 시 지침

### 기본 자세

- 본인은 C# 산업 자동화 백그라운드. Python/웹 생태계는 배우면서 만드는 중.
- 단순 답이 아니라 **왜 그렇게 하는지** 이유를 짚어주면 좋다.
- 모르는 부분을 추측하지 말고 명시적으로 질문하기.
- 한국어로 답변. 코드 주석은 영어 또는 한국어 (일관되게).

### 코드 생성 시

- **타입 힌트, 에러 처리, 로깅** 빠뜨리지 말 것.
- 새 종속성 추가 전에 기존에 비슷한 라이브러리 있는지 확인.
- 동기 라이브러리(pykrx 등)는 반드시 async 래퍼로 감싸기.
- 외부 API 호출은 재시도(`tenacity`) + 타임아웃 명시.
- **시간 처리**: DB에는 UTC 저장. 시장 시간 판단은 거래소 로컬 시간, 표시는 KST.
- **종목 식별자**: 항상 내부 표준 `{EXCHANGE}:{SYMBOL}` 사용. 외부 API 식별자는 어댑터 안에서만.
- **시장 가정 금지**: "한국이니까", "원화니까" 같은 가정을 코드에 박지 말 것. 시장별 분기가 필요하면 어댑터 패턴으로.

### 작업 시작 전

복잡한 작업은 먼저 계획을 보여주고 확인 받기:
1. 어떤 파일을 만들/수정할지
2. 어떤 종속성이 추가되는지
3. 마이그레이션이 필요한지

### 작업 완료 후

- 테스트가 있으면 실행 결과 보여주기
- 새 환경변수가 생겼다면 .env.example 갱신
- README 업데이트가 필요한지 확인

### 하지 말 것

- 자동매매 관련 코드 작성 금지 (스코프 외).
- 다른 사용자 계정 가정한 멀티테넌시 설계 금지 (본인용).
- 종토방/Reddit 글 본문 자체를 DB 외부(LLM 컨텍스트, API 응답 등)로 노출하지 말 것 (저작권/약관).
- 회원가입, 결제, 이메일 인증 등 상용 서비스 기능 만들지 말 것.
- **Phase 1 단계에서 미국장 데이터 소스 코드까지 미리 구현하지 말 것**. 다만 어댑터 인터페이스(추상 클래스)는 처음부터 분리해서, Phase 2에 구현체만 추가하면 되도록 설계.
- 한국 전용 가정(6자리 코드, 원화, KST)을 비즈니스 로직/스키마에 박지 말 것. 어댑터/표시 레이어로 밀어낼 것.

## 자주 쓰는 명령어

### 백엔드

```bash
# 개발 서버
cd backend && uvicorn app.main:app --reload --port 8000

# 마이그레이션 생성
alembic revision --autogenerate -m "add foo table"

# 마이그레이션 적용
alembic upgrade head

# 테스트
pytest -v

# 린트/포맷
ruff check .
ruff format .
```

### 프론트엔드

```bash
cd frontend
pnpm dev          # 개발 서버
pnpm build        # 프로덕션 빌드
pnpm lint
```

### Docker

```bash
docker compose up -d              # 전체 기동
docker compose logs -f backend    # 백엔드 로그
docker compose exec backend bash  # 컨테이너 진입
```

## 환경변수

`.env.example` 참조. 주요 항목:

### 공통
- `DATABASE_URL` — PostgreSQL 연결
- `REDIS_URL` — Redis 연결
- `ANTHROPIC_API_KEY` — Claude API
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — 알림
- `AUTH_PASSWORD` — 본인용 단순 인증
- `LOG_LEVEL` — `INFO` (기본), `DEBUG`
- `ENABLED_MARKETS` — 활성 시장 (예: `KR` 또는 `KR,US`). Phase 2부터 의미 있음.

### 한국 (Phase 1)
- `DART_API_KEY` — DART OpenAPI

### 미국 (Phase 2, 예정)
- `FINNHUB_API_KEY` — Finnhub
- `SEC_EDGAR_USER_AGENT` — SEC 요청용 (이름 + 이메일 의무)
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` — PRAW

## 참고 문서

아래 문서들은 **예정**. 작성 전에는 본 CLAUDE.md가 단일 진실의 출처(SSoT).

- `docs/architecture.md` — 아키텍처 상세 (예정)
- `docs/data-sources.md` — 외부 데이터 소스별 사용법 (예정)
- `docs/llm-context.md` — LLM 컨텍스트 어셈블러 규약 (예정)
- `docs/decisions/` — ADR (Architecture Decision Records, 예정)