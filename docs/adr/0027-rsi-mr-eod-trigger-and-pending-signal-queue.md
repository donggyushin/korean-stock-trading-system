---
date: 2026-05-04
status: 승인됨
deciders: donggyu
related: [0023-rsi-mr-strategy-adoption-conditional.md, 0025-rsi-mr-operational-risk-limits.md]
---

# ADR-0027: RSI MR 운영 — EOD 일봉 트리거 + 다음 영업일 시초 진입 큐

## 상태

승인됨 — 2026-05-04. ADR-0025 PR3 까지의 wiring 으로는 진입·청산 신호가 발생하지 않는 결함을 운영 첫날(2026-05-04) 확인. 본 ADR 은 RSI MR 일봉 전략의 운영 트리거 모델을 PR5 로 확정한다.

## 맥락

### 운영 첫날 결함 노출

ADR-0023 으로 1차 채택 후보 확정된 `RSIMRStrategy` 를 ADR-0025 PR2~PR3 에서 `main.py` 에 wiring 한 후 2026-05-04 (월) 첫 영업일 운영 결과:

- `executor.entry.filled` 0건 / `executor.exit.filled` 0건 / `evaluate_entry` 호출 0건.
- `main.step processed=0 orders=0` 매분 반복.
- ConnectionError 일시 2건 — `_with_backoff` 정상 회복.

근본 원인: `main.py` 의 cron 4종 (RSI MR 모드 3종) 이 분봉 step·session_start·daily_report 만 등록. **EOD 일봉 트리거 cron 이 없다**. `RSIMRStrategy.on_bar(bar: MinuteBar)` 는 시그니처상 분봉 DTO 를 받지만 의미는 일봉 1건 처리(`strategy/CLAUDE.md` rsi_mr 섹션 명시). 분봉 step 만 돌고 보유 0 → 분봉 구독 0(PR4 정상) → `strategy.on_bar` 호출 0 → 시그널 0 → 진입 0.

`main.py` 가 RSI MR 전략을 실제로 호출하지 않는 빈 깡통 상태로 운영 진입했다.

### RSI MR 의 운영 모델 (ADR-0025 명시)

ADR-0025 가 운영 모델을 다음과 같이 서술했다:

- 매일 장 마감 후(또는 장 마감 직전) 전일 일봉을 수신하면 `on_bar` 를 호출해 진입/청산 시그널을 결정한다.
- 시그널이 있으면 다음 장 개장 초 Executor 가 주문을 제출하고 분봉 단위로 체결을 추적한다.
- 일중 강제청산(`force_close_at`)은 일봉 전략에 적합하지 않다.

ADR-0025 는 "EOD 트리거(PR3 예정)" 를 언급했으나 실제 PR3 에서는 force_close cron 비활성화 + Executor 분봉 stop_loss 가드만 처리됐고 EOD 일봉 wiring 은 미구현 상태였다. 본 ADR 이 그 결손을 PR5 로 확정한다.

### 일봉 데이터 소스

`HistoricalDataStore.fetch_daily_ohlcv(symbol, start, end)` 가 `data/stock_agent.db` SQLite 캐시 + pykrx 폴백을 통해 일봉을 제공한다. ADR-0023 C1 검증에서 universe 199 종목 × 2024-04-01~2026-04-21 일봉 백필이 완료됐다. 운영에서는 매일 오늘 자 일봉이 캐시에 없으면 pykrx 네트워크 호출이 발생한다.

`data/daily_bar_loader.py` 의 `DailyBarLoader` 가 `DailyBarSource.fetch_daily_ohlcv` 결과를 09:00 KST `MinuteBar` 로 래핑하는 패턴을 이미 백테스트 진영에서 사용 중. 본 ADR 의 EOD cron 도 동일한 래핑 정책을 사용한다.

### 시그널 buffer 가 필요한 이유

EOD 시점(예: 15:35) 에 시그널을 받아도 paper API 는 장 마감 후 시장가 주문을 즉시 체결시키지 않는다. 다음 영업일 09:00~ 시점 시초가 주문이 정상 경로다. 따라서:

1. EOD cron 이 시그널을 메모리 buffer 에 enqueue.
2. 다음 영업일 09:00 후 `on_step` 첫 발화 시 buffer 를 flush 해 `Executor._process_signals` 에 직접 주입.
3. 처리 후 buffer clear.

## 결정

Phase 3 운영의 RSI MR 트리거 모델을 다음과 같이 확정한다.

### 1. 새 cron `on_eod_signal` (15:35 KST)

`main.py` `_install_jobs` 가 RSI MR 모드일 때 신규 cron 1건 추가:

```python
scheduler.add_job(
    _on_eod_signal(runtime, effective_clock),
    CronTrigger(day_of_week="mon-fri", hour=15, minute=35, timezone=KST),
    name="on_eod_signal",
)
```

콜백 동작:

1. `today = effective_clock().date()` (KST aware datetime → date).
2. universe 199 종목 각각에 대해 `historical_store.fetch_daily_ohlcv(symbol, today, today)` 호출. 빈 결과(휴장·미거래)는 skip + `logger.debug`.
3. 각 `DailyBar` 를 `MinuteBar(bar_time=datetime.combine(today, time(9, 0), tzinfo=KST))` 로 래핑 (`DailyBarLoader` 와 동일 정책).
4. 199회 `strategy.on_bar(wrapped_bar)` 순차 호출 → signals 수집.
5. `executor.enqueue_pending_signals(signals)`.
6. 각 단계 실패는 `RuntimeError` 전파 (Executor 백오프 정책과 동일 기조). `KisClientError` 는 발생하지 않음(pykrx 경로).

15:35 선택 근거: 한국 주식 정규장 마감 15:30 + pykrx 일봉 캐시·KRX 발표 latency 5분 여유. 운영 1~2주 후 latency 실측해 시각 조정 가능 (조건부 ADR-0028 후보).

### 2. `Executor.enqueue_pending_signals` + `step` 처리

`Executor` 신규 인스턴스 필드:

```python
self._pending_signals: list[Signal] = []
```

신규 공개 메서드:

```python
def enqueue_pending_signals(self, signals: Sequence[Signal]) -> None:
    """EOD cron 이 다음 영업일 시초 진입을 위한 시그널을 큐인."""
    for sig in signals:
        if not isinstance(sig, EntrySignal | ExitSignal):
            raise RuntimeError(...)
    self._pending_signals.extend(signals)
```

`step(now)` 흐름 변경 — `_sweep_*_events` 초기화 직후, reconcile 직전 위치에 buffer flush:

```text
1. 가드 (naive datetime, 세션 미시작).
2. _sweep_entry_events / _sweep_exit_events 초기화.
3. pending_signals 처리 — buffer 가 비어있지 않으면 _process_signals(buffer, now) 호출 후 buffer clear. orders_submitted 누적.
4. reconcile() — 잔고 vs RiskManager.
5. 분봉 루프 (per symbol).
6. on_time signals.
7. StepReport 반환.
```

`force_close_all(now)` 는 buffer flush 안 함 — 강제청산은 일중 단발성이고 buffer 의 EOD 시그널과 무관.

`start_session` / `restore_session` 은 buffer 를 clear 하지 않는다 — EOD 시점에 큐인된 시그널이 다음 영업일 09:00 session_start 후 첫 step 에서 자연 처리되어야 하기 때문. 운영 재기동 시 EOD 가 다시 발화되면 같은 시그널이 재큐인될 수 있으므로 운영자가 인지해야 한다.

### 3. Strategy ↔ Executor 상태 동기화 가정

`RSIMRStrategy.on_bar` 가 EntrySignal 을 emit 하면 즉시 `self._holdings[symbol] = ...` 으로 strategy 내부 상태를 갱신한다 (체결 확정 가정). 다음 영업일 시초 paper 주문이 정상 체결되면 RiskManager 와 정합. 시초 주문이 거부되면 다음 reconcile 에서 mismatch 감지 → `_halt = True` → 운영자 개입.

EOD 시점 ExitSignal 도 동일 — strategy 내부에서 즉시 `del self._holdings[symbol]` + `_last_exit_date[symbol] = today`. 다음 영업일 시초 청산 주문이 정상 체결되면 RiskManager 정합. 부분/0 체결은 PR3 정책 그대로 `ExecutorError` 승격.

### 4. 영속화 미포함

`_pending_signals` buffer 는 메모리 전용. 운영 재기동 시 buffer 손실. 재기동 후 EOD cron 이 다시 발화되면 같은 시그널이 재구성되므로 자연 복원. 단:

- 재기동이 EOD 와 다음 영업일 09:00 사이에 일어나면 buffer 손실 → 그 영업일 시초 진입 누락. 다음 EOD 가 새 시그널 산출.
- buffer 영속화는 `storage/db.py` SQLite append-only 테이블로 후속 PR 가능. 운영 1~2주 후 손실 빈도 실측 후 조건부 ADR-0028.

### 5. universe 일관성

EOD cron 이 사용하는 universe 는 `main.py` 가 시작 시 1회 로드한 `runtime.universe.tickers` 와 동일. 운영자가 운영 중 universe.yaml 수정해도 재기동 전에는 반영되지 않음 — Phase 3 명문화된 운영 정책(ADR-0011) 그대로.

## 결과

### 후속 PR 액션

**PR5 (본 PR, 2026-05-04 진행 중)**

코드 변경:
- `src/stock_agent/execution/executor.py` — `Executor._pending_signals` 인스턴스 필드 추가, `enqueue_pending_signals(signals)` 신규 공개 메서드, `step()` 시작 부분 buffer flush 호출.
- `src/stock_agent/main.py` — `_on_eod_signal(runtime, clock)` 콜백 신규 함수, `_install_jobs` 가 RSI MR 모드일 때 `on_eod_signal` cron 등록.
- `src/stock_agent/data/daily_bar_loader.py` 의 09:00 KST MinuteBar 래핑 정책 재사용 (helper 함수 노출 또는 인라인 wrap — 구현 세부).

테스트:
- `tests/test_executor.py` — `enqueue_pending_signals` 계약, `step` 의 buffer flush 우선 처리, EntrySignal/ExitSignal 혼합 buffer 처리, start_session/restore_session 후에도 buffer 보존.
- `tests/test_main.py` — `_install_jobs` 가 RSI MR 모드에서 `on_eod_signal` cron 등록, 콜백이 universe × `fetch_daily_ohlcv` × `strategy.on_bar` 를 호출하고 `enqueue_pending_signals` 로 위임.

문서:
- 본 ADR + root CLAUDE.md / README.md / plan.md / execution·data 모듈 CLAUDE.md 동기화.

### 후속 ADR 후보

10영업일 모의투자 후 회고 시 다음 항목을 평가:

- EOD 15:35 시각이 pykrx 일봉 latency 와 정합한지. 캐시 미스 빈도 측정.
- buffer 영속화 필요성. 재기동 사이 buffer 손실 사례 발생 여부.
- universe 갱신 정책. 운영 중 universe.yaml 변경 → 즉시 반영 vs 다음 재기동까지 대기.
- ADR-0026 ("10영업일 회고 후 한도 갱신") 와 함께 ADR-0028 로 묶어 처리.

### 리스크 고지

본 ADR 의 EOD 트리거 모델은 모의투자(paper) 검증 단계의 운영 wiring 이다. 시그널 buffer 메모리 전용 정책은 단기 결정 — 영속화 부재로 재기동 사이 손실 가능. Phase 3 PASS 선언 (10영업일 무중단 + 0 unhandled error + 모든 주문 SQLite 기록 + 텔레그램 알림 100% 수신) 까지 운영자 능동 모니터링 필수. "수익 보장" 표현 금지 — root CLAUDE.md 리스크 고지 원칙 그대로.

## 추적

- 코드 (PR5 진행 중, 2026-05-04): `src/stock_agent/execution/executor.py` — `enqueue_pending_signals` + step buffer flush. `src/stock_agent/main.py` — `_on_eod_signal` cron + `_install_jobs` 등록.
- 관련 ADR: [ADR-0023](./0023-rsi-mr-strategy-adoption-conditional.md), [ADR-0025](./0025-rsi-mr-operational-risk-limits.md).
- 도입 PR: PR5 (예정).
- 후속 ADR: ADR-0028 (10영업일 회고 후 EOD 시각·buffer 영속화·universe 갱신 정책 갱신, 조건부).
