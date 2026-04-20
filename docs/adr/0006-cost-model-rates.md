---
date: 2026-04-20
status: 승인됨
deciders: donggyu
related: [ADR-0001]
---

# ADR-0006: 백테스트 비용 모델 수치 (슬리피지 0.1% / 수수료 0.015% / 거래세 0.18%)

## 상태

승인됨 — 2026-04-20. KIS 한투 비대면 수수료 체계와 KRX 2026-04 거래세 기준.

## 맥락

Phase 2 백테스트 엔진(`BacktestEngine`) 도입 시점에 한국 시장의 비용 구조를 시뮬레이션에 어떻게 반영할지 결정이 필요했다.

- **수수료**: 한투 비대면(MTS/모의) 기본 0.015% (대량 약정 우대 미적용 보수적 가정).
- **거래세**: 2026-04 기준 KRX 매도 0.18% (코스피 0.05% + 농어촌특별세 0.15% = 0.20% → 단계적 인하로 0.18% 적용). 매수는 0%.
- **슬리피지**: 시장가 주문 가정 시 호가 spread 와 체결 지연을 합쳐 0.1% 불리 (보수적, 소액 주문 KOSPI 200 대형주 기준).

세 비용을 하나의 합산 비율로 두면 매수/매도 비대칭(거래세는 매도만, 수수료는 양쪽)을 표현할 수 없어 PnL 계산 정확도가 떨어진다.

## 결정

**세 비용을 분리해 저장·계산** 한다. `BacktestConfig` 의 기본값:

| 필드 | 기본값 | 적용 시점 |
|---|---|---|
| `slippage_rate` | `Decimal("0.001")` | 매수·매도 모두 (불리 방향) |
| `commission_rate` | `Decimal("0.00015")` | 매수·매도 대칭 |
| `sell_tax_rate` | `Decimal("0.0018")` | **매도만** |

순수 함수(`costs.py`)로 분리해 엔진은 호출만 한다. floor 처리(`int(notional * rate)`) 는 KRW 단위 정수화 시 1회만, cash 갱신 직전.

`Decimal` 사용 — float 누적 오차 차단.

## 결과

**긍정**
- 매수/매도 비대칭 비용 자연스러운 표현. PnL 계산이 실전과 동일 구조.
- 비용 변경(예: 거래세 추가 인하) 시 `BacktestConfig` 기본값만 갱신 + ADR 1건 추가로 추적 가능.
- `Decimal` 정밀도로 누적 오차 0.

**부정**
- 슬리피지 0.1% 는 보수적 추정 — 실측이 아니라 운영자 직관. Phase 3 모의투자 후 실측 데이터로 보정 필요.
- KIS 한투 우대 수수료(약정 규모별 할인) 는 반영 안 함 — 소액 자본(100~200만원) 가정에서는 무시 가능 수준.

**중립**
- 매수 거래세는 0 이라 `costs.py` 에 매수 거래세 함수 자체를 두지 않음.
- 호가 단위 라운딩은 비용 모델과 분리 — Phase 3 executor 에서 처리.

## 추적

- 코드: `src/stock_agent/backtest/costs.py`, `src/stock_agent/backtest/engine.py` (`BacktestConfig`)
- 문서: [src/stock_agent/backtest/CLAUDE.md](../../src/stock_agent/backtest/CLAUDE.md) "engine.py" 섹션, [docs/architecture.md](../architecture.md) "한국 시장 비용 모델" 섹션
- 도입 PR: #10 (Phase 2 세 번째 산출물)
- 검토 트리거: KRX 거래세율 변경, KIS 한투 수수료 체계 변경, Phase 3 슬리피지 실측 결과
