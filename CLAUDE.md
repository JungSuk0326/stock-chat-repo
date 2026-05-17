# CLAUDE.md

이 파일은 Claude Code가 이 프로젝트에서 작업할 때 참고하는 컨텍스트입니다.

## 프로젝트 개요

**Stock Monitor + LLM Advisor** — 개인용 주식 모니터링 웹 앱. 차트, 공시, 뉴스, 종토방 데이터를 통합하고, 현재 보고 있는 종목의 컨텍스트를 자동으로 LLM에 주입해서 자연스럽게 상담할 수 있게 한다.

본인 1명이 사용. 자동매매 없음. 한국 주식만 (KOSPI/KOSDAQ).

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
- pykrx, FinanceDataReader (시세)
- DART OpenAPI (공시)
- 네이버 금융 (실시간 시세, 뉴스, 종토방)
- RSS 피드 (한경, 매경, 연합인포맥스)

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
│   │   │   ├── market.py        # 시세 관련
│   │   │   ├── disclosure.py    # 공시
│   │   │   ├── news.py
│   │   │   ├── board.py         # 종토방
│   │   │   ├── llm.py           # LLM 어셈블러
│   │   │   └── alert.py
│   │   ├── workers/             # 백그라운드 워커
│   │   │   ├── price_poller.py
│   │   │   ├── disclosure_watcher.py
│   │   │   ├── news_collector.py
│   │   │   ├── board_crawler.py
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
- **import 순서**: stdlib → 3rd party → app local. isort 기준.
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

- **브랜치**: `main` (배포) ← `dev` (통합) ← `feature/*`, `bugfix/*`
- **커밋 메시지**: Conventional Commits (feat:, fix:, refactor:, chore:, docs:, test:)
- 한국어 또는 영어 모두 가능, 일관되게.

### 일반

- **변수명/함수명은 영어**. 한국어 주석은 OK.
- **민감 정보는 .env**. .env는 절대 커밋 안 함. .env.example로 템플릿만 관리.
- **DB 마이그레이션**: 직접 SQL 수정 X. 반드시 Alembic으로 생성.
- **종속성 추가 시**: 사유를 PR 또는 커밋 메시지에 명시. lockfile 갱신.

## 도메인 지식

### 종목 코드

- 6자리 숫자 문자열 (예: '005930' 삼성전자)
- 앞에 0 빼지 말 것. `005930` ≠ `5930`

### 시장 시간 (KST)

- 정규장: 09:00 ~ 15:30
- 시간외: 15:40 ~ 16:00 (단일가), 16:00 ~ 18:00 (시간외 단일가)
- 종토방, 뉴스는 24시간 수집. 시세는 정규장 기준.

### 데이터 소스 주의사항

- **pykrx**: 장 마감 후 일봉 데이터 조회용. 장중 실시간 X.
- **네이버 금융 모바일 API**: 비공식. 안정적이지만 약관상 회색 지대. 본인용이라 OK.
- **DART**: 분당 호출 제한 관대. corp_code로 종목코드 매핑 필요.
- **종토방**: robots.txt 존중. 요청 간 sleep 1초. User-Agent 명시.

### LLM 컨텍스트 어셈블러

- `app/services/llm.py:assemble_context()` 가 핵심.
- 호출 시점에 가장 최신 데이터로 컨텍스트 빌드.
- 컨텍스트 크기 ~1500 토큰 목표. 초과 시 우선순위 낮은 정보부터 truncate.
- 종토방 글 본문은 절대 컨텍스트에 안 넣음. 집계 지표만.

### 알림 룰

- DB에 룰 정의 저장. `alert_runner` 워커가 1분마다 평가.
- 동일 룰 중복 발화 방지: `last_triggered_at` + cooldown.
- 알림 채널: 텔레그램. 향후 확장 가능하게 추상화.

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
- 시간 처리: DB에는 UTC 저장, 표시할 때 KST 변환.

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
- 종토방 글 본문 자체를 DB 외부로 노출하지 말 것 (저작권/약관).
- 회원가입, 결제, 이메일 인증 등 상용 서비스 기능 만들지 말 것.

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

- `DATABASE_URL` — PostgreSQL 연결
- `REDIS_URL` — Redis 연결
- `ANTHROPIC_API_KEY` — Claude API
- `DART_API_KEY` — DART OpenAPI
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — 알림
- `AUTH_PASSWORD` — 본인용 단순 인증
- `LOG_LEVEL` — `INFO` (기본), `DEBUG`

## 참고 문서

- `docs/architecture.md` — 아키텍처 상세
- `docs/data-sources.md` — 외부 데이터 소스별 사용법
- `docs/llm-context.md` — LLM 컨텍스트 어셈블러 규약
- `docs/decisions/` — ADR (Architecture Decision Records)