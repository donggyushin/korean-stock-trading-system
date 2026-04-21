---
date: 2026-04-22
status: 승인됨
deciders: donggyu
related: [0012-monitor-notifier-design.md, 0011-apscheduler-adoption-single-process.md, 0003-runtime-error-propagation.md]
---

# ADR-0013: storage/db.py 모듈 설계 — SQLite 원장·Protocol 분리·silent fail 정책·DB 파일 분리

## 상태

승인됨 — 2026-04-22.

## 맥락

Phase 3 PASS 기준(plan.md:198) 은 "모의투자 연속 10영업일 무중단 + 0 unhandled error + **모든 주문이 SQLite 기록** + 텔레그램 알림 100% 수신" 이다. 알림(notifier.py) 과 오케스트레이터(main.py) 는 완료됐지만 영속화 계층이 없어 PASS 선언이 불가능했다. 운영자가 모의투자 중 "2번째 날 15:07 에 어떤 종목을 얼마에 샀고 체결가는 얼마였는지" 를 재구성하려면 loguru 파일 sink 를 grep 하는 데 의존해야 하고, 백테스트 대비 실전 괴리(슬리피지·누락 체결) 를 주간 회고에서 정량 비교할 원천 데이터도 없었다.

구현 경계에서 아래 다섯 가지 설계 결정이 필요했다.

1. **DB 파일 배치**: `data/historical.py` 가 이미 쓰는 `data/stock_agent.db` 에 테이블을 추가할지, 별도 파일로 분리할지.
2. **주입 경로**: Executor 가 recorder 를 직접 의존할지, main.py 콜백에서 notifier 와 나란히 호출할지(Protocol 의존성 역전 — ADR-0012 와 동일 기조).
3. **스키마 모델**: 진입·청산을 각각 테이블로 나눌지, 단일 `orders` 테이블에 `side` 컬럼으로 통합할지, round-trip `trades` 별도 테이블을 둘지.
4. **실패 정책**: DB INSERT 실패 시 예외를 전파할지(매매 루프 죽음) / silent fail(기록 누락 위험) — 콜백 4종 예외 re-raise 금지(ADR-0011 결정 5) 와의 관계.
5. **체결 원장의 PK**: 무엇을 주문 1건의 식별자로 쓸지. 기록 후 브로커 분쟁·불일치 조사 시 KIS 원본과 대조 가능해야 한다.

검토한 대안:
- **단일 DB 파일 공유** (`data/stock_agent.db` 에 테이블만 추가): 운영 복잡도는 낮지만 `historical.py` 의 v3 `schema_version` 분기 로직과 버전 공간이 얽혀 향후 마이그레이션이 복잡해진다. 캐시(재수집 가능) 와 원장(영구) 의 생명주기 차이도 합성 트랜잭션·락 경합 위험을 만든다.
- **ORM 도입** (SQLAlchemy/Peewee): 스키마 3개·컬럼 10여 개 규모에 비해 과도. `historical.py` 가 이미 stdlib `sqlite3` 직접 쓰는 일관성을 깬다.
- **round-trip `trades` 테이블 추가**: 진입·청산 쌍을 명시 행으로 관리. 현 시점 조회 요구는 `SELECT ... WHERE symbol=? AND session_date=?` 로 `orders` 에서 도출 가능 — YAGNI. v2 에서 뷰 또는 테이블로 마테리얼라이즈 가능.
- **예외 전파 정책**: `record_*` 실패 시 `StorageError` raise → 콜백이 캐치 → 로그. 명시적이지만 ADR-0011 결정 5 흐름에 중복 예외 처리를 추가하고, silent fail 패턴을 이미 검증한 notifier 와 비일관성이 생긴다.

추가 고려 사항 — `data/historical.py:140-147` 의 `isolation_level=None` + `BEGIN IMMEDIATE` 패턴 재사용, `monitor/notifier.py:250-258` 의 `_record_failure` / `consecutive_failure_threshold` 패턴 재사용. 외부 의존성 추가 0(stdlib `sqlite3` 전용).

## 결정

1. **모듈 경계 신설** — `src/stock_agent/storage/` 패키지. 공개 심볼: `TradingRecorder` (`@runtime_checkable` Protocol), `SqliteTradingRecorder`, `NullTradingRecorder`, `StorageError`. `Executor` 는 이 타입을 **모른다** — `main.py` 콜백 4종이 `StepReport.entry_events`·`exit_events` 와 `DailySummary` 를 notifier 와 나란히 `runtime.recorder.record_*` 로 포워딩. Protocol 의존성 역전은 ADR-0012 (notifier) 와 동일 기조.

2. **DB 파일 분리** — `data/trading.db` 신설. `data/historical.py` 의 `data/stock_agent.db` (OHLCV 캐시, 재수집 가능) 와 생명주기·책임이 달라 독립. 스키마 버전 공간 분리로 향후 마이그레이션 간섭 방지. `.gitignore` 의 `/data/` 로 자동 제외.

3. **스키마 v1 — 3 테이블 + 2 인덱스 + schema_version** — `orders(order_number PK, session_date, symbol, side CHECK IN ('buy','sell'), qty CHECK >0, fill_price TEXT, ref_price TEXT, exit_reason CHECK IN (stop_loss|take_profit|force_close)|NULL, net_pnl_krw INTEGER NULL, filled_at TEXT)` 에 `side` 컬럼으로 진입·청산 통합(round-trip 은 `SELECT ... WHERE symbol=?` 로 도출). `daily_pnl(session_date PK, starting_capital_krw, realized_pnl_krw, realized_pnl_pct, entries_today, halted CHECK IN (0,1), mismatch_symbols TEXT [JSON], recorded_at)` — 같은 날 재실행 시 `INSERT OR REPLACE`. `schema_version(version PK)` 로 향후 마이그레이션 분기. 인덱스 2종(`idx_orders_session`, `idx_orders_symbol`) — 일간·종목별 조회 가속.

4. **가격은 `TEXT` (Decimal 문자열)** — `historical.py` 의 `daily_bars.open/high/low/close` 와 동일 패턴. `Decimal` 정밀도 보존. SELECT 시 `Decimal(row[col])` 로 복원.

5. **PRAGMA — WAL·NORMAL·foreign_keys ON** — `journal_mode=WAL` (동시 읽기 + append 쓰기 성능, 파일 기반 한정 — `:memory:` 에서는 기본값 유지), `synchronous=NORMAL` (WAL 궁합, 충돌 시 최대 수초 손실 수용), `foreign_keys=ON` (향후 `trades` 등 관계 확장 대비). `isolation_level=None` autocommit + 스키마 init 한정 `BEGIN IMMEDIATE`.

6. **silent fail + 연속 실패 dedupe 경보** — `record_*` 내부의 `sqlite3.Error` 는 raise 하지 않고 `logger.warning`. `_consecutive_failures` 카운터가 `consecutive_failure_threshold`(기본 5) 에 도달하면 `logger.critical` 1회만 방출(`_critical_emitted` 플래그로 dedupe). 성공 1회 시 카운터·플래그 모두 리셋 → 다음 연속 실패가 다시 threshold 에 도달하면 critical 재방출. `monitor/notifier.py` 의 `_record_failure` 패턴과 동일 설계. 생성자의 스키마 init 실패는 `StorageError` 로 raise — 폴백(`NullTradingRecorder`) 선택은 호출자(`main._default_recorder_factory`) 책임.

7. **`NullTradingRecorder` 폴백** — `_default_recorder_factory` 가 `SqliteTradingRecorder` 조립 실패(`StorageError`/`RuntimeError`/`OSError`) 시 `NullTradingRecorder` 주입 + `logger.warning`. "영속화 부재가 세션 전체 실패보다 덜 위험" 이라는 판단 — 알림 부재에 대한 ADR-0012 결정 7 과 동일 기조. 로그(loguru sink) 가 여전히 유지되므로 사후 재구성 경로는 완전히 닫히지 않는다.

8. **`order_number` PK + EntryEvent/ExitEvent DTO 확장** — `OrderTicket.order_number` (KIS 주문번호, 드라이런은 `DRY-NNNN`) 를 `EntryEvent`·`ExitEvent` 에 필드로 추가. `@dataclass(frozen=True, slots=True)` + `__post_init__` 에서 빈 문자열·naive timestamp·0 이하 qty/price 거부 (위반 시 `RuntimeError`, ADR-0003 기조). `_handle_entry`/`_handle_exit` 가 `ticket.order_number` 를 주입. 감사 추적·분쟁 조사 시 KIS 원본과 1:1 대조.

9. **드라이런 구분 없음** — `SqliteTradingRecorder` 는 실전·드라이런 공통. `order_number` 가 `DRY-*` 프리픽스로 구분되므로 별도 DB 파일·테이블 분리 불필요. 후속 조회(`SELECT ... WHERE order_number LIKE 'DRY-%'`) 로 필터링.

10. **close 멱등 + 컨텍스트 매니저** — `__enter__`/`__exit__` + `close()` 중복 호출 안전(`_closed` 플래그). `_graceful_shutdown` + `main()` finally 블록 양쪽에서 호출해도 부작용 없음. close 이후 `record_*` 호출은 warning 1회 + silent, 카운터 불변 (세션 종료 내구성).

## 결과

**긍정**
- Executor 의 Protocol 의존성 역전(ADR-0012 와 동일 기조) 보존 — 드라이런·단위 테스트 경로가 추가 분기 없이 유지됨.
- Phase 3 PASS 조건 중 "모든 주문이 SQLite 기록" 이 자동화 — 로그 grep 의존 제거, 주간 회고에서 백테스트 대비 실전 괴리 정량 비교 가능.
- `historical.py` 와 DB 파일·스키마 버전 공간 독립 — 캐시 삭제·재생성이 원장을 건드리지 않는다. 향후 PostgreSQL 전환(plan.md:71) 시 원장만 마이그레이션 대상.
- 외부 의존성 추가 0 — stdlib `sqlite3` 전용. `notifier.py` 의 `python-telegram-bot` 처럼 새로운 라이브러리 유입 없이 Phase 3 를 마무리.
- `EntryEvent`/`ExitEvent` DTO 가드 추가(`__post_init__`) 로 naive datetime·빈 `order_number`·음수 수량 같은 경로를 타입·런타임 양쪽에서 차단. 기존 63건 test_executor 회귀 0(`order_number` 주입만 추가).

**부정**
- `EntryEvent`/`ExitEvent` DTO 변경이 notifier 쪽 테스트 헬퍼(`_make_entry_event`·`_make_exit_event`) 도 함께 갱신을 요구했다 — 단발 비용이지만 일관성 유지 부담.
- silent fail 이 운영자 진단 난이도를 높이는 부작용(notifier 와 동일). 연속 실패 경보가 완화하지만 "일시적 락 경합 1건" 같은 드문 누락은 loguru sink 확인 필요.
- `ref_price` 를 exit 행에도 NOT NULL 로 두어 `ExitEvent.fill_price` 를 복사해 저장한 것은 모델 일관성 위해 감수한 타협. v2 에서 컬럼 nullable 전환 또는 별도 `exit_orders` 테이블로 분리 고려 가능.

**중립**
- `order_number` 를 PK 로 쓰면 같은 session 에서 KIS 가 재사용하는 사건(일반적으로는 없음) 에서 재INSERT 가 silent fail 로 흡수된다. 실무 운영에서는 문제되지 않지만 테스트에서는 `TestPrimaryKeyConflict` 로 동작을 명시 고정.
- `daily_pnl` 의 `INSERT OR REPLACE` 는 "같은 날 재실행 시 마지막 값 유지" 계약 — 운영자가 장중 프로세스를 재시작했을 때 덮어쓰기 vs 다중 행 보존의 선택이었다. 현재 `session_date` 를 PK 로 고정했으므로 재시작 시 마지막 실행이 정본.

## 추적

- 코드: `src/stock_agent/storage/__init__.py`, `src/stock_agent/storage/db.py`, `src/stock_agent/execution/executor.py` (DTO 확장), `src/stock_agent/main.py` (Runtime·팩토리·콜백·close).
- 테스트: `tests/test_storage_db.py`, `tests/test_executor.py`, `tests/test_notifier.py`, `tests/test_main.py`.
- 문서: [src/stock_agent/storage/CLAUDE.md](../../src/stock_agent/storage/CLAUDE.md), root [CLAUDE.md](../../CLAUDE.md), [plan.md](../../plan.md), [src/stock_agent/execution/CLAUDE.md](../../src/stock_agent/execution/CLAUDE.md).
- 도입 PR: TBD (Phase 3 네 번째 산출물).
