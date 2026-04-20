---
date: 2026-04-20
status: 승인됨
deciders: donggyu
related: [ADR-0006, ADR-0007]
---

# ADR-0001: `backtesting.py` 라이브러리 폐기, 자체 시뮬레이션 루프 채택

## 상태

승인됨 — 2026-04-20.

`plan.md` 초기 결정(`backtesting.py` 사용)을 본 ADR 로 번복.

## 맥락

Phase 2 백테스트 엔진 착수 시점에 `backtesting.py` 의 두 가지 본질적 한계가 드러났다.

1. **단일자산 전용 설계** — `Strategy` 클래스가 단일 가격 시계열을 가정한다. 우리 시스템은 다중종목(KOSPI 200 유니버스 일부) 동시 보유가 핵심이다.
2. **RiskManager 게이팅 표현 불가** — 동시 3종목 한도, 일일 손실 -2% 서킷브레이커, 일일 진입 횟수 한도 같은 게이팅을 라이브러리 추상화 안에서 깔끔히 표현할 수 없다.
3. **AGPL 라이센스** — 본 프로젝트는 비공개 운영이지만 향후 공개·배포 가능성을 차단할 부담이 있다.

대안으로 검토한 라이브러리(`vectorbt`, `zipline-reloaded`)도 다중종목 + 커스텀 게이팅 조합에서 비슷한 마찰이 있었다.

## 결정

**자체 시뮬레이션 루프**를 `src/stock_agent/backtest/engine.py` 에 직접 구현한다. `ORBStrategy.on_bar`/`on_time` 과 `RiskManager.evaluate_entry`/`record_entry`/`record_exit` 를 그대로 호출하는 얇은 오케스트레이터로 설계한다.

## 결과

**긍정**
- 실전 코드(Phase 3 executor)와 백테스트 코드가 동일 인터페이스 공유 — 시뮬레이션-실전 괴리 최소화.
- 매수/매도 비대칭 거래세, 다중종목 보유 한도, 서킷브레이커 자연스럽게 표현.
- 외부 의존성 추가 0건. AGPL 부담 제거.

**부정**
- HTML/노트북 시각화·지표 차트 같은 부가 기능을 직접 만들거나 별도 도구로 위임해야 함 (현재는 Markdown/CSV 출력만).
- 라이브러리가 검증한 메트릭 계산 코드를 우리가 직접 작성·테스트해야 함 (`metrics.py` — 샤프, MDD, 승률 등).

**중립**
- walk-forward 검증·파라미터 민감도는 별도 모듈(`sensitivity.py`) 로 직접 구현.

## 추적

- 코드: `src/stock_agent/backtest/engine.py`, `src/stock_agent/backtest/costs.py`, `src/stock_agent/backtest/metrics.py`
- 문서: [src/stock_agent/backtest/CLAUDE.md](../../src/stock_agent/backtest/CLAUDE.md) "핵심 결정" 섹션
- 도입 PR: #10 (Phase 2 세 번째 산출물)
