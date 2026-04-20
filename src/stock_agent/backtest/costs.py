"""백테스트 비용 계산 — 슬리피지·수수료·거래세 순수 함수.

책임 범위
- 참고가에 슬리피지를 적용한 실체결가 계산 (매수 +, 매도 -).
- 명목금액에 수수료율을 적용한 정수 KRW 수수료 (매수/매도 대칭).
- 매도 명목금액에 거래세율을 적용한 정수 KRW 거래세 (매수는 0).

설계 원칙
- 순수 함수 모듈. 외부 I/O 없음, 상태 없음.
- 입력 금액 연산은 `Decimal` 로 유지, 출력 KRW 정수화는 호출자 시점에서 한 번
  `int(Decimal)` floor 로 절삭. 수수료·세금은 증권사 관행에 맞춰 floor.
- 음수·0 방어는 최소한(`RuntimeError`) — 상위(`BacktestEngine`) 에서 사전에
  양수·유효 비율을 강제하므로 이곳은 계약 준수 가정. 다만 `rate`·`reference`
  가 음수면 버그이므로 즉시 실패시킨다.

한국 시장 맥락
- 거래세는 매도에만 부과 (KRX 기준 0.18% — 2026-04 현재, plan.md Phase 2).
- 수수료는 증권사별 매수·매도 대칭 (한투 비대면 기준 약 0.015~0.025%).
- 슬리피지는 시장가 주문의 불리 방향 (매수는 호가보다 높게·매도는 낮게).
"""

from __future__ import annotations

from decimal import Decimal


def buy_fill_price(reference: Decimal, slippage_rate: Decimal) -> Decimal:
    """매수 체결가 = 참고가 × (1 + slippage_rate).

    Raises:
        RuntimeError: `reference` 또는 `slippage_rate` 가 음수.
    """
    _require_non_negative(reference, "reference")
    _require_non_negative(slippage_rate, "slippage_rate")
    return reference * (Decimal("1") + slippage_rate)


def sell_fill_price(reference: Decimal, slippage_rate: Decimal) -> Decimal:
    """매도 체결가 = 참고가 × (1 - slippage_rate).

    Raises:
        RuntimeError: `reference` 또는 `slippage_rate` 가 음수,
            또는 `slippage_rate >= 1` (체결가가 0 이하가 되는 경우).
    """
    _require_non_negative(reference, "reference")
    _require_non_negative(slippage_rate, "slippage_rate")
    if slippage_rate >= 1:
        raise RuntimeError(f"slippage_rate 는 1 미만이어야 합니다 (got={slippage_rate})")
    return reference * (Decimal("1") - slippage_rate)


def buy_commission(notional: Decimal, rate: Decimal) -> int:
    """매수 수수료 (KRW, floor). `notional` 은 체결가 × 수량."""
    return _commission(notional, rate)


def sell_commission(notional: Decimal, rate: Decimal) -> int:
    """매도 수수료 (KRW, floor). `notional` 은 체결가 × 수량."""
    return _commission(notional, rate)


def sell_tax(notional: Decimal, rate: Decimal) -> int:
    """매도 거래세 (KRW, floor). `notional` 은 체결가 × 수량."""
    _require_non_negative(notional, "notional")
    _require_non_negative(rate, "rate")
    return int(notional * rate)


def _commission(notional: Decimal, rate: Decimal) -> int:
    _require_non_negative(notional, "notional")
    _require_non_negative(rate, "rate")
    return int(notional * rate)


def _require_non_negative(value: Decimal, name: str) -> None:
    if value < 0:
        raise RuntimeError(f"{name} 는 0 이상이어야 합니다 (got={value})")
