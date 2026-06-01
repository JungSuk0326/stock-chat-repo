# CLAUDE.md

이 파일은 Claude Code가 이 프로젝트에서 작업할 때 참고하는 컨텍스트입니다.

## 프로젝트 개요

**Stock Monitor + LLM Advisor** — 개인용 주식 모니터링 웹 앱. **실시간 시세** + 차트 + 공시 + 뉴스 + 커뮤니티 데이터를 통합하고, 현재 보고 있는 종목의 컨텍스트를 자동으로 LLM에 주입해서 자연스럽게 상담할 수 있게 한다. 단타 분석/스윙도 가능한 호흡으로 시세를 본다.

본인 1명이 사용. **자동 주문 실행 없음** (시세 모니터링과 LLM 상담만, 매매 결정은 본인이).

### 시장 확장 로드맵

- **Phase 1 (현재)**: 한국 주식 (KOSPI/KOSDAQ) — MVP, 인프라/UI/LLM 어셈블러 검증
- **Phase 2 (예정)**: 미국 주식 (NYSE/NASDAQ) — yfinance, SEC EDGAR, 영문 뉴스/Reddit 추가
- **Phase 3 이후**: 일본/홍콩, 유럽 (필요 시)

**중요**: Phase 1만 구현하더라도 코드/스키마는 처음부터 멀티마켓을 전제로 설계한다. 종목 식별자, 시간대, 통화, 데이터 소스 추상화를 한국 전용으로 박아두지 말 것. 자세한 규약은 아래 [도메인 지식](#도메인-지식) 참조.

## 현재 진행 상태

마지막 갱신: 2026-06-02 (R1 헬스 메트릭 + R5 DB 백업까지)

### 기반 구조
- [x] CLAUDE.md 초안 + 멀티마켓/발굴 섹션 + Skill routing
- [x] 인프라 컨테이너 — PostgreSQL 16 + TimescaleDB + Redis (docker-compose)
- [x] backend 스캐폴딩 — FastAPI, pydantic-settings, structlog, lifespan
- [x] SQLAlchemy 2.0 async + asyncpg + redis-py 연결, `/health`에 의존성 ping
- [x] backend/worker Docker 이미지 + compose 통합 (R4 결정 + 코드 반영)
- [x] Alembic async 셋업 + 마이그레이션 워크플로우
- [x] 리스크 리뷰 문서화 ([docs/risks-2026-05-21.md](docs/risks-2026-05-21.md), 16개 항목 추적)

### DB 스키마 (멀티마켓 전제)
- [x] `instruments` 테이블 — exchange/symbol/country/currency/market/isin/name
- [x] `prices` hypertable — TimescaleDB 7d chunk + 30d 이후 자동 압축
- [x] `watchlist` 테이블 — 사용자 관심종목 (단일 사용자, UNIQUE instrument_id)
- [x] `corp_codes` 테이블 — 규제기관 corp-id ↔ (exchange, symbol) 매핑 (DART corp_code, 추후 SEC CIK)
- [x] `disclosures` 테이블 — instrument_id FK + source/source_id + filed_at(UTC) + title/submitter/raw_url
- [x] `users` 테이블 — 멀티 유저 골격 (현재 owner row 1개), 새 테이블은 user_id FK 포함
- [x] `chat_sessions` 테이블 — user_id + instrument_id + title, INDEX(user_id, instrument_id, updated_at)
- [x] `chat_messages` 테이블 — session_id FK + role/content + LLM accounting (model/tokens), CASCADE delete
- [x] `alert_rules` 테이블 — user_id + instrument_id + condition_type + threshold + cooldown_minutes + market_hours_only + last_triggered_at
- [x] `alert_events` 테이블 — rule_id FK + fired_at + triggered_value + channel + delivery_status (발화 이력/디버깅)
- [ ] `news_items`, `community_signals` 테이블
- [ ] `screeners`, `candidates`, `fundamentals_snapshot` 테이블 (발굴)

### KR 데이터 수집 (Phase 1)
- [x] `KrMarketAdapter.fetch_instruments()` — FDR로 KOSPI/KOSDAQ 2,649개 적재
- [x] `KrMarketAdapter.fetch_eod_prices()` — pykrx로 일봉 OHLCV
- [x] `app/scripts/sync_instruments.py`, `sync_prices.py` 수동 실행 스크립트
- [x] 실시간 시세 워커 — 2초 폴링, Redis 캐시/Pub-Sub, 1분봉 적재
- [x] WebSocket `/ws/prices/{exchange}/{symbol}` (4404 close code, 자동 cleanup)
- [x] 프론트엔드 라이브 차트 — WS 자동 재연결(지수 백오프) + 오늘 봉 실시간 업데이트 + LIVE/재연결 중 뱃지
- [x] **다종목 watchlist** — DB watchlist 테이블 + CRUD API + 동적 워커 (30s sync, add/remove auto)
- [x] **종목 검색 + URL 라우팅** — `?symbol=KR:000660`, 사이드바 + SearchModal
- [x] **신규 watchlist 종목 자동 EOD backfill** — reconcile 시점에 1년치 일봉 fire-and-forget
- [x] **매일 EOD 일봉 sync** — 16:00 KST cron, watchlist 전체 종목 최근 7일 (멱등 UPSERT)
- [x] **매일 instruments 갱신** — 06:00 KST cron, FDR로 KOSPI/KOSDAQ 전 종목 마스터 UPSERT
- [x] **DART corp_code 동기화** (R11) — 매일 05:30 KST cron, ZIP/XML 파싱 → 상장사 ~3,900개
- [x] **DART 공시 수집 워커** — 1분 폴링, watchlist 전체 종목 [today-1, today] KST
- [x] **신규 watchlist 종목 공시 backfill** — 가입 시점 6개월치 fire-and-forget
- [x] **알림 평가 워커** — 1분 폴링, 활성 룰 전체 평가, cooldown 적용, 발화 시 채널로 발송
- [x] **헬스체크 메트릭 (R1)** — `app/workers/heartbeat.py`, 6개 잡 + backup 추적, `/health.workers` 노출, stale → 503
- [x] **DB 백업 워커 (R5)** — `app/workers/backup.py`, 매일 03:30 KST `pg_dump | gzip`, BACKUP_RETENTION_DAYS 자동 정리, 오프사이트는 호스트 hyper backup
- [ ] 네이버 금융 뉴스 + RSS 수집
- [ ] 종토방 크롤링 + 감성 분류 (claude-haiku)

### API / UI
- [x] `GET /prices/{exchange}/{symbol}` + CORS
- [x] `GET /ws/prices/{exchange}/{symbol}` WebSocket (Redis Pub/Sub fan-out)
- [x] `GET /instruments?q=...&market=...&limit=20` 검색
- [x] `GET/POST/DELETE /watchlist` CRUD
- [x] 프론트엔드 첫 차트 — Next.js 16 + lightweight-charts
- [x] 프론트엔드 실시간 연동 — 라이브 뱃지, 자동 재연결, tick→오늘 봉 업데이트
- [x] 프론트엔드 watchlist 사이드바 + 검색 모달 + URL 라우팅(`?symbol=...`)
- [x] **LLM 상담 UI** (`ChatPanel`) — 세션 드롭다운 + "새 대화" + 삭제, URL ?session= 라우팅, 모델 선택 localStorage
- [x] `GET /llm/models` — 카탈로그 (provider 키 있는 모델만 노출) + default 정보
- [x] `POST /chat` — provider/model 선택, 컨텍스트 자동 주입, session_id 처리 (3 모드: 이어쓰기/자동생성/ephemeral), **multi-turn tool use**
- [x] **`GET/POST/DELETE /chat/sessions`** — 세션 CRUD (owner-scoped, get_current_user_id 의존성)
- [x] **`POST /chat/tool-confirm`** — LLM이 제안한 쓰기 도구를 사용자 확인 후 실행
- [x] **공시 UI** (`DisclosurePanel`) — 차트 아래 시간순 리스트, 60초 자동 새로고침, 제목 클릭 → DART 뷰어
- [x] `GET /disclosures/{ex}/{sym}` — 종목별 최근 공시 (헤드라인만)
- [x] **알림 UI** (`AlertsPanel`) — 조건 드롭다운 + 임계값 + 이름, 켜기/끄기 토글, 삭제, 30초 자동 새로고침 + ChatPanel 확인 시 CustomEvent로 즉시 반영
- [x] `GET/POST/PATCH/DELETE /alerts` + `GET /alerts/{id}/events` — owner-scoped CRUD + 발화 이력
- [x] **차트 지표 토글** (`ChartSettings`) — SMA(5/20/60/120) / EMA(12/26) / Bollinger(20,2σ) / RSI(14, 별도 페인), localStorage 영속
- [x] **AI 알림 등록 (`ChatPanel` ActionCard)** — 자연어로 "삼성 320k 알림" → 확인 카드 → 실제 등록. 환각 안전망
- [ ] 발굴 후보 UI

### LLM
- [x] **LLM 비용 hard cap (R2)** — Redis 기반 daily/monthly 통합 카운터, provider 무관
- [x] **`assemble_context()` 어셈블러** — 현재가/추세/MA/RSI/공시 헤드라인 자동 빌드
- [x] **Multi-provider 추상화** — `LLMClient` ABC + `LLMRegistry` + `catalog` 정적 모델 목록
- [x] **Anthropic + Gemini 지원** — Claude Opus/Haiku 4.5/4.7, Gemini 2.5 Pro/Flash (4종)
- [x] **provider/model 사용자 선택** — UI 드롭다운, 호출마다 클라이언트가 선택
- [x] **공시 컨텍스트 합류** — 최근 14일 / 최대 20건, 헤드라인 + 제출인만 (본문 X)
- [x] **채팅 세션 영속화** — `chat_sessions` + `chat_messages` DB 저장, 새로고침/디바이스 간 동기화
- [x] **세션 자동 타이틀** — 첫 메시지 교환 직후 Gemini Flash로 ~30자 요약 (백그라운드, fail-soft)
- [x] **LLM tool-use 인프라** — `LLMClient.ask(tools=...)`, Anthropic + Gemini 정규화, multi-turn round-trip (`ChatMessage.tool_calls`/`tool_results`)
- [x] **알림 도구 3종** (`app/llm/tools.py`) — `list_alerts` (READ, 즉시) / `create_alert_rule` (WRITE, 확인 후) / `delete_alert` (WRITE, 확인 후). 실행 함수는 `app.services.alert_rules`와 동일 경로 공유
- [ ] 뉴스 헤드라인 합류 (뉴스 수집 후)
- [ ] 종토방 감성 집계 합류 (감성 분류 후)
- [ ] 종목 발굴 — 조건 스크리닝
- [ ] 종목 발굴 — LLM 기반 추천

### 운영 / 보안 / 배포
- [x] **Top4 — 알림 시스템 (텔레그램)** — 4종 조건 + cooldown + 채널 추상화 + 수동 UI
  - [x] Phase A: `alert_rules` + `alert_events` 테이블 + ORM + Alembic env.py 필터
  - [x] Phase B: `tick_alert_runner` (1분) + 조건 평가 + cooldown + LogChannel
  - [x] Phase C: TelegramChannel (HTML parse_mode) + 설정 기반 채널 선택 + fallback
  - [x] Phase D: `/alerts` CRUD REST + `AlertsPanel` UI
  - [x] Phase E: 텔레그램 실제 발송 검증 완료
- [x] **Top5 — AI 기반 알림 생성** — LLM tool-use, 확인 카드 안전망. 자세한 설계는 [도메인 지식 > 알림 룰 > Top5](#top5--ai-기반-알림-생성-예정-top4-완료-후) 참조
  - [x] Phase A: `LLMClient.ask(tools=...)` + Anthropic + Gemini 어댑터 + `ToolDef`/`ToolCall`/`ToolResult`
  - [x] Phase B: `app/llm/tools.py` 도구 3종 + `/chat` multi-turn loop + `/chat/tool-confirm` 엔드포인트
  - [x] Phase C: `ChatPanel` 확인 카드 (`ActionCard`) UI + CustomEvent로 `AlertsPanel` 즉시 반영
- [x] **R1 헬스 메트릭** — 워커 잡별 heartbeat (Redis), `/health.workers` 잡 상태 노출, stale → 503
- [x] **R5 DB 백업 자동화** — 매일 03:30 KST `pg_dump | gzip` → `./backups/`, 14일 보존, 오프사이트는 호스트 hyper backup 단계
- [ ] Cloudflare Access 인증 (R3 — 외부 배포 직전 필수)
- [ ] 시놀로지 NAS 배포

### Phase 2 이상
- [ ] 미국장 데이터 소스 통합 (yfinance, SEC EDGAR, Finnhub)
- [ ] 일본/홍콩/유럽 (필요 시)

## 기술 스택

### Backend
- Python 3.11+ / FastAPI / asyncio
- SQLAlchemy 2.0 (async) / Alembic
- PostgreSQL 16 + TimescaleDB
- Redis (cache + pub/sub)
- APScheduler (배치/스케줄링)
- httpx (외부 API 호출, async)

### Frontend
- Next.js 16 (App Router, Turbopack) / React 19
- TypeScript (strict mode)
- Tailwind CSS v4
- Lightweight Charts (TradingView) v5
- 실시간 시세: 브라우저 WebSocket → `/ws/prices/{exchange}/{symbol}`
- Zustand (상태관리)

### LLM

Multi-provider 구조. `LLMClient` ABC 뒤에 각 provider 구현을 두고, `LLMRegistry`가 키가 설정된 provider만 부팅한다. 사용자가 `/llm/models` 카탈로그에서 모델을 골라 `/chat` 호출 시 `provider/model` 필드로 전달.

- **현재 카탈로그** (`app/llm/catalog.py`):
  - Anthropic: `claude-opus-4-7` (premium), `claude-haiku-4-5` (lite) — `anthropic` SDK
  - Gemini: `gemini-2.5-pro` (premium), `gemini-2.5-flash` (standard) — `google-genai` SDK
- **기본값**: `LLM_DEFAULT_PROVIDER=gemini`, `LLM_DEFAULT_MODEL=gemini-2.5-pro` (.env로 조정 가능)
- **공통 비용 cap (R2)**: `LLMBudget`이 Redis로 daily/monthly 토큰을 **provider 통합**으로 누적. 초과 시 호출 전 `LLMBudgetExceeded` raise → 429.
- **Tool use**: `LLMClient.ask(tools=[ToolDef(...)])`로 모든 provider가 동일 인터페이스. `AskResult.tool_calls`로 정규화된 결과. multi-turn round-trip은 `ChatMessage.tool_calls` / `tool_results`가 들고 다님. 도구 카탈로그는 `app/llm/tools.py`에 정의 + 실제 실행 함수는 도메인 service에서 import (예: 알림 도구는 `app/services/alert_rules` 사용).
- **확장**: 새 provider 추가 시 `LLMClient` 상속 구현체 + `catalog.py`에 한 줄 + `registry.from_settings()` 분기 한 줄. UI/스키마 변경 불필요.
- **BYOK 미래**: 단일 사용자 기준 operator 키만 .env에 두지만, 패턴은 그대로 두면 user_id 차원 추가 + TTL 캐시만으로 BYOK로 확장 가능.

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

## 워커 토폴로지

백엔드 프로세스는 **두 개로 분리**한다. 같은 코드 저장소, 같은 Docker 이미지, 진입점(`command`)만 다르게 띄움.

### 서비스 구성

| 서비스 | 역할 | 외부 노출 | 인스턴스 수 |
|--------|------|-----------|-------------|
| `backend` | FastAPI HTTP/WebSocket (`app.main:app`) | ✓ (Cloudflare Tunnel) | N (stateless, 부하 따라 늘림) |
| `worker` | APScheduler 기반 배치 (`app/workers/*`) | ✗ | 1 (singleton) |

`docker-compose.yml`에서 `build`는 동일, `command`만 다르게:

```yaml
backend:
  build: ./backend
  command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
  ports: ["8000:8000"]

worker:
  build: ./backend
  command: uv run python -m app.workers.runner
  # ports 노출 X — 외부 공격면 차단
```

### 왜 분리하는가

- `uvicorn --reload` 시 워커 영향 X (개발 사이클 빠름)
- API 다중 인스턴스 띄워도 워커 스케줄 중복 실행 X
- 워커 OOM이 API를 죽이지 않음 (사용자 체감 가용성↑)
- 워커 코드는 외부에서 절대 접근 불가 (공격면 축소)

### Singleton 보장 (실수로 워커 2개 떠도 안전)

워커 컨테이너가 실수로 다중 기동되어도 동일 작업이 두 번 실행되지 않도록 Redis 락으로 보호:

```python
acquired = await redis.set(f"lock:{job_name}", "1", nx=True, ex=ttl)
if not acquired:
    return  # 다른 워커가 처리 중, 이번 tick은 skip
try:
    ...  # 실제 작업
finally:
    await redis.delete(f"lock:{job_name}")
```

각 워커는 자기 작업 예상 시간 + 여유로 TTL 설정. 작업 끝나면 명시적 해제.

### 진입점

- `backend/app/main.py` — FastAPI 앱 (이미 존재)
- `backend/app/workers/runner.py` — APScheduler 부팅 + 모든 워커 job 등록 (백엔드 도메인 코드 작성 시점에 추가)

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

### 실시간 시세 흐름 (KR)

목표: 장중 시세를 ~2초 단위로 모니터링하고, 차트가 페이지 새로고침 없이 움직인다. 단타 분석/스윙 의사결정 보조용.

#### 데이터 경로

```
[Naver Mobile API]                                                       
    ↓ (장중 2초마다 polling)                                              
[worker: price_poller]                                                  
    │                                                                    
    ├─→ Redis SET  "price:{EXCHANGE}:{SYMBOL}"   TTL 60s   (현재가 캐시) 
    ├─→ Redis PUB  "ticks.{EXCHANGE}.{SYMBOL}"            (실시간 fan-out)
    └─→ 분 경계마다 INSERT prices(interval='1m', ...)      (히스토리 영속)
                                                                          
                          ┌──── Redis SUBSCRIBE                          
                          │                                              
[FastAPI WebSocket]  ──── ┘                                              
GET /ws/prices/{exchange}/{symbol}                                       
    ↓ (서버 → 브라우저 push)                                              
[Browser]  ─── chart.update(tick)                                        
```

#### 명명 규약

- Redis 키 (캐시): `price:{EXCHANGE}:{SYMBOL}` (예: `price:KR:005930`)
  - 값: JSON `{"close": ..., "volume_cum": ..., "ts": "..."}`
  - TTL 60초 — 워커 죽으면 자동 만료
- Redis 채널 (Pub/Sub): `ticks.{EXCHANGE}.{SYMBOL}` (예: `ticks.KR.005930`)
- WebSocket 경로: `/ws/prices/{exchange}/{symbol}` (예: `/ws/prices/KR/005930`)

#### 폴링 주기 & 시장 시간

- **KR 정규장 (09:00~15:30 KST)**: 2초 (실시간 체감 + Naver IP 차단 위험 낮춤)
- **시간외/휴장**: 폴링 중단 (Naver API 부하 + 약관 회색지대 줄임)
- 시장 시간 판단: 일단 `zoneinfo` + 요일/시간 단순 판단. 정확한 휴장(R15)은 `pandas_market_calendars` 또는 KRX 휴장 API로 추후 보강.

#### 1분봉 집계

워커가 2초마다 tick을 메모리 버퍼에 누적 → 분 경계 도달 시 OHLCV로 집계 → `prices(interval='1m')` UPSERT.

- `open` = 분의 첫 tick close
- `high` = 분 내 max
- `low` = 분 내 min
- `close` = 분의 마지막 tick close
- `volume` = (분 끝 누적거래량) - (분 시작 누적거래량). Naver API는 누적 거래량 반환.

#### Singleton 보장

워커 컨테이너가 실수로 다중 기동되어도 한 종목당 하나만 폴링하도록 Redis 락:
```
SET "lock:poller:{EXCHANGE}:{SYMBOL}" "<worker-id>" NX EX 5
```
TTL 5초로 짧게 — 폴링 간격(2초)보다 길고, 워커 죽으면 곧 만료. 안전한 fail-over.

#### 절대 하지 말 것

- tick 단위 영속화 (양 폭발, 가치 낮음). 1분봉 이하 해상도는 메모리 버퍼만.
- 종토방/뉴스를 같은 채널로 발행 (도메인 분리, 각자 채널).
- 워커가 죽으면 silent. 헬스 메트릭(R1) 마련 시 success rate 추적.

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

#### Top5 — AI 기반 알림 생성 (완료, 2026-06-01)

채팅에서 자연어로 알림을 추가/조회/삭제할 수 있다. LLM은 의도 파악 + 인자
추출만 하고, 실행은 백엔드 함수가 한다. 쓰기 작업은 사용자 확인 카드를 거쳐
실제 DB에 INSERT.

구현:
- `LLMClient.ask(system, messages, model, tools)` — Anthropic + Gemini 모두
  같은 시그니처로 추상화. provider별 SDK 차이(`tool_use` blocks vs
  `function_call` Parts)는 어댑터 안에서 변환
- 노출 도구 3종 (`app/llm/tools.py`):
  - `list_alerts(exchange?, symbol?)` — READ, 즉시 실행, 결과를 tool_result로
    LLM에 다시 던져 자연어 답변 받음
  - `create_alert_rule(exchange, symbol, condition_type, threshold, name?,
    cooldown_minutes?, market_hours_only?)` — WRITE, 확인 카드
  - `delete_alert(rule_id)` — WRITE, 확인 카드
- 실행 함수는 `app/services/alert_rules.py`의 기존 서비스 재사용 — UI 폼과
  LLM 도구가 같은 검증/INSERT 경로 공유, 단일 진실
- `POST /chat`은 multi-turn loop (max 3 라운드):
  - 읽기 호출이면 실행 → tool_result 메시지 추가 → LLM 재호출
  - 쓰기 호출이면 즉시 중단, `pending_actions`로 응답 → 프론트가 확인 카드 띄움
- `POST /chat/tool-confirm`은 같은 executor를 호출. 사용자 [확인] 없이는
  DB가 절대 변하지 않음
- 안전장치:
  - `MAX_WRITE_CALLS_PER_RESPONSE=3` — 한 응답에서 쓰기 호출 폭주 방지
  - system prompt에 "되묻기 금지, 도구 직접 호출 + 시스템이 자동 카드 띄움"
    명시 → 모델이 자연어로 "등록할까요?" 되묻는 행동 억제
  - JSON Schema `enum`으로 `condition_type` 4개 강제
  - 종목 코드는 컨텍스트의 instrument에서 가져옴 (system prompt 가드)

Top4의 D(수동 UI)는 fallback으로 살려둠 — LLM이 망가지거나 임계값 미세조정
때 폼 직접 사용.

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
- `ANTHROPIC_API_KEY` — Anthropic Claude API (비워두면 카탈로그에서 자동 제외)
- `GEMINI_API_KEY` — Google Gemini API (비워두면 카탈로그에서 자동 제외)
- `LLM_DEFAULT_PROVIDER`, `LLM_DEFAULT_MODEL` — 클라이언트가 미지정 시 사용할 기본값
- `LLM_DAILY_TOKEN_CAP`, `LLM_MONTHLY_TOKEN_CAP` — provider 통합 토큰 한도 (R2)
- `LLM_MAX_OUTPUT_TOKENS` — 응답 1회당 출력 토큰 cap
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — 알림 (텔레그램 봇 발행 + getUpdates로 chat_id 획득)
- `ALERT_CHANNEL` — `"log"`(기본, dev) 또는 `"telegram"`. telegram + 토큰/chat_id 모두 설정 시에만 실제 발송, 아니면 log fallback
- `BACKUP_DIR` — 매일 `pg_dump | gzip` 출력 경로 (기본 `/backups`, docker compose에서 `./backups`로 마운트). 호스트 워커로 돌릴 때는 별도 경로 + 로컬 `pg_dump` major 버전이 서버와 일치해야 함 (현재 서버 = pg16, Homebrew 기본 14는 안 됨 → `brew install postgresql@16`)
- `BACKUP_RETENTION_DAYS` — 보존 일수 (기본 14)
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

아래 문서들 중 (예정) 표시된 건 미작성. 작성된 문서가 우선, 그 외는 CLAUDE.md가 단일 진실의 출처(SSoT).

- [`docs/risks-2026-05-21.md`](docs/risks-2026-05-21.md) — 구현 리스크 & 보완 사항 리뷰 (작성됨, 처리 진행 중)
- `docs/architecture.md` — 아키텍처 상세 (예정)
- `docs/data-sources.md` — 외부 데이터 소스별 사용법 (예정)
- `docs/llm-context.md` — LLM 컨텍스트 어셈블러 규약 (예정)
- `docs/decisions/` — ADR (Architecture Decision Records, 예정)

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore