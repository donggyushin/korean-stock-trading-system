---
date: 2026-04-20
status: 승인됨
deciders: donggyu
related: [ADR-0001]
---

# ADR-0007: phantom_long 처리 — RiskManager 거부 시 strategy 가짜 LONG 흡수

## 상태

승인됨 — 2026-04-20.

## 맥락

`ORBStrategy` 와 `RiskManager` 는 의도적으로 분리됐다 — strategy 는 시그널 생성, risk 는 게이팅. 그런데 `ORBStrategy._enter_long` 은 `EntrySignal` 을 반환하기 **전에** 자체 상태(`position_state="long"`) 로 전이시킨다. 이유: 상태 머신이 단순해지고, 동일 bar 에서 진입 시그널이 중복 생성되는 것을 막을 수 있다.

문제: `BacktestEngine` 이 `EntrySignal` 을 받아 `RiskManager.evaluate_entry` 를 호출했을 때 거부되면, strategy 는 여전히 `position_state="long"` 으로 안다. 다음 bar 에서 손절·익절 조건이 성립하면 `ExitSignal` 을 만든다. 이 `ExitSignal` 을 그대로 처리하면 다음 두 가지 문제가 발생한다.

1. RiskManager 는 진입을 모르므로 `record_exit` 호출 시 미보유 심볼 청산 → `RiskManagerError` (상태 무결성 위반).
2. 엔진이 `_active_lots[symbol]` 을 보유하지 않으므로 `RuntimeError` (entry 없이 exit 불가).

대안 검토:
- "strategy 가 RiskManager 결과를 알게" — 결합도 폭증, strategy 가 risk 모듈 import 필요.
- "EntrySignal 반환 후 상태 전이" — 동일 bar 다중 시그널 위험, 상태 머신 복잡.
- "RiskManager 거부 시 strategy 에 rollback 통지" — Strategy Protocol 확장, 모든 구현체에 책임 전가.

## 결정

**`BacktestEngine` 이 `phantom_longs: set[str]` 을 유지** 한다.

- RiskManager 거부 → `phantom_longs.add(symbol)` (디버그 로그)
- 후속 ExitSignal 의 `symbol in phantom_longs` 면 silent 흡수 (debug 로그, `record_exit` 호출 안 함, TradeRecord 누적 안 함)
- 다음 세션 경계에서 `phantom_longs` 자동 클리어

엔진 내부 디테일이라 strategy/risk 인터페이스 영향 없음.

## 결과

**긍정**
- strategy 와 risk 의 결합 0 유지.
- 거부 카운트(`rejected_counts`) 는 정확히 집계됨 — RiskManager 의 `entries_today` 도 미증가.
- Phase 3 실시간 executor 에서도 동일 패턴 재사용 가능.

**부정**
- 엔진이 strategy 의 내부 상태 전이를 "알고 있어야" 함 — 구현 디테일 누설.
- 백테스트 결과 해석 시 "phantom 거부" 는 `rejected_counts[reason]` 으로만 보임, ExitSignal 흡수는 디버그 로그에만 남음.

**중립**
- 사후 슬리피지 거부(엔진의 cash 부족 감지) 도 동일 메커니즘으로 처리 — `post_slippage_rejections` 카운터 별도 분리.

## 추적

- 코드: `src/stock_agent/backtest/engine.py` (`phantom_longs`, `_handle_entry_signal`, `_handle_exit_signal`)
- 문서: [src/stock_agent/backtest/CLAUDE.md](../../src/stock_agent/backtest/CLAUDE.md) "진입 처리 흐름", [docs/architecture.md](../architecture.md) "5.2 phantom_long 흐름" 시퀀스 다이어그램
- 도입 PR: #10 (Phase 2 세 번째 산출물)
